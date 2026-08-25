/*
 * menai_vm_c.c — C implementation of the Menai VM execute loop.
 *
 * Exposes:
 *   menai_vm_c.execute(code, globals_dict) -> MenaiValue *   (in menai_vm_bridge.c)
 *   menai_vm_c.cancel() -> None   (request cancellation of the running execute)
 *
 * The execute entry point and all Python-boundary logic live in
 * menai_vm_bridge.c.  This file contains the native execute loop
 * and the cancel method.  Globals table management is in menai_vm_globals.c.
 */
#define _POSIX_C_SOURCE 200809L
#include <math.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "menai_vm_c.h"
#include "menai_vm_atomic.h"

/*
 * Portable complex arithmetic — avoids <complex.h>, which is unsupported on MSVC.
 *
 * All complex math is expressed in terms of <math.h> functions (exp, log, sin,
 * cos, tan, sqrt, atan2, hypot), which are available on every target platform.
 */
typedef struct {
    double re;
    double im;
} mc_t;

static inline mc_t
mc(double re, double im)
{
    mc_t z = {re, im};
    return z;
}

static inline int
mc_zero(mc_t z)
{
    return z.re == 0.0 && z.im == 0.0;
}

static inline mc_t
mc_mul(mc_t a, mc_t b)
{
    return mc(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re);
}

static inline mc_t
mc_div(mc_t a, mc_t b)
{
    double d = b.re * b.re + b.im * b.im;
    return mc((a.re * b.re + a.im * b.im) / d, (a.im * b.re - a.re * b.im) / d);
}

static inline mc_t
mc_exp(mc_t z)
{
    double e = exp(z.re);
    return mc(e * cos(z.im), e * sin(z.im));
}

static inline mc_t
mc_log(mc_t z)
{
    return mc(log(hypot(z.re, z.im)), atan2(z.im, z.re));
}

static inline mc_t
mc_pow(mc_t a, mc_t b)
{
    return mc_zero(a) ? mc(0.0, 0.0) : mc_exp(mc_mul(b, mc_log(a)));
}

static inline mc_t
mc_sqrt(mc_t z)
{
    double r = hypot(z.re, z.im);
    double s = sqrt((r + z.re) / 2.0);
    double t = (z.im >= 0.0 ? 1.0 : -1.0) * sqrt((r - z.re) / 2.0);
    return mc(s, t);
}

static inline mc_t
mc_sin(mc_t z)
{
    return mc(sin(z.re) * cosh(z.im), cos(z.re) * sinh(z.im));
}

static inline mc_t
mc_cos(mc_t z)
{
    return mc(cos(z.re) * cosh(z.im), -sin(z.re) * sinh(z.im));
}

static inline mc_t
mc_tan(mc_t z)
{
    return mc_div(mc_sin(z), mc_cos(z));
}

static inline mc_t
mc_log10(mc_t z)
{
    mc_t l = mc_log(z);
    double s = 1.0 / log(10.0);
    return mc(l.re * s, l.im * s);
}

static inline mc_t
mc_logn(mc_t a, mc_t b)
{
    return mc_div(mc_log(a), mc_log(b)); 
}

/*
 * Portable overflow-detecting arithmetic for the small-integer fast paths.
 *
 * _menai_add_overflow(a, b, &result) returns 1 if a+b overflows long, 0 otherwise.
 * _menai_sub_overflow and _menai_mul_overflow follow the same convention.
 */
#if defined(__GNUC__) || defined(__clang__)
#define _menai_add_overflow(a, b, rp) __builtin_add_overflow((a), (b), (rp))
#define _menai_sub_overflow(a, b, rp) __builtin_sub_overflow((a), (b), (rp))
#define _menai_mul_overflow(a, b, rp) __builtin_mul_overflow((a), (b), (rp))
#else
static inline int
_menai_add_overflow(long a, long b, long *r) {
    unsigned long ua = (unsigned long)a, ub = (unsigned long)b;
    unsigned long ur = ua + ub;
    *r = (long)ur;
    return (a > 0 && b > 0 && *r < 0) || (a < 0 && b < 0 && *r > 0);
}

static inline int
_menai_sub_overflow(long a, long b, long *r) {
    unsigned long ua = (unsigned long)a, ub = (unsigned long)b;
    unsigned long ur = ua - ub;
    *r = (long)ur;
    return (b < 0 && a > 0 && *r < 0) || (b > 0 && a < 0 && *r > 0);
}

static inline int
_menai_mul_overflow(long a, long b, long *r) {
    /* Conservative: use double to detect overflow. */
    double d = (double)a * (double)b;
    *r = (long)((unsigned long)a * (unsigned long)b);
    return d > (double)LONG_MAX || d < (double)LONG_MIN;
}

#endif

/*
 * Limits
 */
#define MAX_FRAME_DEPTH 1024

/*
 * Cancellation check interval.
 */
#define CANCEL_CHECK_INTERVAL (1 << 20)

/*
 * Instruction encoding constants — must match menai_bytecode.py
 */
#define OPCODE_SHIFT 48
#define DEST_SHIFT 36
#define SRC0_SHIFT 24
#define SRC1_SHIFT 12
#define FIELD_MASK 0xFFFu
#define OPCODE_MASK 0xFFFFu

/*
 * Opcode values — must match menai_bytecode.py Opcode enum
 */
#define OP_LOAD_NONE 0
#define OP_LOAD_TRUE 1
#define OP_LOAD_FALSE 2
#define OP_LOAD_EMPTY_LIST 3
#define OP_LOAD_EMPTY_DICT 4
#define OP_LOAD_EMPTY_SET 5
#define OP_LOAD_CONST 6
#define OP_LOAD_NAME 7
#define OP_MOVE 8
#define OP_JUMP 20
#define OP_JUMP_IF_FALSE 21
#define OP_JUMP_IF_TRUE 22
#define OP_RAISE_ERROR 23
#define OP_MAKE_CLOSURE 30
#define OP_PATCH_CLOSURE 31
#define OP_CALL 32
#define OP_TAIL_CALL 33
#define OP_APPLY 34
#define OP_TAIL_APPLY 35
#define OP_RETURN 37
#define OP_NONE_P 50
#define OP_FUNCTION_P 60
#define OP_FUNCTION_EQ_P 61
#define OP_FUNCTION_NEQ_P 62
#define OP_FUNCTION_MIN_ARITY 63
#define OP_FUNCTION_VARIADIC_P 64
#define OP_FUNCTION_ACCEPTS_P 65
#define OP_SYMBOL_P 80
#define OP_SYMBOL_EQ_P 81
#define OP_SYMBOL_NEQ_P 82
#define OP_SYMBOL_TO_STRING 83
#define OP_BOOLEAN_P 100
#define OP_BOOLEAN_EQ_P 101
#define OP_BOOLEAN_NEQ_P 102
#define OP_BOOLEAN_NOT 103
#define OP_INTEGER_P 120
#define OP_INTEGER_EQ_P 121
#define OP_INTEGER_NEQ_P 122
#define OP_INTEGER_LT_P 123
#define OP_INTEGER_GT_P 124
#define OP_INTEGER_LTE_P 125
#define OP_INTEGER_GTE_P 126
#define OP_INTEGER_ABS 127
#define OP_INTEGER_ADD 128
#define OP_INTEGER_SUB 129
#define OP_INTEGER_MUL 130
#define OP_INTEGER_DIV 131
#define OP_INTEGER_MOD 132
#define OP_INTEGER_NEG 133
#define OP_INTEGER_EXPN 134
#define OP_INTEGER_BIT_NOT 135
#define OP_INTEGER_BIT_SHIFT_LEFT 136
#define OP_INTEGER_BIT_SHIFT_RIGHT 137
#define OP_INTEGER_BIT_OR 138
#define OP_INTEGER_BIT_AND 139
#define OP_INTEGER_BIT_XOR 140
#define OP_INTEGER_MIN 141
#define OP_INTEGER_MAX 142
#define OP_INTEGER_TO_FLOAT 143
#define OP_INTEGER_TO_COMPLEX 144
#define OP_INTEGER_TO_STRING 145
#define OP_INTEGER_CODEPOINT_TO_STRING 146
#define OP_FLOAT_P 160
#define OP_FLOAT_EQ_P 161
#define OP_FLOAT_NEQ_P 162
#define OP_FLOAT_LT_P 163
#define OP_FLOAT_GT_P 164
#define OP_FLOAT_LTE_P 165
#define OP_FLOAT_GTE_P 166
#define OP_FLOAT_NEG 167
#define OP_FLOAT_ADD 168
#define OP_FLOAT_SUB 169
#define OP_FLOAT_MUL 170
#define OP_FLOAT_DIV 171
#define OP_FLOAT_FLOOR_DIV 172
#define OP_FLOAT_MOD 173
#define OP_FLOAT_EXP 174
#define OP_FLOAT_EXPN 175
#define OP_FLOAT_LOG 176
#define OP_FLOAT_LOG10 177
#define OP_FLOAT_LOG2 178
#define OP_FLOAT_LOGN 179
#define OP_FLOAT_SIN 180
#define OP_FLOAT_COS 181
#define OP_FLOAT_TAN 182
#define OP_FLOAT_SQRT 183
#define OP_FLOAT_ABS 184
#define OP_FLOAT_TO_INTEGER 185
#define OP_FLOAT_TO_COMPLEX 186
#define OP_FLOAT_TO_STRING 187
#define OP_FLOAT_FLOOR 188
#define OP_FLOAT_CEIL 189
#define OP_FLOAT_ROUND 190
#define OP_FLOAT_MIN 191
#define OP_FLOAT_MAX 192
#define OP_FLOAT_ATAN2 193
#define OP_FLOAT_COSH 194
#define OP_FLOAT_SINH 195
#define OP_FLOAT_TANH 196
#define OP_FLOAT_ASIN 197
#define OP_FLOAT_ACOS 198
#define OP_FLOAT_ATAN 199
#define OP_FLOAT_HYPOT 221
#define OP_FLOAT_EXP2 222
#define OP_FLOAT_CBRT 223
#define OP_FLOAT_EXPM1 224
#define OP_FLOAT_LOG1P 225
#define OP_FLOAT_TRUNC 226
#define OP_FLOAT_COPYSIGN 227
#define OP_COMPLEX_P 200
#define OP_COMPLEX_EQ_P 201
#define OP_COMPLEX_NEQ_P 202
#define OP_COMPLEX_REAL 203
#define OP_COMPLEX_IMAG 204
#define OP_COMPLEX_ABS 205
#define OP_COMPLEX_ADD 206
#define OP_COMPLEX_SUB 207
#define OP_COMPLEX_MUL 208
#define OP_COMPLEX_DIV 209
#define OP_COMPLEX_NEG 210
#define OP_COMPLEX_EXP 211
#define OP_COMPLEX_EXPN 212
#define OP_COMPLEX_LOG 213
#define OP_COMPLEX_LOG10 214
#define OP_COMPLEX_LOGN 215
#define OP_COMPLEX_SIN 216
#define OP_COMPLEX_COS 217
#define OP_COMPLEX_TAN 218
#define OP_COMPLEX_SQRT 219
#define OP_COMPLEX_TO_STRING 220
#define OP_STRING_P 240
#define OP_STRING_EQ_P 241
#define OP_STRING_NEQ_P 242
#define OP_STRING_LT_P 243
#define OP_STRING_GT_P 244
#define OP_STRING_LTE_P 245
#define OP_STRING_GTE_P 246
#define OP_STRING_LENGTH 247
#define OP_STRING_UPCASE 248
#define OP_STRING_DOWNCASE 249
#define OP_STRING_TRIM 250
#define OP_STRING_TRIM_LEFT 251
#define OP_STRING_TRIM_RIGHT 252
#define OP_STRING_TO_INTEGER 253
#define OP_STRING_TO_FLOAT 254
#define OP_STRING_TO_LIST 255
#define OP_STRING_REF 256
#define OP_STRING_PREFIX_P 257
#define OP_STRING_SUFFIX_P 258
#define OP_STRING_CONCAT 259
#define OP_STRING_SLICE 260
#define OP_STRING_REPLACE 261
#define OP_STRING_INDEX 262
#define OP_STRING_TO_INTEGER_CODEPOINT 263
#define OP_STRING_TO_COMPLEX 264
#define OP_MAKE_DICT 280
#define OP_DICT_P 281
#define OP_DICT_EQ_P 282
#define OP_DICT_NEQ_P 283
#define OP_DICT_KEYS 284
#define OP_DICT_VALUES 285
#define OP_DICT_LENGTH 286
#define OP_DICT_HAS_P 287
#define OP_DICT_REMOVE 288
#define OP_DICT_MERGE 289
#define OP_DICT_SET 290
#define OP_DICT_GET 291
#define OP_MAKE_LIST 300
#define OP_LIST_P 301
#define OP_LIST_EQ_P 302
#define OP_LIST_NEQ_P 303
#define OP_LIST_PREPEND 304
#define OP_LIST_APPEND 305
#define OP_LIST_REVERSE 306
#define OP_LIST_FIRST 307
#define OP_LIST_REST 308
#define OP_LIST_LAST 309
#define OP_LIST_LENGTH 310
#define OP_LIST_REF 311
#define OP_LIST_NULL_P 312
#define OP_LIST_MEMBER_P 313
#define OP_LIST_INDEX 314
#define OP_LIST_SLICE 315
#define OP_LIST_REMOVE 316
#define OP_LIST_CONCAT 317
#define OP_LIST_TO_STRING 318
#define OP_LIST_TO_SET 319
#define OP_MAKE_SET 340
#define OP_SET_P 341
#define OP_SET_EQ_P 342
#define OP_SET_NEQ_P 343
#define OP_SET_MEMBER_P 344
#define OP_SET_ADD 345
#define OP_SET_REMOVE 346
#define OP_SET_LENGTH 347
#define OP_SET_UNION 348
#define OP_SET_INTERSECTION 349
#define OP_SET_DIFFERENCE 350
#define OP_SET_SUBSET_P 351
#define OP_SET_TO_LIST 352
#define OP_MAKE_STRUCT 360
#define OP_STRUCT_P 361
#define OP_STRUCT_IS_INSTANCE_P 362
#define OP_STRUCT_GET 363
#define OP_STRUCT_REF 364
#define OP_STRUCT_SET 365
#define OP_STRUCT_SET_REF 366
#define OP_STRUCT_EQ_P 367
#define OP_STRUCT_NEQ_P 368
#define OP_STRUCT_TYPE 369
#define OP_STRUCTTYPE_P 372
#define OP_STRUCTTYPE_EQ_P 373
#define OP_STRUCTTYPE_NEQ_P 374
#define OP_STRUCTTYPE_NAME 370
#define OP_STRUCTTYPE_FIELDS 371
#define OP_RANGE 380
#define OP_BYTES_P 400
#define OP_BYTES_EQ_P 401
#define OP_BYTES_NEQ_P 402
#define OP_BYTES_LENGTH 403
#define OP_BYTES_REF 410
#define OP_BYTES_APPEND_U8 411
#define OP_LIST_TO_BYTES 415
#define OP_BYTES_SLICE 420
#define OP_STRING_TO_BYTES 425
#define OP_BYTES_TO_STRING 426
#define OP_BYTES_TO_LIST 427
#define OP_BYTES_TO_STRING_HEX 428
#define OP_STRING_HEX_TO_BYTES 429
#define OP_BYTES_CONCAT 435
#define OP_BYTES_INDEX 440
#define OP_BYTES_INDEX_INT 441
#define OP_BYTES_LT_P 445
#define OP_BYTES_GT_P 446
#define OP_BYTES_LTE_P 447
#define OP_BYTES_GTE_P 448
#define OP_BYTES_READ_U8 460
#define OP_BYTES_READ_U16_LE 461
#define OP_BYTES_READ_U24_LE 462
#define OP_BYTES_READ_U32_LE 463
#define OP_BYTES_READ_U64_LE 464
#define OP_BYTES_READ_U16_BE 465
#define OP_BYTES_READ_U24_BE 466
#define OP_BYTES_READ_U32_BE 467
#define OP_BYTES_READ_U64_BE 468
#define OP_BYTES_READ_I8 469
#define OP_BYTES_READ_I16_LE 470
#define OP_BYTES_READ_I24_LE 471
#define OP_BYTES_READ_I32_LE 472
#define OP_BYTES_READ_I64_LE 473
#define OP_BYTES_READ_I16_BE 474
#define OP_BYTES_READ_I24_BE 475
#define OP_BYTES_READ_I32_BE 476
#define OP_BYTES_READ_I64_BE 477
#define OP_BYTES_APPEND_U16_LE 481
#define OP_BYTES_APPEND_U16_BE 482
#define OP_BYTES_APPEND_U24_LE 483
#define OP_BYTES_APPEND_U24_BE 484
#define OP_BYTES_APPEND_U32_LE 485
#define OP_BYTES_APPEND_U32_BE 486
#define OP_BYTES_APPEND_U64_LE 487
#define OP_BYTES_APPEND_U64_BE 488
#define OP_BYTES_APPEND_I8 489
#define OP_BYTES_APPEND_I16_LE 490
#define OP_BYTES_APPEND_I16_BE 491
#define OP_BYTES_APPEND_I24_LE 492
#define OP_BYTES_APPEND_I24_BE 493
#define OP_BYTES_APPEND_I32_LE 494
#define OP_BYTES_APPEND_I32_BE 495
#define OP_BYTES_APPEND_I64_LE 496
#define OP_BYTES_APPEND_I64_BE 497
#define OP_BYTES_WRITE_U8 500
#define OP_BYTES_WRITE_U16_LE 501
#define OP_BYTES_WRITE_U16_BE 502
#define OP_BYTES_WRITE_U24_LE 503
#define OP_BYTES_WRITE_U24_BE 504
#define OP_BYTES_WRITE_U32_LE 505
#define OP_BYTES_WRITE_U32_BE 506
#define OP_BYTES_WRITE_U64_LE 507
#define OP_BYTES_WRITE_U64_BE 508
#define OP_BYTES_WRITE_I8 509
#define OP_BYTES_WRITE_I16_LE 510
#define OP_BYTES_WRITE_I16_BE 511
#define OP_BYTES_WRITE_I24_LE 512
#define OP_BYTES_WRITE_I24_BE 513
#define OP_BYTES_WRITE_I32_LE 514
#define OP_BYTES_WRITE_I32_BE 515
#define OP_BYTES_WRITE_I64_LE 516
#define OP_BYTES_WRITE_I64_BE 517
#define OP_BYTES_READ_ULEB128 520
#define OP_BYTES_APPEND_ULEB128 521
#define OP_BYTES_READ_SLEB128 522
#define OP_BYTES_APPEND_SLEB128 523
#define OP_ASSERT_NONE 530
#define OP_ASSERT_BOOLEAN 531
#define OP_ASSERT_INTEGER 532
#define OP_ASSERT_FLOAT 533
#define OP_ASSERT_COMPLEX 534
#define OP_ASSERT_STRING 535
#define OP_ASSERT_SYMBOL 536
#define OP_ASSERT_LIST 537
#define OP_ASSERT_DICT 538
#define OP_ASSERT_SET 539
#define OP_ASSERT_FUNCTION 540
#define OP_ASSERT_BYTES 541
#define OP_ASSERT_STRUCT 542
#define OP_ASSERT_STRUCTTYPE 543

/*
 * Profiling control — enable and extract.
 * These are always compiled in (not under MENAI_PROFILE) so the bridge
 * can call them regardless of build mode.  When profiling is not compiled
 * into the dispatch loop the counters simply stay at zero.
 */
void
menai_vm_enable_profiling(MenaiVMState *vs)
{
    memset(&vs->_profile, 0, sizeof(MenaiProfileData));
    vs->_profile.enabled = 1;
}

void
menai_vm_get_profile_data(MenaiVMState *vs, uint64_t *out_counts, uint64_t *out_total_instr)
{
    memcpy(out_counts, vs->_profile.opcode_counts, sizeof(vs->_profile.opcode_counts));
    *out_total_instr = vs->_profile.total_instructions;
}

/*
 * menai_integer_to_menai_bigint — promote a MenaiInteger to an owned MenaiBigInt.
 * Caller must ensure val is a MenaiInteger and must free *out after use.
 * *out must be initialised (menai_bigint_init) before calling.
 * Returns 0 on success, negative MENAI_ERR_* on failure.
 */
static inline int
menai_integer_to_menai_bigint(MenaiInteger *val, MenaiBigInt *out)
{
    if (!val->is_big) {
        return menai_bigint_from_long(val->fixed, out);
    }

    return menai_bigint_copy(&val->big, out);
}

/*
 * menai_integer_to_long — extract a C long from a MenaiInteger.
 * Returns 0 on success, -1 on error (value too large for a C long).
 * Caller must ensure val is a MenaiInteger.
 */
static inline int
menai_integer_to_long(MenaiInteger *val, long *out)
{
    if (!val->is_big) {
        *out = val->fixed;
        return 0;
    }

    if (menai_bigint_to_long(&val->big, out) < 0) {
        return -1;
    }

    return 0;
}

/*
 * menai_integer_to_long_long — extract a C long long from a MenaiInteger.
 * Returns 0 on success, -1 on error (value too large for a C long long).
 * Caller must ensure val is a MenaiInteger.
 */
static inline int
menai_integer_to_long_long(MenaiInteger *val, long long *out)
{
    if (!val->is_big) {
        *out = val->fixed;
        return 0;
    }

    if (menai_bigint_to_long_long(&val->big, out) < 0) {
        return -1;
    }

    return 0;
}

/*
 * menai_integer_to_unsigned_long_long — extract an unsigned long long from a MenaiInteger.
 * Returns 0 on success, -1 on error (no exception set — caller handles).
 * Caller must ensure val is a MenaiInteger.
 */
static inline int
menai_integer_to_unsigned_long_long(MenaiInteger *val, unsigned long long *out)
{
    if (!val->is_big) {
        if (val->fixed < 0) {
            return -1;
        }

        *out = (unsigned long long)val->fixed;
        return 0;
    }

    return menai_bigint_to_unsigned_long_long(&val->big, out);
}

/*
 * menai_integer_to_ssize_t — extract a ssize_t from a MenaiInteger.
 * Returns 0 on success, -1 on error (value too large for a ssize_t).
 * Caller must ensure val is a MenaiInteger.
 */
static inline int
menai_integer_to_ssize_t(MenaiInteger *val, ssize_t *out)
{
    long tmp;
    if (menai_integer_to_long(val, &tmp) < 0) {
        return -1;
    }

    *out = (ssize_t)tmp;
    return 0;
}

/*
 * menai_reg_set_own — store an owned reference into a register slot.
 *
 * val is an already-owned reference (e.g. freshly allocated, or returned from
 * a constructor).  The old slot value is released.  The slot must not be NULL.
 */
static inline void
menai_reg_set_own(MenaiVMState *vs, MenaiValue **regs, int slot, MenaiValue *val)
{
    menai_value_release(vs, regs[slot]);
    regs[slot] = val;
}

/*
 * menai_reg_set_borrow — store a borrowed reference into a register slot.
 *
 * val is a borrowed reference (e.g. read from another register, a constant
 * table, or a container element).  A retain is taken on val, then the old
 * slot value is released.  The slot must not be NULL.
 */
static inline void
menai_reg_set_borrow(MenaiVMState *vs, MenaiValue **regs, int slot, MenaiValue *val)
{
    menai_value_retain(val);
    menai_value_release(vs, regs[slot]);
    regs[slot] = val;
}

static inline void bool_store(MenaiVMState *vs, MenaiValue **regs, int slot, int cond)
{
    menai_reg_set_borrow(vs, regs, slot, cond ? (MenaiValue *)menai_boolean_true(vs) : (MenaiValue *)menai_boolean_false(vs));
}

/*
 * trim_to_ascii_buf — trim whitespace from a MenaiString, copy the result
 * into a caller-provided stack buffer as null-terminated ASCII, and release
 * the trimmed intermediate.  Returns 1 on success, 0 if the trimmed string
 * is too long for the buffer or contains a non-ASCII codepoint.  On failure
 * the trimmed string is still released.
 */
static int
trim_to_ascii_buf(MenaiVMState *vs, MenaiString *s, char *buf, size_t buf_size)
{
    MenaiString *trimmed = alloc_menai_string_from_trim(vs, s);
    if (trimmed == NULL) {
        return -1;
    }

    ssize_t len = trimmed->length;
    int ok = 0;
    if (len < (ssize_t)buf_size) {
        ok = 1;
        for (ssize_t i = 0; i < len; i++) {
            if (trimmed->data[i] > 0x7F) {
                ok = 0;
                break;
            }

            buf[i] = (char)trimmed->data[i];
        }

        if (ok) {
            buf[len] = '\0';
        }
    }

    menai_value_release(vs, (MenaiValue *)trimmed);
    return ok;
}

/*
 * parse_complex_string — parse a null-terminated ASCII string as a complex
 * number, matching Python's complex() constructor semantics.
 *
 * Grammar (after stripping leading/trailing whitespace):
 *
 *   complex  := float
 *             | imag_part
 *             | float imag_part
 *
 *   imag_part := sign? coefficient? ('j' | 'J')
 *   coefficient := float_magnitude
 *
 * where float is parsed by strtod (handles inf, nan, signs, scientific
 * notation), and a bare 'j'/'+j'/'-j' with no coefficient means 1j/-1j.
 *
 * Stores the parsed real and imaginary parts in *out_real and *out_imag.
 * Returns 1 on success, 0 on parse failure.
 */
static int
parse_complex_string(const char *s, double *out_real, double *out_imag)
{
    /* Skip leading whitespace. */
    while (*s == ' ' || *s == '\t') {
        s++;
    }

    /* Find end, skip trailing whitespace. */
    size_t len = strlen(s);
    while (len > 0 && (s[len - 1] == ' ' || s[len - 1] == '\t')) {
        len--;
    }

    if (len == 0) {
        return 0;
    }

    /* Work on a null-terminated copy of the trimmed string. */
    char buf[64];
    if (len >= sizeof(buf)) {
        return 0;
    }

    memcpy(buf, s, len);
    buf[len] = '\0';

    char *p = buf;
    char *end;
    double real = 0.0;
    double imag = 0.0;

    /*
     * Try to parse a leading float.  strtod consumes an optional sign,
     * digits, decimal point, exponent, and the special strings inf/nan.
     * If it consumes nothing (end == p), there is no real part.
     */
    double first = strtod(p, &end);
    if (end == p) {
        /* No leading float — must be a bare sign + 'j'. */
        first = 0.0;
    } else {
        p = end;
    }

    /* Check for end of string (pure real: "1.5", "inf", etc.) */
    if (*p == '\0') {
        *out_real = first;
        *out_imag = 0.0;
        return 1;
    }

    /* Check for imaginary suffix 'j'/'J' immediately (pure imaginary). */
    if (*p == 'j' || *p == 'J') {
        if (*(p + 1) != '\0') {
            return 0;
        }

        *out_real = 0.0;
        *out_imag = (end == buf) ? 1.0 : first;
        return 1;
    }

    /*
     * We have a real part (first) followed by an imaginary part.
     * The imaginary part starts with '+' or '-'.
     */
    if (*p != '+' && *p != '-') {
        return 0;
    }

    real = first;

    /*
     * Peek ahead: if the next character after the sign is 'j'/'J', this
     * is a bare +j or -j (coefficient 1).
     */
    if ((p[1] == 'j' || p[1] == 'J') && p[2] == '\0') {
        imag = (*p == '-') ? -1.0 : 1.0;
        *out_real = real;
        *out_imag = imag;
        return 1;
    }

    /* Parse the imaginary coefficient. */
    double imag_coeff = strtod(p, &end);
    if (end == p) {
        return 0;
    }

    p = end;
    if (*p != 'j' && *p != 'J') {
        return 0;
    }

    if (*(p + 1) != '\0') {
        return 0;
    }

    *out_real = real;
    *out_imag = imag_coeff;
    return 1;
}

/*
 * Frame struct
 *
 * All fields are plain C.  code_obj is a retained MenaiCodeObject *; all
 * other pointers are borrowed from it and live as long as code_obj does.
 */
typedef struct {
    MenaiCodeObject *code_obj;       /* retained — owns all frame metadata */
    MenaiValue **constants_items;    /* borrowed from code_obj->constants */
    const char **names_items;        /* borrowed from code_obj->names */
    hash_t *name_hashes;             /* borrowed from code_obj->name_hashes */
    ssize_t nnames;                  /* borrowed from code_obj->nnames */
    MenaiCodeObject **children;      /* borrowed from code_obj->children */
    ssize_t nchildren;               /* borrowed from code_obj->nchildren */
    uint64_t *instrs;                /* borrowed from code_obj->instrs */
    int code_len;
    int local_count;
    int ip;
    int base;
    MenaiValue **frame_regs;
    int return_dest;
    int is_sentinel;
} Frame;

/*
 * frame_setup
 *
 * Populates a Frame from a MenaiCodeObject.  Takes a retain on co.
 */
static inline void
frame_setup(Frame *f, MenaiValue **regs, MenaiCodeObject *co, int base, int return_dest)
{
    menai_code_object_retain(co);
    f->code_obj = co;
    f->constants_items = co->constants;
    f->names_items = co->names;
    f->name_hashes = co->name_hashes;
    f->nnames = co->nnames;
    f->children = co->children;
    f->nchildren = co->nchildren;
    f->instrs = co->instrs;
    f->code_len = co->code_len;
    f->local_count = co->local_count;
    f->ip = 0;
    f->base = base;
    f->frame_regs = regs + base;
    f->return_dest = return_dest;
    f->is_sentinel = 0;
}

/*
 * Register array helpers
 *
 * The register array is a flat MenaiValue * array:
 *   regs[depth * max_locals + slot]
 * All slots are initialised to menai_none(vs) (borrowed — the singleton is
 * kept alive by the VM state).  menai_reg_set_own/menai_reg_set_borrow manage reference counts correctly.
 */
/*
 * call_setup — shared logic for CALL and APPLY
 *
 * Sets up new_frame for a call to func_obj with arity arguments already
 * written into regs[callee_base .. callee_base+arity-1].
 *
 * Handles:
 *   - arity checking (fixed and variadic)
 *   - variadic rest-list packing
 *   - capture slot population
 *
 * Returns MENAI_OK on success, or a MENAI_ERR_* code on error.
 */
static int
call_setup(MenaiVMState *vs, Frame *new_frame, MenaiFunction *func, MenaiValue **regs, int callee_base, int arity, int return_dest)
{
    MenaiCodeObject *co = func->bytecode;
    int param_count = co->param_count;
    int is_variadic = co->is_variadic;

    if (MENAI_UNLIKELY(is_variadic)) {
        int min_arity = param_count - 1;
        if (arity < min_arity) {
            return MENAI_ERR_ARITY_MISMATCH;
        }

        /* Pack excess args into a MenaiList for the rest parameter. */
        int rest_count = arity - min_arity;
        MenaiList *rest_list = alloc_menai_list(vs, rest_count);
        if (!rest_list) {
            return MENAI_ERR_NOMEM;
        }

        for (int k = 0; k < rest_count; k++) {
            MenaiValue *elem = regs[callee_base + min_arity + k];
            menai_value_retain(elem);
            rest_list->elements[k] = elem;
        }

        menai_reg_set_own(vs, regs, callee_base + min_arity, (MenaiValue *)rest_list);
    } else if (MENAI_UNLIKELY(arity != param_count)) {
        return MENAI_ERR_ARITY_MISMATCH;
    }

    /* Populate capture slots: regs[callee_base + param_count + i] */
    ssize_t ncap = func->ncap;
    MenaiValue **captures = func->captures;
    for (ssize_t i = 0; i < ncap; i++) {
        MenaiValue *cv = *captures++;
        menai_reg_set_borrow(vs, regs, callee_base + param_count + (int)i, cv);
    }

    frame_setup(new_frame, regs, co, callee_base, return_dest);
    return MENAI_OK;
}

/*
 * Internal execute — called by menai_vm_c_execute after setup.
 * Returns the result value (new reference) or NULL on error.
 */
static MenaiValue *
execute_loop(MenaiVMState *vs, MenaiCodeObject *code, const GlobalsTable *extra_globals)
{
    int vm_err = MENAI_OK;
    const char *vm_user_message = NULL;

    MenaiValue **regs = vs->regs;

    /* Frame stack — depth 0 is the sentinel. */
    Frame frames[MAX_FRAME_DEPTH + 1];
    frames[0] = (Frame){
        .is_sentinel = 1,
        .code_obj = NULL,
        .constants_items = NULL,
        .instrs = NULL,
    };
    frames[1] = (Frame){
        .is_sentinel = 0,
        .code_obj = NULL,
        .constants_items = NULL,
        .instrs = NULL,
    };

    /* Set up frame at depth 1 for the top-level code object. */
    frame_setup(&frames[1], regs, code, 0, 0);

    int frame_depth = 1;
    Frame *frame = &frames[1];
    int instr_count = 0;
    int cur_ip = 0;
    MenaiValue **frame_regs = frame->frame_regs;

    while (1) {
        /* Cancellation check */
        if ((++instr_count & (CANCEL_CHECK_INTERVAL - 1)) == 0) {
            instr_count = 0;

            if (_menai_atomic_load((_menai_atomic_int *)&vs->_cancel_flag)) {
                vm_err = MENAI_ERR_CANCELLED;
                goto error;
            }
        }

        if (frame->ip >= frame->code_len) {
            vm_err = MENAI_ERR_MISSING_RETURN;
            goto error;
        }

        /* Fetch and decode instruction */
        cur_ip = frame->ip;
        uint64_t word = frame->instrs[frame->ip++];
        int opcode = (int)((word >> OPCODE_SHIFT) & OPCODE_MASK);
        int dest = (int)((word >> DEST_SHIFT) & FIELD_MASK);

        if (vs->_profile.enabled) {
            vs->_profile.opcode_counts[opcode]++;
            vs->_profile.total_instructions++;
        }

        switch (opcode) {
        case OP_LOAD_NONE:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            break;

        case OP_LOAD_TRUE:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_boolean_true(vs));
            break;

        case OP_LOAD_FALSE:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_boolean_false(vs));
            break;

        case OP_LOAD_EMPTY_LIST:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)vs->empty_list);
            break;

        case OP_LOAD_EMPTY_DICT:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)vs->empty_dict);
            break;

        case OP_LOAD_EMPTY_SET:
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)vs->empty_set);
            break;

        case OP_LOAD_CONST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *val = frame->constants_items[src0];
            menai_reg_set_borrow(vs, frame_regs, dest, val);
            break;
        }

        case OP_LOAD_NAME: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            const char *name_str = frame->names_items[src0];
            hash_t name_hash = frame->name_hashes[src0];
            MenaiValue *val = NULL;
            if (extra_globals != NULL) {
                val = globals_lookup(extra_globals, name_str, name_hash);
            }

            if (val == NULL && vs->_globals_valid) {
                val = globals_lookup(&vs->_globals, name_str, name_hash);
            }

            if (val == NULL) {
                vm_err = MENAI_ERR_UNDEFINED_VARIABLE;
                goto error;
            }

            menai_reg_set_borrow(vs, frame_regs, dest, val);
            break;
        }

        case OP_MOVE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            menai_reg_set_borrow(vs, frame_regs, dest, frame_regs[src0]);
            break;
        }

        case OP_JUMP: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            frame->ip = src0;
            break;
        }

        case OP_JUMP_IF_FALSE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBoolean *cond = (MenaiBoolean *)frame_regs[src0];
            if (!cond->value) {
                int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
                frame->ip = src1;
            }

            break;
        }

        case OP_JUMP_IF_TRUE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBoolean *cond = (MenaiBoolean *)frame_regs[src0];
            if (cond->value) {
                int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
                frame->ip = src1;
            }

            break;
        }

        case OP_RAISE_ERROR: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *msg = (MenaiString *)frame_regs[src0];
            char *cstr = alloc_utf8_from_menai_string(msg, NULL);
            if (cstr == NULL) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            vm_err = MENAI_ERR_USER_ERROR;
            vm_user_message = cstr;
            goto error;
        }

        case OP_RETURN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *retval = frame_regs[src0];
            menai_value_retain(retval);

            int saved_return_dest = frame->return_dest;
            menai_code_object_release(vs, frame->code_obj);
            frame->code_obj = NULL;
            frame_depth--;
            Frame *caller = &frames[frame_depth];

            if (caller->is_sentinel) {
                /* Top-level return — exit the loop. */
                return retval;
            }

            /* Store result into caller's register window. */
            menai_reg_set_own(vs, regs, caller->base + saved_return_dest, retval);

            frame = caller;
            frame_regs = frame->frame_regs;
            break;
        }

        case OP_CALL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *raw = frame_regs[src0];
            int arity = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            int callee_base = frame->base + frame->local_count;

            if (IS_MENAI_FUNCTION(raw)) {
                if (frame_depth >= MAX_FRAME_DEPTH) {
                    vm_err = MENAI_ERR_CALL_DEPTH_EXCEEDED;
                    goto error;
                }

                frame_depth++;
                Frame *new_frame = &frames[frame_depth];
                vm_err = call_setup(vs, new_frame, (MenaiFunction *)raw, regs, callee_base, arity, dest);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    frame_depth--;
                    goto error;
                }

                frame = new_frame;
                frame_regs = frame->frame_regs;
                break;
            }

            if (IS_MENAI_STRUCTTYPE(raw)) {
                /* Struct constructor call */
                MenaiStructType *sraw = (MenaiStructType *)raw;
                int n_fields = sraw->nfields;
                if (arity != (int)n_fields) {
                    vm_err = MENAI_ERR_STRUCT_ARITY_MISMATCH;
                    goto error;
                }

                MenaiStruct *instance = alloc_menai_struct(vs, sraw, &regs[callee_base], n_fields);
                if (instance == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)instance);
                break;
            }

            vm_err = MENAI_ERR_NOT_CALLABLE;
            goto error;
        }

        case OP_TAIL_CALL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *raw = frame_regs[src0];
            int n_args = (int)((word >> SRC1_SHIFT) & FIELD_MASK);

            /* Take an owned reference before the arg-moving loop.
             * The loop may overwrite regs[base+src0] if src0 < n_args,
             * which would decrement raw's refcount to zero and free it. */
            menai_value_retain(raw);

            int local_count = frame->local_count;

            if (IS_MENAI_FUNCTION(raw)) {
                /* Move outgoing args down to base+0..n_args-1 in place. */
                for (int i = 0; i < n_args; i++) {
                    MenaiValue *v = frame_regs[local_count + i];
                    menai_reg_set_borrow(vs, frame_regs, i, v);
                }

                /* Reuse current frame — release old code_obj and instructions. */
                menai_code_object_release(vs, frame->code_obj);
                frame->code_obj = NULL;

                int saved_return_dest = frame->return_dest;
                vm_err = call_setup(vs, frame, (MenaiFunction *)raw, regs, frame->base, n_args, saved_return_dest);
                menai_value_release(vs, raw);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }

                break;
            }

            if (IS_MENAI_STRUCTTYPE(raw)) {
                MenaiStructType *sraw = (MenaiStructType *)raw;
                int n_fields = sraw->nfields;
                if (n_args != (int)n_fields) {
                    vm_err = MENAI_ERR_STRUCT_ARITY_MISMATCH;
                    menai_value_release(vs, raw);
                    goto error;
                }

                MenaiStruct *retval = alloc_menai_struct(vs, sraw, &frame_regs[local_count], n_fields);
                if (retval == NULL) {
                    menai_value_release(vs, raw);
                    goto error;
                }

                /* Tail-return the struct: pop frame and deliver to caller. */
                int saved_return_dest = frame->return_dest;
                menai_code_object_release(vs, frame->code_obj);
                frame->code_obj = NULL;
                frame_depth--;
                Frame *caller = &frames[frame_depth];
                if (caller->is_sentinel) {
                    menai_value_release(vs, raw);
                    return (MenaiValue *)retval;
                }

                menai_reg_set_own(vs, regs, caller->base + saved_return_dest, (MenaiValue *)retval);
                menai_value_release(vs, raw);
                frame = caller;
                frame_regs = frame->frame_regs;
                break;
            }

            menai_value_release(vs, raw);
            vm_err = MENAI_ERR_NOT_CALLABLE;
            goto error;
        }

        case OP_APPLY: {
            /*
             * APPLY dest, src0, src1:
             * src0 = function register, src1 = arg_list register.
             * Scatters the list into the callee's register window and pushes a frame.
             */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *raw_func = frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *raw_args = frame_regs[src1];
            if (MENAI_UNLIKELY(!IS_MENAI_LIST(raw_args))) {
                vm_err = MENAI_ERR_APPLY_SECOND_NOT_LIST;
                goto error;
            }

            MenaiList *list = (MenaiList *)raw_args;
            MenaiValue **elements = list->elements;
            int arity = (int)list->length;

            if (IS_MENAI_FUNCTION(raw_func)) {
                if (frame_depth >= MAX_FRAME_DEPTH) {
                    vm_err = MENAI_ERR_CALL_DEPTH_EXCEEDED;
                    goto error;
                }

                int callee_base = frame->base + frame->local_count;

                /* Scatter list elements into the callee window */
                for (int i = 0; i < arity; i++) {
                    menai_reg_set_borrow(vs, regs, callee_base + i, elements[i]);
                }

                frame_depth++;
                Frame *new_frame = &frames[frame_depth];
                vm_err = call_setup(vs, new_frame, (MenaiFunction *)raw_func, regs, callee_base, arity, dest);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    frame_depth--;
                    goto error;
                }

                frame = new_frame;
                frame_regs = frame->frame_regs;
                break;
            }

            if (IS_MENAI_STRUCTTYPE(raw_func)) {
                MenaiStructType *sraw_func = (MenaiStructType *)raw_func;
                int n_fields = sraw_func->nfields;
                if (arity != (int)n_fields) {
                    vm_err = MENAI_ERR_STRUCT_ARITY_MISMATCH;
                    goto error;
                }

                MenaiStruct *instance = alloc_menai_struct(vs, sraw_func, elements, n_fields);
                if (instance == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)instance);
                break;
            }

            vm_err = MENAI_ERR_APPLY_FIRST_NOT_FUNCTION;
            goto error;
        }

        case OP_TAIL_APPLY: {
            /*
             * TAIL_APPLY src0, src1:
             * src0 = function register, src1 = arg_list register.
             * Reuses current frame (tail position).
             */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *raw_func = frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *raw_args = frame_regs[src1];

            /* Own raw_func before the scatter loop which may overwrite its slot. */
            /* Own raw_args for the same reason — src1 may be < arity. */
            menai_value_retain(raw_func);
            menai_value_retain(raw_args);
            if (MENAI_UNLIKELY(!IS_MENAI_LIST(raw_args))) {
                menai_value_release(vs, raw_func);
                menai_value_release(vs, raw_args);
                vm_err = MENAI_ERR_APPLY_SECOND_NOT_LIST;
                goto error;
            }

            MenaiList *list = (MenaiList *)raw_args;
            MenaiValue **elements = list->elements;
            int arity = (int)list->length;

            if (IS_MENAI_FUNCTION(raw_func)) {
                /* Scatter args into base+0..arity-1 (reusing current frame's base) */
                for (int i = 0; i < arity; i++) {
                    menai_reg_set_borrow(vs, frame_regs, i, elements[i]);
                }

                menai_value_release(vs, raw_args);

                /* Release old code_obj and instructions, reuse frame. */
                menai_code_object_release(vs, frame->code_obj);
                frame->code_obj = NULL;

                int saved_return_dest = frame->return_dest;
                vm_err = call_setup(vs, frame, (MenaiFunction *)raw_func, regs, frame->base, arity, saved_return_dest);
                menai_value_release(vs, raw_func);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }

                break;
            }

            if (IS_MENAI_STRUCTTYPE(raw_func)) {
                MenaiStructType *sraw_func = (MenaiStructType *)raw_func;
                int n_fields = sraw_func->nfields;
                if (arity != (int)n_fields) {
                    menai_value_release(vs, raw_func);
                    menai_value_release(vs, raw_args);
                    vm_err = MENAI_ERR_STRUCT_ARITY_MISMATCH;
                    goto error;
                }

                MenaiStruct *retval = alloc_menai_struct(vs, sraw_func, elements, n_fields);
                if (retval == NULL) {
                    menai_value_release(vs, raw_args);
                    menai_value_release(vs, raw_func);
                    goto error;
                }

                int saved_return_dest = frame->return_dest;
                menai_code_object_release(vs, frame->code_obj);
                frame->code_obj = NULL;
                frame_depth--;
                Frame *caller = &frames[frame_depth];
                if (caller->is_sentinel) {
                    menai_value_release(vs, raw_args);
                    menai_value_release(vs, raw_func);
                    return (MenaiValue *)retval;
                }

                menai_reg_set_own(vs, regs, caller->base + saved_return_dest, (MenaiValue *)retval);
                menai_value_release(vs, raw_args);
                menai_value_release(vs, raw_func);
                frame = caller;
                frame_regs = frame->frame_regs;
                break;
            }

            menai_value_release(vs, raw_func);
            menai_value_release(vs, raw_args);
            vm_err = MENAI_ERR_APPLY_FIRST_NOT_FUNCTION;
            goto error;
        }

        case OP_NONE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_NONE(frame_regs[src0]));
            break;
        }

        case OP_BOOLEAN_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_BOOLEAN(frame_regs[src0]));
            break;
        }

        case OP_BOOLEAN_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBoolean *a = (MenaiBoolean *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBoolean *b = (MenaiBoolean *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_boolean_equal(a, b));
            break;
        }

        case OP_BOOLEAN_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBoolean *a = (MenaiBoolean *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBoolean *b = (MenaiBoolean *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_boolean_equal(a, b));
            break;
        }

        case OP_BOOLEAN_NOT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBoolean *a = (MenaiBoolean *)frame_regs[src0];
            bool_store(vs, frame_regs, dest, !a->value);
            break;
        }

        case OP_SYMBOL_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_SYMBOL(frame_regs[src0]));
            break;
        }

        case OP_SYMBOL_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSymbol *a = (MenaiSymbol *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSymbol *b = (MenaiSymbol *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_symbol_equal(a, b));
            break;
        }

        case OP_SYMBOL_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSymbol *a = (MenaiSymbol *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSymbol *b = (MenaiSymbol *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_symbol_equal(a, b));
            break;
        }

        case OP_SYMBOL_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSymbol *a = (MenaiSymbol *)frame_regs[src0];
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)a->name);
            break;
        }

        case OP_FUNCTION_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_FUNCTION(frame_regs[src0]));
            break;
        }

        case OP_FUNCTION_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *a = frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *b = frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_function_equal(a, b));
            break;
        }

        case OP_FUNCTION_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *a = frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *b = frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_function_equal(a, b));
            break;
        }

        case OP_FUNCTION_MIN_ARITY: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFunction *f = (MenaiFunction *)frame_regs[src0];
            int min_a = f->bytecode->is_variadic ? f->bytecode->param_count - 1 : f->bytecode->param_count;
            MenaiInteger *r = alloc_menai_integer_from_long(vs, min_a);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FUNCTION_VARIADIC_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFunction *f = (MenaiFunction *)frame_regs[src0];
            bool_store(vs, frame_regs, dest, f->bytecode->is_variadic);
            break;
        }

        case OP_FUNCTION_ACCEPTS_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFunction *f = (MenaiFunction *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *n_obj = frame_regs[src1];
            int pc = f->bytecode->param_count;
            int is_var = f->bytecode->is_variadic;
            MenaiInteger *n_io = (MenaiInteger *)n_obj;

            long n;
            if (!n_io->is_big) {
                n = n_io->fixed;
            } else {
                vm_err = menai_bigint_to_long(&n_io->big, &n);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            int accepts = is_var ? (n >= pc - 1) : (n == pc);
            bool_store(vs, frame_regs, dest, accepts);
            break;
        }

        case OP_INTEGER_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_INTEGER(frame_regs[src0]));
            break;
        }

        case OP_INTEGER_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_integer_equal(a, b));
            break;
        }

        case OP_INTEGER_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_integer_equal(a, b));
            break;
        }

        case OP_INTEGER_LT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                bool_store(vs, frame_regs, dest, a->fixed < b->fixed);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            bool_store(vs, frame_regs, dest, menai_bigint_lt(pa, pb));

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_GT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                bool_store(vs, frame_regs, dest, a->fixed > b->fixed);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            bool_store(vs, frame_regs, dest, menai_bigint_gt(pa, pb));

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_LTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                bool_store(vs, frame_regs, dest, a->fixed <= b->fixed);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            bool_store(vs, frame_regs, dest, menai_bigint_le(pa, pb));

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_GTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                bool_store(vs, frame_regs, dest, a->fixed >= b->fixed);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            bool_store(vs, frame_regs, dest, menai_bigint_ge(pa, pb));

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_ABS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];

            if (!a->is_big) {
                long sv = a->fixed;
                long rv = sv < 0 ? -sv : sv;
                /* LONG_MIN has no positive counterpart — promote to bigint. */
                if (sv == LONG_MIN) {
                    MenaiBigInt tmp;
                    menai_bigint_init(&tmp);
                    vm_err = menai_bigint_from_long(sv, &tmp);
                    if (MENAI_UNLIKELY(vm_err < 0)) {
                        goto error;
                    }

                    MenaiBigInt res;
                    menai_bigint_init(&res);
                    vm_err = menai_bigint_abs(&tmp, &res);
                    if (MENAI_UNLIKELY(vm_err < 0)) {
                        menai_bigint_final(&tmp);
                        goto error;
                    }

                    menai_bigint_final(&tmp);
                    MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
                    if (!r) {
                        goto error;
                    }

                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                    break;
                }

                MenaiInteger *r = alloc_menai_integer_from_long(vs, rv);
                if (!r) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                break;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_abs(&a->big, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_NEG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];

            if (!a->is_big) {
                long sv = a->fixed;
                /* LONG_MIN negation overflows — promote to bigint. */
                if (sv == LONG_MIN) {
                    MenaiBigInt tmp;
                    menai_bigint_init(&tmp);
                    vm_err = menai_bigint_from_long(sv, &tmp);
                    if (MENAI_UNLIKELY(vm_err < 0)) {
                        goto error;
                    }

                    MenaiBigInt res;
                    menai_bigint_init(&res);
                    vm_err = menai_bigint_neg(&tmp, &res);
                    if (MENAI_UNLIKELY(vm_err < 0)) {
                        menai_bigint_final(&tmp);
                        goto error;
                    }

                    menai_bigint_final(&tmp);
                    MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
                    if (!r) {
                        goto error;
                    }

                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                    break;
                }

                MenaiInteger *r = alloc_menai_integer_from_long(vs, -sv);
                if (!r) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                break;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_neg(&a->big, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_NOT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];

            MenaiBigInt tmp;
            menai_bigint_init(&tmp);
            vm_err = menai_integer_to_menai_bigint(a, &tmp);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_not(&tmp, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp);
                goto error;
            }

            menai_bigint_final(&tmp);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_ADD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (!a->is_big && !b->is_big) {
                long la = a->fixed;
                long lb = b->fixed;
                long lr;
                if (!_menai_add_overflow(la, lb, &lr)) {
                    MenaiInteger *r = alloc_menai_integer_from_long(vs, lr);
                    if (!r) {
                        goto error;
                    }

                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                    break;
                }
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_add(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_SUB: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (!a->is_big && !b->is_big) {
                long la = a->fixed;
                long lb = b->fixed;
                long lr;
                if (!_menai_sub_overflow(la, lb, &lr)) {
                    MenaiInteger *r = alloc_menai_integer_from_long(vs, lr);
                    if (!r) {
                        goto error;
                    }

                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                    break;
                }
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_sub(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_MUL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (!a->is_big && !b->is_big) {
                long la = a->fixed;
                long lb = b->fixed;
                long lr;
                if (!_menai_mul_overflow(la, lb, &lr)) {
                    MenaiInteger *r = alloc_menai_integer_from_long(vs, lr);
                    if (!r) {
                        goto error;
                    }

                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                    break;
                }
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_mul(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_DIV: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            int b_is_zero = (!b->is_big && b->fixed == 0) || (b->is_big && b->big.sign == 0);
            if (b_is_zero) {
                vm_err = MENAI_ERR_DIVISION_BY_ZERO;
                goto error;
            }

            if (!a->is_big && !b->is_big) {
                long la = a->fixed;
                long lb = b->fixed;

                /* Floor division: round toward negative infinity. */
                long lq = la / lb;
                long lr = la % lb;
                if (lr != 0 && ((lr < 0) != (lb < 0))) {
                    lq--;
                }

                MenaiInteger *r = alloc_menai_integer_from_long(vs, lq);
                if (!r) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                break;
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_floordiv(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_MOD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            int b_is_zero = (!b->is_big && b->fixed == 0) || (b->is_big && b->big.sign == 0);
            if (b_is_zero) {
                vm_err = MENAI_ERR_MODULO_BY_ZERO;
                goto error;
            }

            if (!a->is_big && !b->is_big) {
                long la = a->fixed;
                long lb = b->fixed;

                /* Floor modulo: result takes sign of divisor. */
                long lr = la % lb;
                if (lr != 0 && ((lr < 0) != (lb < 0))) {
                    lr += lb;
                }

                MenaiInteger *r = alloc_menai_integer_from_long(vs, lr);
                if (!r) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
                break;
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_mod(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_EXPN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            int b_is_neg = (!b->is_big && b->fixed < 0) || (b->is_big && b->big.sign == -1);
            if (b_is_neg) {
                vm_err = MENAI_ERR_NEGATIVE_EXPONENT;
                goto error;
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_pow(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_OR: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_or(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_AND: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_and(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_XOR: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt bv;
            menai_bigint_init(&bv);
            vm_err = menai_integer_to_menai_bigint(b, &bv);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_xor(&av, &bv, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                menai_bigint_final(&bv);
                goto error;
            }

            menai_bigint_final(&av);
            menai_bigint_final(&bv);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_SHIFT_LEFT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long shift;
            if (!b->is_big) {
                shift = b->fixed;
            } else {
                if (!menai_bigint_fits_long(&b->big)) {
                    vm_err = MENAI_ERR_SHIFT_TOO_LARGE;
                    goto error;
                }

                vm_err = menai_bigint_to_long(&b->big, &shift);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (shift < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SHIFT;
                goto error;
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_shift_left(&av, (ssize_t)shift, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            menai_bigint_final(&av);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_BIT_SHIFT_RIGHT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long shift;
            if (!b->is_big) {
                shift = b->fixed;
            } else {
                if (!menai_bigint_fits_long(&b->big)) {
                    vm_err = MENAI_ERR_SHIFT_TOO_LARGE;
                    goto error;
                }

                vm_err = menai_bigint_to_long(&b->big, &shift);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (shift < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SHIFT;
                goto error;
            }

            MenaiBigInt av;
            menai_bigint_init(&av);
            vm_err = menai_integer_to_menai_bigint(a, &av);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_shift_right(&av, (ssize_t)shift, &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&av);
                goto error;
            }

            menai_bigint_final(&av);
            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (!r) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_MIN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                menai_reg_set_borrow(vs, frame_regs, dest, a->fixed <= b->fixed ? (MenaiValue *)a : (MenaiValue *)b);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            menai_reg_set_borrow(vs, frame_regs, dest, menai_bigint_le(pa, pb) ? (MenaiValue *)a : (MenaiValue *)b);

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_MAX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            if (MENAI_LIKELY(!a->is_big && !b->is_big)) {
                menai_reg_set_borrow(vs, frame_regs, dest, a->fixed >= b->fixed ? (MenaiValue *)a : (MenaiValue *)b);
                break;
            }

            const MenaiBigInt *ma = a->is_big ? &a->big : NULL;
            const MenaiBigInt *mb = b->is_big ? &b->big : NULL;

            MenaiBigInt tmp_a;
            menai_bigint_init(&tmp_a);
            vm_err = menai_integer_to_menai_bigint(a, &tmp_a);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiBigInt tmp_b;
            menai_bigint_init(&tmp_b);
            vm_err = menai_integer_to_menai_bigint(b, &tmp_b);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_bigint_final(&tmp_a);
                goto error;
            }

            const MenaiBigInt *pa = a->is_big ? ma : &tmp_a;
            const MenaiBigInt *pb = b->is_big ? mb : &tmp_b;
            menai_reg_set_borrow(vs, frame_regs, dest, menai_bigint_ge(pa, pb) ? (MenaiValue *)a : (MenaiValue *)b);

            menai_bigint_final(&tmp_a);
            menai_bigint_final(&tmp_b);
            break;
        }

        case OP_INTEGER_TO_FLOAT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];

            double d;
            if (!a->is_big) {
                d = (double)a->fixed;
            } else {
                vm_err = menai_bigint_to_double(&a->big, &d);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            MenaiFloat *r = alloc_menai_float(vs, d);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_TO_COMPLEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            double re;
            if (!a->is_big) {
                re = (double)a->fixed;
            } else {
                vm_err = menai_bigint_to_double(&a->big, &re);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            double im;
            if (!b->is_big) {
                im = (double)b->fixed;
            } else {
                vm_err = menai_bigint_to_double(&b->big, &im);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            MenaiComplex *r = alloc_menai_complex(vs, re, im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long radix;
            if (!b->is_big) {
                radix = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &radix);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (radix != 2 && radix != 8 && radix != 10 && radix != 16) {
                vm_err = MENAI_ERR_INVALID_RADIX;
                goto error;
            }

            MenaiBigInt tmp;
            menai_bigint_init(&tmp);
            vm_err = menai_integer_to_menai_bigint(a, &tmp);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiString *r = menai_bigint_to_menai_string(vs, &tmp, (int)radix);
            menai_bigint_final(&tmp);
            if (r == NULL) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_INTEGER_CODEPOINT_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];

            long cp;
            if (!a->is_big) {
                cp = a->fixed;
            } else {
                vm_err = menai_bigint_to_long(&a->big, &cp);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (cp < 0 || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
                vm_err = MENAI_ERR_INVALID_CODEPOINT;
                goto error;
            }

            MenaiString *r = alloc_menai_string(vs, 1);
            if (r == NULL) {
                goto error;
            }

            r->data[0] = cp;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_FLOAT(frame_regs[src0]));
            break;
        }

        case OP_FLOAT_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_float_equal(a, b));
            break;
        }

        case OP_FLOAT_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_float_equal(a, b));
            break;
        }

        case OP_FLOAT_LT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, a->value < b->value);
            break;
        }

        case OP_FLOAT_GT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, a->value > b->value);
            break;
        }

        case OP_FLOAT_LTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, a->value <= b->value);
            break;
        }

        case OP_FLOAT_GTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, a->value >= b->value);
            break;
        }

        case OP_FLOAT_NEG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, -a->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ABS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, fabs(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);

            break;
        }

        case OP_FLOAT_ADD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, a->value + b->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_SUB: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, a->value - b->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_MUL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, a->value * b->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_DIV: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double bv = b->value;
            if (bv == 0.0) {
                vm_err = MENAI_ERR_DIVISION_BY_ZERO;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, a->value / bv);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_FLOOR_DIV: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double bv = b->value;
            if (bv == 0.0) {
                vm_err = MENAI_ERR_DIVISION_BY_ZERO;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, floor(a->value / bv));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_MOD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double bv = b->value;
            if (bv == 0.0) {
                vm_err = MENAI_ERR_MODULO_BY_ZERO;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, fmod(a->value, bv));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_EXP: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, exp(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_EXPN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, pow(a->value, b->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_LOG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < 0.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, v == 0.0 ? -INFINITY : log(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_LOG10: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < 0.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, v == 0.0 ? -INFINITY : log10(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_LOG2: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < 0.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, v == 0.0 ? -INFINITY : log2(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_LOGN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double av = a->value;
            double bv = b->value;
            if (bv <= 0.0 || bv == 1.0) {
                vm_err = MENAI_ERR_INVALID_LOG_BASE;
                goto error;
            }

            if (av < 0.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, av == 0.0 ? -INFINITY : log(av) / log(bv));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_SIN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, sin(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_COS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, cos(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TAN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, tan(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_SQRT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < 0.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, sqrt(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_FLOOR: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, floor(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_CEIL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, ceil(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ROUND: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, round(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_MIN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double av = a->value;
            double bv = b->value;
            MenaiFloat *r = alloc_menai_float(vs, av <= bv ? av : bv);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_MAX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            double av = a->value;
            double bv = b->value;
            MenaiFloat *r = alloc_menai_float(vs, av >= bv ? av : bv);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ATAN2: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, atan2(a->value, b->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_COSH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, cosh(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_SINH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, sinh(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TANH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, tanh(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ASIN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < -1.0 || v > 1.0) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, asin(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ACOS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < -1.0 || v > 1.0) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, acos(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_ATAN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, atan(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_HYPOT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, hypot(a->value, b->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_EXP2: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, exp2(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_CBRT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, cbrt(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_EXPM1: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, expm1(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_LOG1P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            double v = a->value;
            if (v < -1.0) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }

            MenaiFloat *r = alloc_menai_float(vs, v == -1.0 ? -INFINITY : log1p(v));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TRUNC: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, trunc(a->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_COPYSIGN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiFloat *r = alloc_menai_float(vs, copysign(a->value, b->value));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TO_INTEGER: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];

            MenaiBigInt res;
            menai_bigint_init(&res);
            vm_err = menai_bigint_from_double(trunc(a->value), &res);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_bigint(vs, res);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TO_COMPLEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiFloat *b = (MenaiFloat *)frame_regs[src1];
            MenaiComplex *r = alloc_menai_complex(vs, a->value, b->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_FLOAT_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFloat *a = (MenaiFloat *)frame_regs[src0];
            MenaiString *r = alloc_menai_string_from_float(vs, a->value);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_MAKE_CLOSURE: {
            /*
             * MAKE_CLOSURE dest, src0:
             * src0 is the index into code_objects of the child CodeObject.
             * Creates a MenaiFunction with capture slots initialised to None,
             * ready for PATCH_CLOSURE to fill in.
             */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            if (src0 >= (int)frame->nchildren) {
                vm_err = MENAI_ERR_CLOSURE_INDEX_OUT_OF_RANGE;
                goto error;
            }

            MenaiFunction *func = alloc_menai_function(vs, frame->children[src0]);
            if (func == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)func);
            break;
        }

        case OP_PATCH_CLOSURE: {
            /*
             * PATCH_CLOSURE src0, src1, src2:
             * src0 = closure register, src1 = capture slot index, src2 = value register.
             */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiFunction *closure = (MenaiFunction *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            if (src1 >= closure->ncap) {
                vm_err = MENAI_ERR_CLOSURE_INDEX_OUT_OF_RANGE;
                goto error;
            }

            MenaiValue *old = closure->captures[src1];
            menai_value_release(vs, old);

            int src2 = (int)(word & FIELD_MASK);
            MenaiValue *val = frame_regs[src2];
            menai_value_retain(val);
            closure->captures[src1] = val;
            break;
        }

        case OP_COMPLEX_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_COMPLEX(frame_regs[src0]));
            break;
        }

        case OP_COMPLEX_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_complex_equal(a, b));
            break;
        }

        case OP_COMPLEX_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_complex_equal(a, b));
            break;
        }

        case OP_COMPLEX_REAL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, a->real);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_IMAG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            MenaiFloat *r = alloc_menai_float(vs, a->imag);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_ABS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            double re = a->real;
            double im = a->imag;
            MenaiFloat *r = alloc_menai_float(vs, sqrt(re * re + im * im));
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_NEG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            MenaiComplex *r = alloc_menai_complex(vs, -a->real, -a->imag);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_ADD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            MenaiComplex *r = alloc_menai_complex(vs, a->real + b->real, a->imag + b->imag);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_SUB: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            MenaiComplex *r = alloc_menai_complex(vs, a->real - b->real, a->imag - b->imag);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_MUL: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            double ar = a->real;
            double ai = a->imag;
            double br = b->real;
            double bi = b->imag;
            MenaiComplex *r = alloc_menai_complex(vs, ar * br - ai * bi, ar * bi + ai * br);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_DIV: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            double ar = a->real;
            double ai = a->imag;
            double br = b->real;
            double bi = b->imag;
            if (br == 0.0 && bi == 0.0) {
                vm_err = MENAI_ERR_DIVISION_BY_ZERO;
                goto error;
            }

            double denom = br * br + bi * bi;
            MenaiComplex *r = alloc_menai_complex(vs, (ar * br + ai * bi) / denom, (ai * br - ar * bi) / denom);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_EXPN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            mc_t za = mc(a->real, a->imag);
            mc_t zb = mc(b->real, b->imag);
            mc_t cr = mc_pow(za, zb);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_EXP: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_exp(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_LOG: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_log(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_LOG10: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_log10(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_SIN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_sin(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_COS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_cos(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_TAN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_tan(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_SQRT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            mc_t z = mc(a->real, a->imag);
            mc_t cr = mc_sqrt(z);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_LOGN: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiComplex *b = (MenaiComplex *)frame_regs[src1];
            mc_t za = mc(a->real, a->imag);
            mc_t zb = mc(b->real, b->imag);
            if (mc_zero(zb)) {
                vm_err = MENAI_ERR_INVALID_LOG_BASE;
                goto error;
            }

            mc_t cr = mc_logn(za, zb);
            MenaiComplex *r = alloc_menai_complex(vs, cr.re, cr.im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_COMPLEX_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiComplex *a = (MenaiComplex *)frame_regs[src0];
            MenaiString *r = alloc_menai_string_from_complex(vs, a->real, a->imag);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_STRING(frame_regs[src0]));
            break;
        }

        case OP_STRING_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_string_equal(a, b));
            break;
        }

        case OP_STRING_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_string_equal(a, b));
            break;
        }

        case OP_STRING_LT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_string_compare(a, b) < 0);
            break;
        }

        case OP_STRING_GT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_string_compare(a, b) > 0);
            break;
        }

        case OP_STRING_LTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_string_compare(a, b) <= 0);
            break;
        }

        case OP_STRING_GTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_string_compare(a, b) >= 0);
            break;
        }

        case OP_STRING_LENGTH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, a->length);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_UPCASE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            ssize_t upcase_len = menai_string_upcase_length(a);
            MenaiString *r = alloc_menai_string(vs, upcase_len);
            if (r == NULL) {
                goto error;
            }

            menai_string_upcase(a, r);
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_DOWNCASE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            MenaiString *r = alloc_menai_string(vs, a->length);
            if (r == NULL) {
                goto error;
            }

            menai_string_downcase(a, r);
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TRIM: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            MenaiString *r = alloc_menai_string_from_trim(vs, a);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TRIM_LEFT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            MenaiString *r = alloc_menai_string_from_trim_left(vs, a);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TRIM_RIGHT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            MenaiString *r = alloc_menai_string_from_trim_right(vs, a);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_CONCAT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];

            MenaiString *r = alloc_menai_string(vs, a->length + b->length);
            if (r == NULL) {
                goto error;
            }

            ssize_t la = a->length;
            memcpy(r->data, a->data, (size_t)la * sizeof(uint32_t));
            ssize_t lb = b->length;
            memcpy(r->data + la, b->data, (size_t)lb * sizeof(uint32_t));
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_PREFIX_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *s = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *p = (MenaiString *)frame_regs[src1];
            ssize_t plen = p->length;

            bool r = false;
            if (plen <= s->length) {
                r = (memcmp(s->data, p->data, (size_t)plen * sizeof(uint32_t)) == 0);
            }

            bool_store(vs, frame_regs, dest, r);
            break;
        }

        case OP_STRING_SUFFIX_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *s = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *su = (MenaiString *)frame_regs[src1];
            ssize_t slen = s->length;
            ssize_t sulen = su->length;

            bool r = false;
            if (sulen <= slen) {
                r = (memcmp(s->data + (slen - sulen), su->data, (size_t)sulen * sizeof(uint32_t)) == 0);
            }

            bool_store(vs, frame_regs, dest, r);
            break;
        }

        case OP_STRING_REF: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long idx_l;
            if (!b->is_big) {
                idx_l = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &idx_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t idx = (ssize_t)idx_l;
            ssize_t slen = a->length;
            if (idx < 0 || idx >= slen) {
                vm_err = MENAI_ERR_INDEX_OUT_OF_RANGE;
                goto error;
            }

            MenaiString *r = alloc_menai_string(vs, 1);
            if (r == NULL) {
                goto error;
            }

            r->data[0] = a->data[idx];
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_SLICE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            int src2 = (int)(word & FIELD_MASK);
            MenaiInteger *c = (MenaiInteger *)frame_regs[src2];

            long start_l;
            if (!b->is_big) {
                start_l = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &start_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            long end_l;
            if (!c->is_big) {
                end_l = c->fixed;
            } else {
                vm_err = menai_bigint_to_long(&c->big, &end_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t start = (ssize_t)start_l, end = (ssize_t)end_l;
            ssize_t slen = a->length;
            if (start < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SLICE_INDEX;
                goto error;
            }

            if (end < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SLICE_INDEX;
                goto error;
            }

            if (start > slen) {
                vm_err = MENAI_ERR_SLICE_START_OUT_OF_RANGE;
                goto error;
            }

            if (end > slen) {
                vm_err = MENAI_ERR_SLICE_END_OUT_OF_RANGE;
                goto error;
            }

            if (start > end) {
                vm_err = MENAI_ERR_SLICE_START_AFTER_END;
                goto error;
            }

            ssize_t len = end - start;
            MenaiString *r = alloc_menai_string(vs, len);
            if (r == NULL) {
                goto error;
            }

            memcpy(r->data, a->data + start, (size_t)len * sizeof(uint32_t));
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_REPLACE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            int src2 = (int)(word & FIELD_MASK);
            MenaiString *c = (MenaiString *)frame_regs[src2];
            MenaiString *r = alloc_menai_string_from_replace(vs, a, b, c);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_INDEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            ssize_t idx = menai_string_find(a, b);
            if (idx == -1) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            } else {
                MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, idx);
                if (r == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }

            break;
        }

        case OP_STRING_TO_INTEGER_CODEPOINT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            ssize_t slen = a->length;
            if (slen != 1) {
                vm_err = MENAI_ERR_NOT_SINGLE_CHAR_STRING;
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_long(vs, (long)a->data[0]);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TO_INTEGER: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long radix;
            if (!b->is_big) {
                radix = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &radix);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (radix != 2 && radix != 8 && radix != 10 && radix != 16) {
                vm_err = MENAI_ERR_INVALID_RADIX;
                goto error;
            }

            char buf[64];
            int ok = trim_to_ascii_buf(vs, a, buf, sizeof(buf));
            if (ok < 0) {
                goto error;
            }
            if (!ok) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            MenaiBigInt sti_tmp;
            menai_bigint_init(&sti_tmp);
            int sti_ok = menai_bigint_from_string(buf, (int)radix, &sti_tmp);
            if (sti_ok < 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            } else {
                MenaiInteger *r = alloc_menai_integer_from_bigint(vs, sti_tmp);
                if (r == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }

            break;
        }

        case OP_STRING_TO_FLOAT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];

            char buf[64];
            int ok = trim_to_ascii_buf(vs, a, buf, sizeof(buf));
            if (ok < 0) {
                goto error;
            }
            if (!ok) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            char *end = NULL;
            double dv = strtod(buf, &end);
            if (end == buf || *end != '\0') {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            MenaiFloat *r = alloc_menai_float(vs, dv);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TO_COMPLEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];

            char buf[64];
            int ok = trim_to_ascii_buf(vs, a, buf, sizeof(buf));
            if (ok < 0) {
                goto error;
            }
            if (!ok) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            double re, im;
            if (!parse_complex_string(buf, &re, &im)) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            MenaiComplex *r = alloc_menai_complex(vs, re, im);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TO_LIST: {
            /* src0=string, src1=delimiter string */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *a = (MenaiString *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];
            ssize_t alen = a->length;
            ssize_t blen = b->length;
            const uint32_t *adata = a->data;
            const uint32_t *bdata = b->data;
            if (blen == 0) {
                /* Split into individual codepoints */
                MenaiList *r_stl = alloc_menai_list(vs, alen);
                if (!r_stl) {
                    vm_err = MENAI_ERR_NOMEM;
                    goto error;
                }

                MenaiValue **stl_arr = r_stl->elements;
                for (ssize_t i = 0; i < alen; i++) {
                    MenaiString *r = alloc_menai_string(vs, 1);
                    if (!r) {
                        for (ssize_t k = 0; k < i; k++) {
                            menai_value_release(vs, stl_arr[k]);
                        }

                        menai_value_release(vs, (MenaiValue *)r_stl);
                        goto error;
                    }

                    r->data[0] = adata[i];
                    stl_arr[i] = (MenaiValue *)r;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r_stl);
                break;
            }

            /* Split on delimiter — find occurrences and build list */
            ssize_t count = 0;
            for (ssize_t i = 0; i <= alen - blen; ) {
                if (memcmp(adata + i, bdata, (size_t)blen * sizeof(uint32_t)) == 0) {
                    count++;
                    i += blen;
                } else {
                    i++;
                }
            }

            ssize_t nparts = count + 1;
            MenaiList *r_parts = alloc_menai_list(vs, nparts);
            if (!r_parts) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **parts2 = r_parts->elements;
            ssize_t seg_start = 0, pi2 = 0;
            for (ssize_t i = 0; i <= alen; ) {
                int match = (i <= alen - blen) &&
                    (memcmp(adata + i, bdata, (size_t)blen * sizeof(uint32_t)) == 0);
                if (match || i == alen) {
                    ssize_t seg_len = i - seg_start;
                    MenaiString *p = alloc_menai_string(vs, seg_len);
                    if (!p) {
                        for (ssize_t k = 0; k < pi2; k++) {
                            menai_value_release(vs, parts2[k]);
                        }

                        menai_value_release(vs, (MenaiValue *)r_parts);
                        goto error;
                    }

                    memcpy(p->data, adata + seg_start, seg_len * sizeof(uint32_t));
                    parts2[pi2] = (MenaiValue *)p;
                    pi2++;
                    if (match) {
                        seg_start = i + blen;
                        i += blen;
                    } else {
                        break;
                    }
                } else {
                    i++;
                }
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r_parts);
            break;
        }

        case OP_BYTES_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_BYTES(frame_regs[src0]));
            break;
        }

        case OP_BYTES_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_bytes_equal(a, b));
            break;
        }

        case OP_BYTES_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_bytes_equal(a, b));
            break;
        }

        case OP_BYTES_LENGTH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, a->length);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_BYTES_REF: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *idx_val = (MenaiInteger *)frame_regs[src1];
            ssize_t offset;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(idx_val, &offset) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t blen = b->length;
            if (offset < 0 || offset >= blen) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_long(vs, (long)b->data[offset]);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_BYTES_APPEND_U8: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *v = (MenaiInteger *)frame_regs[src1];
            long val;
            if (MENAI_UNLIKELY(menai_integer_to_long(v, &val) < 0)) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            if (val < 0 || val > 255) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            MenaiBytes *r = alloc_menai_bytes_from_append_u8(vs, b, (uint8_t)val);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_TO_BYTES: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *lst = (MenaiList *)frame_regs[src0];
            ssize_t n = lst->length;
            MenaiBytes *mb = alloc_menai_bytes(vs, n);
            if (mb == NULL) {
                goto error;
            }

            for (ssize_t i = 0; i < n; i++) {
                MenaiValue *elem = lst->elements[i];
                if (MENAI_UNLIKELY(!IS_MENAI_INTEGER(elem))) {
                    vm_err = MENAI_ERR_LIST_ELEMENTS_NOT_INTEGERS;
                    menai_value_release(vs, (MenaiValue *)mb);
                    goto error;
                }

                long val;
                if (MENAI_UNLIKELY(menai_integer_to_long((MenaiInteger *)elem, &val) < 0)) {
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                    menai_value_release(vs, (MenaiValue *)mb);
                    goto error;
                }

                if (val < 0 || val > 255) {
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                    menai_value_release(vs, (MenaiValue *)mb);
                    goto error;
                }

                mb->inline_data[i] = (uint8_t)val;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)mb);
            break;
        }

        case OP_BYTES_SLICE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *start_val = (MenaiInteger *)frame_regs[src1];
            int src2 = (int)(word & FIELD_MASK);
            MenaiInteger *end_val = (MenaiInteger *)frame_regs[src2];
            ssize_t blen = b->length;

            ssize_t start;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(start_val, &start) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t end;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(end_val, &end) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            if (start < 0) {
                start = 0;
            }

            if (end > blen) {
                end = blen;
            }

            if (start > end) {
                start = end;
            }

            MenaiBytes *r = alloc_menai_bytes_from_slice(vs, b, start, end);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRING_TO_BYTES: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *s = (MenaiString *)frame_regs[src0];
            ssize_t slen = s->length;
            const uint32_t *cp = s->data;

            /* Encode UTF-32 codepoints to UTF-8 bytes */
            ssize_t nbytes = 0;
            for (ssize_t i = 0; i < slen; i++) {
                uint32_t c = cp[i];
                if (c < 0x80) {
                    nbytes += 1;
                } else if (c < 0x800) {
                    nbytes += 2;
                } else if (c < 0x10000) {
                    nbytes += 3;
                } else {
                    nbytes += 4;
                }
            }

            MenaiBytes *mb = alloc_menai_bytes(vs, nbytes);
            if (mb == NULL) {
                goto error;
            }

            ssize_t pos = 0;
            for (ssize_t i = 0; i < slen; i++) {
                uint32_t c = cp[i];
                if (c < 0x80) {
                    mb->inline_data[pos++] = (uint8_t)c;
                } else if (c < 0x800) {
                    mb->inline_data[pos++] = (uint8_t)(0xC0 | (c >> 6));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | (c & 0x3F));
                } else if (c < 0x10000) {
                    mb->inline_data[pos++] = (uint8_t)(0xE0 | (c >> 12));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | ((c >> 6) & 0x3F));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | (c & 0x3F));
                } else {
                    mb->inline_data[pos++] = (uint8_t)(0xF0 | (c >> 18));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | ((c >> 12) & 0x3F));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | ((c >> 6) & 0x3F));
                    mb->inline_data[pos++] = (uint8_t)(0x80 | (c & 0x3F));
                }
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)mb);
            break;
        }

        case OP_BYTES_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            ssize_t nbytes = b->length;
            const uint8_t *data = b->data;

            /* Decode UTF-8 to UTF-32 codepoints */
            ssize_t ncp = 0;
            ssize_t i = 0;
            while (i < nbytes) {
                uint8_t c = data[i];
                if (c < 0x80) {
                    i += 1;
                } else if ((c & 0xE0) == 0xC0) {
                    if (i + 2 > nbytes || (data[i+1] & 0xC0) != 0x80) {
                        vm_err = MENAI_ERR_INVALID_UTF8;
                        goto error;
                    }
                    i += 2;
                } else if ((c & 0xF0) == 0xE0) {
                    if (i + 3 > nbytes || (data[i+1] & 0xC0) != 0x80 || (data[i+2] & 0xC0) != 0x80) {
                        vm_err = MENAI_ERR_INVALID_UTF8;
                        goto error;
                    }
                    i += 3;
                } else if ((c & 0xF8) == 0xF0) {
                    if (i + 4 > nbytes || (data[i+1] & 0xC0) != 0x80 || (data[i+2] & 0xC0) != 0x80 || (data[i+3] & 0xC0) != 0x80) {
                        vm_err = MENAI_ERR_INVALID_UTF8;
                        goto error;
                    }
                    i += 4;
                } else {
                    vm_err = MENAI_ERR_INVALID_UTF8;
                    goto error;
                }
                ncp++;
            }

            uint32_t *cp_buf = (uint32_t *)malloc((size_t)ncp * sizeof(uint32_t));
            if (!cp_buf) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            i = 0;
            ssize_t cp_idx = 0;
            while (i < nbytes) {
                uint8_t c = data[i];
                if (c < 0x80) {
                    cp_buf[cp_idx++] = c;
                    i += 1;
                } else if ((c & 0xE0) == 0xC0) {
                    cp_buf[cp_idx++] = ((uint32_t)(c & 0x1F) << 6) | (data[i+1] & 0x3F);
                    i += 2;
                } else if ((c & 0xF0) == 0xE0) {
                    cp_buf[cp_idx++] = ((uint32_t)(c & 0x0F) << 12) | ((uint32_t)(data[i+1] & 0x3F) << 6) | (data[i+2] & 0x3F);
                    i += 3;
                } else {
                    cp_buf[cp_idx++] = ((uint32_t)(c & 0x07) << 18) | ((uint32_t)(data[i+1] & 0x3F) << 12) | ((uint32_t)(data[i+2] & 0x3F) << 6) | (data[i+3] & 0x3F);
                    i += 4;
                }
            }

            MenaiString *result = alloc_menai_string(vs, ncp);
            if (result == NULL) {
                free(cp_buf);
                goto error;
            }

            memcpy(result->data, cp_buf, ncp * sizeof(uint32_t));
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            free(cp_buf);
            break;
        }

        case OP_BYTES_TO_LIST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            ssize_t nbytes = b->length;
            MenaiList *result = alloc_menai_list(vs, nbytes);
            if (result == NULL) {
                goto error;
            }

            MenaiValue **arr = result->elements;
            const uint8_t *data = b->data;
            for (ssize_t i = 0; i < nbytes; i++) {
                arr[i] = (MenaiValue *)alloc_menai_integer_from_long(vs, (long)data[i]);
                if (arr[i] == NULL) {
                    for (ssize_t j = 0; j < i; j++) {
                        menai_value_release(vs, arr[j]);
                    }

                    menai_value_release(vs, (MenaiValue *)result);
                    goto error;
                }
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            break;
        }

        case OP_BYTES_TO_STRING_HEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            ssize_t nbytes = b->length;
            const uint8_t *data = b->data;

            uint32_t *cp_buf = (uint32_t *)malloc((size_t)(nbytes * 2) * sizeof(uint32_t));
            if (!cp_buf) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            static const uint32_t hex_chars[] = {
                '0', '1', '2', '3', '4', '5', '6', '7',
                '8', '9', 'a', 'b', 'c', 'd', 'e', 'f'
            };

            for (ssize_t i = 0; i < nbytes; i++) {
                cp_buf[i * 2] = hex_chars[(data[i] >> 4) & 0xF];
                cp_buf[i * 2 + 1] = hex_chars[data[i] & 0xF];
            }

            MenaiString *result = alloc_menai_string(vs, nbytes * 2);
            if (result == NULL) {
                free(cp_buf);
                goto error;
            }

            memcpy(result->data, cp_buf, nbytes * 2 * sizeof(uint32_t));
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            free(cp_buf);
            break;
        }

        case OP_STRING_HEX_TO_BYTES: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiString *s = (MenaiString *)frame_regs[src0];
            ssize_t slen = s->length;
            const uint32_t *cp = s->data;

            if (slen % 2 != 0) {
                vm_err = MENAI_ERR_HEX_EVEN_LENGTH;
                goto error;
            }

            ssize_t nbytes = slen / 2;
            MenaiBytes *mb = alloc_menai_bytes(vs, nbytes);
            if (mb == NULL) {
                goto error;
            }

            for (ssize_t i = 0; i < nbytes; i++) {
                uint32_t hi = cp[i * 2];
                uint32_t lo = cp[i * 2 + 1];

                int hi_val = -1;
                if (hi >= '0' && hi <= '9') {
                    hi_val = hi - '0';
                } else if (hi >= 'a' && hi <= 'f') {
                    hi_val = hi - 'a' + 10;
                } else if (hi >= 'A' && hi <= 'F') {
                    hi_val = hi - 'A' + 10;
                }

                int lo_val = -1;
                if (lo >= '0' && lo <= '9') {
                    lo_val = lo - '0';
                } else if (lo >= 'a' && lo <= 'f') {
                    lo_val = lo - 'a' + 10;
                } else if (lo >= 'A' && lo <= 'F') {
                    lo_val = lo - 'A' + 10;
                }

                if (hi_val < 0 || lo_val < 0) {
                    vm_err = MENAI_ERR_INVALID_HEX_CHAR;
                    menai_value_release(vs, (MenaiValue *)mb);
                    goto error;
                }

                mb->inline_data[i] = (uint8_t)((hi_val << 4) | lo_val);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)mb);
            break;
        }

        case OP_BYTES_CONCAT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            MenaiBytes *r = alloc_menai_bytes_from_concat(vs, a, b);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_BYTES_INDEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *haystack = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *needle = (MenaiBytes *)frame_regs[src1];
            ssize_t nlen = needle->length;
            ssize_t hlen = haystack->length;
            if (nlen == 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)alloc_menai_integer_from_ssize_t(vs, 0));
                break;
            }

            if (nlen > hlen) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
                break;
            }

            const uint8_t *nd = needle->data;
            const uint8_t *hd = haystack->data;
            ssize_t limit = hlen - nlen;
            ssize_t found = -1;
            for (ssize_t i = 0; i <= limit; i++) {
                if (memcmp(hd + i, nd, (size_t)nlen) == 0) {
                    found = i;
                    break;
                }
            }

            if (found == -1) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            } else {
                MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, found);
                if (r == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }
            break;
        }

        case OP_BYTES_INDEX_INT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *byte_val = (MenaiInteger *)frame_regs[src1];

            long target;
            if (MENAI_UNLIKELY(menai_integer_to_long(byte_val, &target) < 0)) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            if (target < 0 || target > 255) {
                vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE;
                goto error;
            }

            ssize_t blen = b->length;
            const uint8_t *data = b->data;
            ssize_t found = -1;
            for (ssize_t i = 0; i < blen; i++) {
                if (data[i] == (uint8_t)target) {
                    found = i;
                    break;
                }
            }

            if (found == -1) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            } else {
                MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, found);
                if (r == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }
            break;
        }

        case OP_BYTES_LT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_bytes_compare(a, b) < 0);
            break;
        }

        case OP_BYTES_GT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_bytes_compare(a, b) > 0);
            break;
        }

        case OP_BYTES_LTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_bytes_compare(a, b) <= 0);
            break;
        }

        case OP_BYTES_GTE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *a = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_bytes_compare(a, b) >= 0);
            break;
        }

        case OP_BYTES_READ_U8: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1];
            ssize_t offset;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t blen = b->length;
            if (offset < 0 || offset >= blen) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            MenaiInteger *r = alloc_menai_integer_from_long(vs, (long)b->data[offset]);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_BYTES_READ_I8: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1];
            ssize_t offset;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t blen = b->length;
            if (offset < 0 || offset >= blen) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            int8_t val = (int8_t)b->data[offset];
            MenaiInteger *r = alloc_menai_integer_from_long(vs, (long)val);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        /* Multi-byte read helpers using a shared pattern.  Each reads N bytes
         * at the given offset, assembles them in the specified endianness,
         * and returns an integer.  */
#define BYTES_READ_MULTI(opcode_name, width, is_signed, le) \
        case opcode_name: { \
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK); \
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0]; \
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK); \
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1]; \
            ssize_t offset; \
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) { \
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS; \
                goto error; \
            } \
\
            ssize_t blen = b->length; \
            if (offset < 0 || offset + (width) > blen) { \
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS; \
                goto error; \
            } \
\
            const uint8_t *d = b->data + offset; \
            unsigned long long uval = 0; \
            if (le) { \
                for (int _i = 0; _i < (width); _i++) { \
                    uval |= ((unsigned long long)d[_i]) << (_i * 8); \
                } \
            } else { \
                for (int _i = 0; _i < (width); _i++) { \
                    uval = (uval << 8) | d[_i]; \
                } \
            } \
\
            if (is_signed) { \
                long long sval; \
                if ((width) == 8) { \
                    /* Full 64-bit width already occupies every bit; no \
                     * extension mask is needed (and shifting a 64-bit \
                     * value left by 64 would itself be undefined behavior). */ \
                    sval = (long long)uval; \
                } else { \
                    unsigned long long sign_bit = 1ULL << ((width) * 8 - 1); \
                    if (uval & sign_bit) { \
                        sval = (long long)(uval | ~(~0ULL >> (64 - (width) * 8))); \
                    } else { \
                        sval = (long long)uval; \
                    } \
                } \
\
                MenaiInteger *r = alloc_menai_integer_from_long_long(vs, sval); \
                if (r == NULL) { \
                    goto error; \
                } \
\
                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
            } else { \
                if (uval > (unsigned long long)LONG_MAX) { \
                    /* Value exceeds LONG_MAX; would misrepresent as \
                     * negative (or wrap) if narrowed to long. */ \
                    MenaiBigInt big; \
                    menai_bigint_init(&big); \
                    vm_err = menai_bigint_from_unsigned_long_long((unsigned long long)uval, &big); \
                    if (MENAI_UNLIKELY(vm_err < 0)) { \
                        goto error; \
                    } \
\
                    MenaiInteger *r = alloc_menai_integer_from_bigint(vs, big); \
                    if (r == NULL) { \
                        goto error; \
                    } \
\
                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
                } else { \
                    MenaiInteger *r = alloc_menai_integer_from_long_long(vs, (long long)uval); \
                    if (r == NULL) { \
                        goto error; \
                    } \
\
                    menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
                } \
            } \
            break; \
        }

        BYTES_READ_MULTI(OP_BYTES_READ_U16_LE, 2, 0, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_U24_LE, 3, 0, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_U32_LE, 4, 0, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_U64_LE, 8, 0, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_U16_BE, 2, 0, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_U24_BE, 3, 0, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_U32_BE, 4, 0, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_U64_BE, 8, 0, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_I16_LE, 2, 1, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_I24_LE, 3, 1, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_I32_LE, 4, 1, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_I64_LE, 8, 1, 1)
        BYTES_READ_MULTI(OP_BYTES_READ_I16_BE, 2, 1, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_I24_BE, 3, 1, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_I32_BE, 4, 1, 0)
        BYTES_READ_MULTI(OP_BYTES_READ_I64_BE, 8, 1, 0)

#undef BYTES_READ_MULTI

        /*
         * Multi-byte append helpers using a shared pattern.  Each takes bytes
         * and an integer value, encodes the value into N bytes in the specified
         * endianness, appends them, and returns the new bytes.
         * For signed variants the value range check uses the signed range.
         */
#define BYTES_APPEND_MULTI(opcode_name, width, is_signed, le) \
        case opcode_name: { \
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK); \
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0]; \
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK); \
            MenaiInteger *v = (MenaiInteger *)frame_regs[src1]; \
            long long val; \
            if (is_signed) { \
                if (MENAI_UNLIKELY(menai_integer_to_long_long(v, &val) < 0)) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
\
                if ((width) < (int)sizeof(long long)) { \
                   long long max_val = (long long)((1ULL << ((width) * 8 - 1)) - 1); \
                   if (val < -max_val - 1 || val > max_val) { \
                       vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                       goto error; \
                   } \
                } \
                unsigned long long uval = (unsigned long long)val; \
                MenaiBytes *r = alloc_menai_bytes_from_append_multi(vs, b, uval, (width), le); \
                if (r == NULL) { \
                    goto error; \
                } \
\
                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
            } else { \
                unsigned long long uval_ull; \
                if (menai_integer_to_unsigned_long_long(v, &uval_ull) < 0) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
\
                if ((width) < (int)sizeof(unsigned long long) && uval_ull > (~0ULL >> (64 - (width) * 8))) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
\
                MenaiBytes *r = alloc_menai_bytes_from_append_multi(vs, b, uval_ull, (width), le); \
                if (r == NULL) { \
                    goto error; \
                } \
\
                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
            } \
            break; \
        }

        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U16_LE, 2, 0, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U16_BE, 2, 0, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U24_LE, 3, 0, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U24_BE, 3, 0, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U32_LE, 4, 0, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U32_BE, 4, 0, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U64_LE, 8, 0, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_U64_BE, 8, 0, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I8, 1, 1, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I16_LE, 2, 1, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I16_BE, 2, 1, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I24_LE, 3, 1, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I24_BE, 3, 1, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I32_LE, 4, 1, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I32_BE, 4, 1, 0)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I64_LE, 8, 1, 1)
        BYTES_APPEND_MULTI(OP_BYTES_APPEND_I64_BE, 8, 1, 0)

#undef BYTES_APPEND_MULTI

        /*
         * Multi-byte write helpers.  Each takes bytes, offset, and integer value,
         * writes the encoded value at the offset, and returns the new bytes.
         * For signed variants the value range check uses the signed range.
         */
#define BYTES_WRITE_MULTI(opcode_name, width, is_signed, le) \
        case opcode_name: { \
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK); \
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0]; \
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK); \
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1]; \
            int src2 = (int)(word & FIELD_MASK); \
            MenaiInteger *v = (MenaiInteger *)frame_regs[src2]; \
            ssize_t offset; \
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) { \
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS; \
                goto error; \
            } \
\
            ssize_t blen = b->length; \
            if (offset < 0 || offset + (width) > blen) { \
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS; \
                goto error; \
            } \
\
            unsigned long long uval_ull; \
            if (is_signed) { \
                long long val; \
                if (menai_integer_to_long_long(v, &val) < 0) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
\
                if ((width) < (int)sizeof(long long)) { \
                    long long max_val = (long long)((1ULL << ((width) * 8 - 1)) - 1); \
                    if (val < -max_val - 1 || val > max_val) { \
                        vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                        goto error; \
                    } \
                } \
                uval_ull = (unsigned long long)val; \
            } else { \
                if (menai_integer_to_unsigned_long_long(v, &uval_ull) < 0) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
\
                if ((width) < (int)sizeof(unsigned long long) && \
                        uval_ull > (~0ULL >> (64 - (width) * 8))) { \
                    vm_err = MENAI_ERR_VALUE_OUT_OF_RANGE; \
                    goto error; \
                } \
            } \
            MenaiBytes *r = alloc_menai_bytes_from_write_multi(vs, b, offset, uval_ull, (width), le); \
            if (r == NULL) { \
                goto error; \
            } \
\
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r); \
            break; \
        }

        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U8, 1, 0, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U16_LE, 2, 0, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U16_BE, 2, 0, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U24_LE, 3, 0, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U24_BE, 3, 0, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U32_LE, 4, 0, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U32_BE, 4, 0, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U64_LE, 8, 0, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_U64_BE, 8, 0, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I8, 1, 1, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I16_LE, 2, 1, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I16_BE, 2, 1, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I24_LE, 3, 1, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I24_BE, 3, 1, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I32_LE, 4, 1, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I32_BE, 4, 1, 0)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I64_LE, 8, 1, 1)
        BYTES_WRITE_MULTI(OP_BYTES_WRITE_I64_BE, 8, 1, 0)

#undef BYTES_WRITE_MULTI

        /*
         * LEB128 read (unsigned).  Returns a two-element list (value next-offset).
         */
        case OP_BYTES_READ_ULEB128: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1];
            ssize_t offset;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t blen = b->length;
            if (offset < 0 || offset >= blen) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            const uint8_t *d = b->data;
            unsigned long long result = 0;
            int shift = 0;
            ssize_t pos = offset;
            int byte;
            do {
                if (pos >= blen) {
                    vm_err = MENAI_ERR_TRUNCATED_LEB128;
                    goto error;
                }
                byte = d[pos];
                result |= ((unsigned long long)(byte & 0x7F)) << shift;
                shift += 7;
                pos++;
            } while (byte & 0x80);

            MenaiInteger *val_result;
            if ((long long)result < 0) {
                /* Value exceeds LONG_MAX — use bigint path */
                MenaiBigInt big;
                menai_bigint_init(&big);
                vm_err = menai_bigint_from_unsigned_long_long(result, &big);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
                val_result = alloc_menai_integer_from_bigint(vs, big);
                if (val_result == NULL) {
                    goto error;
                }
            } else {
                val_result = alloc_menai_integer_from_long_long(vs, (long long)result);
                if (val_result == NULL) {
                    goto error;
                }
            }

            MenaiInteger *next_off = alloc_menai_integer_from_ssize_t(vs, pos);
            if (next_off == NULL) {
                menai_value_release(vs, (MenaiValue *)val_result);
                goto error;
            }

            MenaiList *lst = alloc_menai_list(vs, 2);
            if (lst == NULL) {
                menai_value_release(vs, (MenaiValue *)val_result);
                menai_value_release(vs, (MenaiValue *)next_off);
                goto error;
            }

            MenaiValue **elems = lst->elements;
            elems[0] = (MenaiValue *)val_result;
            elems[1] = (MenaiValue *)next_off;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)lst);
            break;
        }

        /*
         * LEB128 append (unsigned).  Encodes value as unsigned LEB128 and appends.
         */
        case OP_BYTES_APPEND_ULEB128: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *v = (MenaiInteger *)frame_regs[src1];
            unsigned long long uval;
            if (MENAI_UNLIKELY(menai_integer_to_unsigned_long_long(v, &uval) < 0)) {
                vm_err = MENAI_ERR_NEGATIVE_ARGUMENT;
                goto error;
            }
            uint8_t buf[10];
            int nbytes = 0;
            do {
                buf[nbytes++] = (uint8_t)(uval & 0x7F);
                uval >>= 7;
            } while (uval != 0);

            for (int i = 0; i < nbytes - 1; i++) {
                buf[i] |= 0x80;
            }

            MenaiBytes *result = b;
            menai_value_retain((MenaiValue *)result);
            for (int i = 0; i < nbytes; i++) {
                MenaiBytes *next = alloc_menai_bytes_from_append_u8(vs, result, buf[i]);
                menai_value_release(vs, (MenaiValue *)result);
                if (next == NULL) {
                    goto error;
                }
                result = next;
            }
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            break;
        }

        /*
         * LEB128 read (signed).  Returns a two-element list (value next-offset).
         */
        case OP_BYTES_READ_SLEB128: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *off_val = (MenaiInteger *)frame_regs[src1];
            ssize_t offset;
            if (MENAI_UNLIKELY(menai_integer_to_ssize_t(off_val, &offset) < 0)) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            ssize_t blen = b->length;
            if (offset < 0 || offset >= blen) {
                vm_err = MENAI_ERR_OFFSET_OUT_OF_BOUNDS;
                goto error;
            }

            const uint8_t *d = b->data;
            long long result = 0;
            int shift = 0;
            ssize_t pos = offset;
            int byte;
            do {
                if (pos >= blen) {
                    vm_err = MENAI_ERR_TRUNCATED_LEB128;
                    goto error;
                }
                byte = d[pos];
                result |= ((long long)(byte & 0x7F)) << shift;
                shift += 7;
                pos++;
            } while (byte & 0x80);

            /* Sign extend if the sign bit is set */
            if (shift < 64 && (byte & 0x40)) {
                result |= (long long)(-1LL) << shift;
            }

            MenaiInteger *val_result = alloc_menai_integer_from_long_long(vs, result);
            if (val_result == NULL) {
                goto error;
            }

            MenaiInteger *next_off = alloc_menai_integer_from_ssize_t(vs, pos);
            if (next_off == NULL) {
                menai_value_release(vs, (MenaiValue *)val_result);
                goto error;
            }

            MenaiList *lst = alloc_menai_list(vs, 2);
            if (lst == NULL) {
                menai_value_release(vs, (MenaiValue *)val_result);
                menai_value_release(vs, (MenaiValue *)next_off);
                goto error;
            }

            MenaiValue **elems = lst->elements;
            elems[0] = (MenaiValue *)val_result;
            elems[1] = (MenaiValue *)next_off;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)lst);
            break;
        }

        /*
         * LEB128 append (signed).  Encodes value as signed LEB128 and appends.
         */
        case OP_BYTES_APPEND_SLEB128: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiBytes *b = (MenaiBytes *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *v = (MenaiInteger *)frame_regs[src1];
            long long val;
            if (MENAI_UNLIKELY(menai_integer_to_long_long(v, &val) < 0)) {
                vm_err = MENAI_ERR_OVERFLOW;
                goto error;
            }
            uint8_t buf[10];
            int nbytes = 0;
            int more = 1;
            while (more) {
                uint8_t byte = (uint8_t)(val & 0x7F);
                val >>= 7;
                if ((val == 0 && !(byte & 0x40)) || (val == -1 && (byte & 0x40))) {
                    more = 0;
                } else {
                    byte |= 0x80;
                }
                buf[nbytes++] = byte;
            }

            MenaiBytes *result = b;
            menai_value_retain((MenaiValue *)result);
            for (int i = 0; i < nbytes; i++) {
                MenaiBytes *next = alloc_menai_bytes_from_append_u8(vs, result, buf[i]);
                menai_value_release(vs, (MenaiValue *)result);
                if (next == NULL) {
                    goto error;
                }
                result = next;
            }
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            break;
        }

        case OP_LIST_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_LIST(frame_regs[src0]));
            break;
        }

        case OP_LIST_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiList *b = (MenaiList *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_list_equal(a, b));
            break;
        }

        case OP_LIST_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiList *b = (MenaiList *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_list_equal(a, b));
            break;
        }

        case OP_LIST_NULL_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int is_null = (a->length == 0);
            bool_store(vs, frame_regs, dest, is_null);
            break;
        }

        case OP_LIST_LENGTH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, n);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_FIRST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            if (a->length == 0) {
                vm_err = MENAI_ERR_EMPTY_LIST;
                goto error;
            }

            menai_reg_set_borrow(vs, frame_regs, dest, a->elements[0]);
            break;
        }

        case OP_LIST_REST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            if (a->length == 0) {
                vm_err = MENAI_ERR_EMPTY_LIST;
                goto error;
            }

            MenaiList *r = alloc_menai_list(vs, 0);
            if (r == NULL) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiList *owner = (a->owner != NULL) ? a->owner : a;
            menai_value_retain((MenaiValue *)owner);
            r->owner = owner;
            r->elements = a->elements + 1;
            r->length = a->length - 1;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_LAST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            if (n == 0) {
                vm_err = MENAI_ERR_EMPTY_LIST;
                goto error;
            }

            menai_reg_set_borrow(vs, frame_regs, dest, a->elements[n - 1]);
            break;
        }

        case OP_LIST_REF: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];

            long idx_l;
            if (!b->is_big) {
                idx_l = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &idx_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t idx = (ssize_t)idx_l;
            ssize_t n = a->length;
            if (idx < 0 || idx >= n) {
                vm_err = MENAI_ERR_INDEX_OUT_OF_RANGE;
                goto error;
            }

            menai_reg_set_borrow(vs, frame_regs, dest, a->elements[idx]);
            break;
        }

        case OP_LIST_PREPEND: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiList *r = alloc_menai_list(vs, n + 1);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            MenaiValue **pre_arr = r->elements;
            pre_arr[0] = item;
            menai_value_retain(item);
            for (ssize_t i = 0; i < n; i++) {
                pre_arr[i + 1] = a->elements[i];
                menai_value_retain(pre_arr[i + 1]);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_APPEND: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiList *r = alloc_menai_list(vs, n + 1);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **app_arr = r->elements;
            for (ssize_t i = 0; i < n; i++) {
                app_arr[i] = a->elements[i];
                menai_value_retain(app_arr[i]);
            }

            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            app_arr[n] = item;
            menai_value_retain(item);
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_REVERSE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **rev_arr = r->elements;
            for (ssize_t i = 0; i < n; i++) {
                rev_arr[i] = a->elements[n - 1 - i];
                menai_value_retain(rev_arr[i]);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_CONCAT: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiList *b = (MenaiList *)frame_regs[src1];
            ssize_t na = a->length;
            ssize_t nb = b->length;
            ssize_t nc = na + nb;
            MenaiList *r = alloc_menai_list(vs, nc);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **cat_arr = r->elements;
            for (ssize_t i = 0; i < na; i++) {
                cat_arr[i] = a->elements[i];
                menai_value_retain(cat_arr[i]);
            }

            for (ssize_t i = 0; i < nb; i++) {
                cat_arr[na + i] = b->elements[i];
                menai_value_retain(cat_arr[na + i]);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_MEMBER_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            int mem_found = 0;
            for (ssize_t i = 0; i < a->length; i++) {
                int eq = menai_value_equal(a->elements[i], item);
                if (eq) {
                    mem_found = 1;
                    break;
                }
            }

            bool_store(vs, frame_regs, dest, mem_found);
            break;
        }

        case OP_LIST_INDEX: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            ssize_t n = a->length;
            ssize_t found = -1;
            for (ssize_t i = 0; i < n; i++) {
                int eq = menai_value_equal(a->elements[i], item);
                if (eq) {
                    found = i;
                    break;
                }
            }

            if (found == -1) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)menai_none(vs));
            } else {
                MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, found);
                if (r == NULL) {
                    goto error;
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }

            break;
        }

        case OP_LIST_SLICE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            int src2 = (int)(word & FIELD_MASK);
            MenaiInteger *c = (MenaiInteger *)frame_regs[src2];

            long start_l;
            if (!b->is_big) {
                start_l = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &start_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            long end_l;
            if (!c->is_big) {
                end_l = c->fixed;
            } else {
                vm_err = menai_bigint_to_long(&c->big, &end_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t start = (ssize_t)start_l;
            ssize_t end = (ssize_t)end_l;
            ssize_t n = a->length;
            if (start < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SLICE_INDEX;
                goto error;
            }

            if (end < 0) {
                vm_err = MENAI_ERR_NEGATIVE_SLICE_INDEX;
                goto error;
            }

            if (start > n) {
                vm_err = MENAI_ERR_SLICE_START_OUT_OF_RANGE;
                goto error;
            }

            if (end > n) {
                vm_err = MENAI_ERR_SLICE_END_OUT_OF_RANGE;
                goto error;
            }

            if (start > end) {
                vm_err = MENAI_ERR_SLICE_START_AFTER_END;
                goto error;
            }

            MenaiList *r = alloc_menai_list(vs, 0);
            if (r == NULL) {
                goto error;
            }

            MenaiList *owner = (a->owner != NULL) ? a->owner : a;
            menai_value_retain((MenaiValue *)owner);
            r->owner = owner;
            r->elements = a->elements + start;
            r->length = end - start;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_REMOVE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;

            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            MenaiValue **rm_arr = r->elements;
            ssize_t j = 0;
            for (ssize_t i = 0; i < n; i++) {
                MenaiValue *e = a->elements[i];
                int eq = menai_value_equal(e, item);
                if (!eq) {
                    menai_value_retain(e);
                    rm_arr[j++] = e;
                }
            }

            ((MenaiList *)r)->length = j;
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_LIST_TO_STRING: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiString *b = (MenaiString *)frame_regs[src1];

            /* Validate all elements are strings first. */
            ssize_t n = a->length;
            for (ssize_t i = 0; i < n; i++) {
                if (MENAI_UNLIKELY(!IS_MENAI_STRING(a->elements[i]))) {
                    vm_err = MENAI_ERR_LIST_TO_STRING_NOT_STRINGS;
                    goto error;
                }
            }

            /* Compute total output length. */
            ssize_t sep_len = b->length;
            const uint32_t *sep_data = b->data;
            ssize_t total = (n > 0) ? (n - 1) * sep_len : 0;
            for (ssize_t i = 0; i < n; i++) {
                MenaiString *elem = (MenaiString *)a->elements[i];
                total += elem->length;
            }

            uint32_t *lts_buf = total > 0 ? (uint32_t *)malloc((size_t)total * sizeof(uint32_t)) : NULL;
            if (total > 0 && !lts_buf) {
                goto error;
            }

            uint32_t *dst = lts_buf;
            for (ssize_t i = 0; i < n; i++) {
                if (i > 0 && sep_len > 0) {
                    memcpy(dst, sep_data, (size_t)sep_len * sizeof(uint32_t));
                    dst += sep_len;
                }

                MenaiString *elem = (MenaiString *)a->elements[i];
                ssize_t elen = elem->length;
                if (elen > 0) {
                    memcpy(dst, elem->data, (size_t)elen * sizeof(uint32_t));
                    dst += elen;
                }
            }

            MenaiString *r = alloc_menai_string(vs, total);
            if (r == NULL) {
                free(lts_buf);
                goto error;
            }

            memcpy(r->data, lts_buf, total * sizeof(uint32_t));
            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            free(lts_buf);
            break;
        }

        case OP_LIST_TO_SET: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiList *a = (MenaiList *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiSet *r = alloc_menai_set(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nelems = r->elements;
            hash_t *nhashes = r->hashes;
            MenaiHashTable lts_seen;
            vm_err = menai_ht_init(&lts_seen, n);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_value_release(vs, (MenaiValue *)r);
                goto error;
            }

            ssize_t out = 0;
            for (ssize_t i = 0; i < n; i++) {
                MenaiValue *elem = a->elements[i];
                hash_t h = menai_value_hash(elem);
                if (h == -1) {
                    menai_ht_final(&lts_seen);
                    for (ssize_t k = 0; k < out; k++) {
                        menai_value_release(vs, nelems[k]);
                    }

                    menai_value_release(vs, (MenaiValue *)r);
                    vm_err = MENAI_ERR_UNHASHABLE_KEY;
                    goto error;
                }

                ssize_t existing = menai_ht_lookup(&lts_seen, elem, h);
                if (existing < 0) {
                    menai_ht_insert(&lts_seen, elem, h, out);
                    menai_value_retain(elem);
                    nelems[out] = elem;
                    nhashes[out] = h;
                    out++;
                }
            }

            r->ht = lts_seen;
            r->length = out;

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_DICT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_DICT(frame_regs[src0]));
            break;
        }

        case OP_DICT_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiDict *b = (MenaiDict *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_dict_equal(a, b));
            break;
        }

        case OP_DICT_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiDict *b = (MenaiDict *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_dict_equal(a, b));
            break;
        }

        case OP_DICT_LENGTH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, a->length);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_DICT_KEYS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **dk_arr = r->elements;
            for (ssize_t i = 0; i < n; i++) {
                menai_value_retain(a->keys[i]);
                dk_arr[i] = a->keys[i];
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_DICT_VALUES: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            ssize_t n = a->length;
            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **dv_arr = r->elements;
            for (ssize_t i = 0; i < n; i++) {
                menai_value_retain(a->values[i]);
                dv_arr[i] = a->values[i];
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_DICT_HAS_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *key = frame_regs[src1];
            hash_t h = menai_value_hash(key);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            int has = (menai_ht_lookup(&a->ht, key, h) >= 0);
            bool_store(vs, frame_regs, dest, has);
            break;
        }

        case OP_DICT_GET: {
            /* src0=dict, src1=key, src2=default */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *key = frame_regs[src1];
            hash_t h = menai_value_hash(key);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t idx = menai_ht_lookup(&a->ht, key, h);
            if (idx >= 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, a->values[idx]);
            } else {
                int src2 = (int)(word & FIELD_MASK);
                menai_reg_set_borrow(vs, frame_regs, dest, frame_regs[src2]);
            }

            break;
        }

        case OP_DICT_SET: {
            /* src0=dict, src1=key, src2=value */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *key = frame_regs[src1];
            hash_t h = menai_value_hash(key);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t replace_idx = menai_ht_lookup(&a->ht, key, h);
            ssize_t n = a->length;
            ssize_t new_n = (replace_idx >= 0) ? n : n + 1;
            MenaiDict *result = alloc_menai_dict(vs, new_n);
            if (!result) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nkeys = result->keys;
            MenaiValue **nvals = result->values;
            hash_t *nhashes = result->hashes;

            int src2 = (int)(word & FIELD_MASK);
            MenaiValue *val = frame_regs[src2];
            if (replace_idx >= 0) {
                for (ssize_t i = 0; i < n; i++) {
                    if (i == replace_idx) {
                        menai_value_retain(key);
                        nkeys[i] = key;
                        menai_value_retain(val);
                        nvals[i] = val;
                        nhashes[i] = h;
                    } else {
                        menai_value_retain(a->keys[i]);
                        nkeys[i] = a->keys[i];
                        menai_value_retain(a->values[i]);
                        nvals[i] = a->values[i];
                        nhashes[i] = a->hashes[i];
                    }
                }
            } else {
                for (ssize_t i = 0; i < n; i++) {
                    menai_value_retain(a->keys[i]);
                    nkeys[i] = a->keys[i];
                    menai_value_retain(a->values[i]);
                    nvals[i] = a->values[i];
                    nhashes[i] = a->hashes[i];
                }

                menai_value_retain(key);
                nkeys[n] = key;
                menai_value_retain(val);
                nvals[n] = val;
                nhashes[n] = h;
            }

            if (menai_ht_init(&result->ht, new_n) < 0) {
                result->length = new_n;
                menai_value_release(vs, (MenaiValue *)result);
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            for (ssize_t i = 0; i < new_n; i++) {
                menai_ht_insert(&result->ht, nkeys[i], nhashes[i], i);
            }

            result->length = new_n;

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)result);
            break;
        }

        case OP_DICT_REMOVE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *key = frame_regs[src1];
            hash_t h = menai_value_hash(key);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t remove_idx = menai_ht_lookup(&a->ht, key, h);
            if (remove_idx < 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)a);
                break;
            }

            ssize_t n = a->length;
            ssize_t new_n = n - 1;
            MenaiDict *r = alloc_menai_dict(vs, new_n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nkeys = r->keys;
            MenaiValue **nvals = r->values;
            hash_t *nhashes = r->hashes;

            for (ssize_t i = 0, j = 0; i < n; i++) {
                if (i == remove_idx) {
                    continue;
                }

                menai_value_retain(a->keys[i]);
                nkeys[j] = a->keys[i];
                menai_value_retain(a->values[i]);
                nvals[j] = a->values[i];
                nhashes[j] = a->hashes[i];
                j++;
            }

            if (menai_ht_init(&r->ht, new_n) < 0) {
                r->length = new_n;
                menai_value_release(vs, (MenaiValue *)r);
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            for (ssize_t i = 0; i < new_n; i++) {
                menai_ht_insert(&r->ht, nkeys[i], nhashes[i], i);
            }

            r->length = new_n;

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_DICT_MERGE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiDict *a = (MenaiDict *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiDict *b = (MenaiDict *)frame_regs[src1];
            ssize_t na = a->length;
            ssize_t nb = b->length;
            ssize_t cap = na + nb;
            MenaiDict *r = alloc_menai_dict(vs, cap);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nkeys = r->keys;
            MenaiValue **nvals = r->values;
            hash_t *nhashes = r->hashes;

            ssize_t out = 0;
            /* Add a's entries, using b's value where b overrides */
            for (ssize_t i = 0; i < na; i++) {
                ssize_t bi = menai_ht_lookup(&b->ht, a->keys[i], a->hashes[i]);
                menai_value_retain(a->keys[i]);
                nkeys[out] = a->keys[i];
                nhashes[out] = a->hashes[i];
                if (bi >= 0) {
                    menai_value_retain(b->values[bi]);
                    nvals[out] = b->values[bi];
                } else {
                    menai_value_retain(a->values[i]);
                    nvals[out] = a->values[i];
                }

                out++;
            }

            /* Add b's entries not in a */
            for (ssize_t i = 0; i < nb; i++) {
                ssize_t ai = menai_ht_lookup(&a->ht, b->keys[i], b->hashes[i]);
                if (ai < 0) {
                    menai_value_retain(b->keys[i]);
                    nkeys[out] = b->keys[i];
                    menai_value_retain(b->values[i]);
                    nvals[out] = b->values[i];
                    nhashes[out] = b->hashes[i];
                    out++;
                }
            }

            if (menai_ht_init(&r->ht, out) < 0) {
                r->length = out;
                menai_value_release(vs, (MenaiValue *)r);
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            for (ssize_t i = 0; i < out; i++) {
                menai_ht_insert(&r->ht, nkeys[i], nhashes[i], i);
            }

            r->length = out;

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_SET(frame_regs[src0]));
            break;
        }

        case OP_SET_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_set_equal(a, b));
            break;
        }

        case OP_SET_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_set_equal(a, b));
            break;
        }

        case OP_SET_LENGTH: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            MenaiInteger *r = alloc_menai_integer_from_ssize_t(vs, a->length);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_MEMBER_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            hash_t h = menai_value_hash(item);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t idx = menai_ht_lookup(&a->ht, item, h);
            bool_store(vs, frame_regs, dest, idx >= 0);
            break;
        }

        case OP_SET_ADD: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            hash_t h = menai_value_hash(item);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t existing = menai_ht_lookup(&a->ht, item, h);
            if (existing >= 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)a);
            } else {
                ssize_t n = a->length;
                MenaiSet *r = alloc_menai_set(vs, n + 1);
                if (!r) {
                    vm_err = MENAI_ERR_NOMEM;
                    goto error;
                }

                MenaiValue **nelems = r->elements;
                hash_t *nhashes = r->hashes;
                for (ssize_t i = 0; i < n; i++) {
                    menai_value_retain(a->elements[i]);
                    nelems[i] = a->elements[i];
                    nhashes[i] = a->hashes[i];
                }

                menai_value_retain(item);
                nelems[n] = item;
                nhashes[n] = h;
                r->length = n + 1;
                vm_err = menai_ht_init(&r->ht, n + 1);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                for (ssize_t i = 0; i < n + 1; i++) {
                    menai_ht_insert(&r->ht, nelems[i], nhashes[i], i);
                }

                menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            }

            break;
        }

        case OP_SET_REMOVE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *item = frame_regs[src1];
            hash_t h = menai_value_hash(item);
            if (h == -1) {
                vm_err = MENAI_ERR_UNHASHABLE_KEY;
                goto error;
            }

            ssize_t remove_idx = menai_ht_lookup(&a->ht, item, h);
            if (remove_idx < 0) {
                menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)a);
                break;
            }

            ssize_t n = a->length;
            ssize_t new_n = n - 1;
            MenaiSet *r = alloc_menai_set(vs, new_n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nelems = r->elements;
            hash_t *nhashes = r->hashes;
            for (ssize_t i = 0, j = 0; i < n; i++) {
                if (i == remove_idx) {
                    continue;
                }

                menai_value_retain(a->elements[i]);
                nelems[j] = a->elements[i];
                nhashes[j] = a->hashes[i];
                j++;
            }

            r->length = new_n;
            vm_err = menai_ht_init(&r->ht, new_n);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_value_release(vs, (MenaiValue *)r);
                goto error;
            }

            for (ssize_t i = 0; i < new_n; i++) {
                menai_ht_insert(&r->ht, nelems[i], nhashes[i], i);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_UNION: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            ssize_t na = a->length;
            ssize_t nb = b->length;
            ssize_t cap = na + nb;
            MenaiSet *r = alloc_menai_set(vs, cap);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nelems = r->elements;
            hash_t *nhashes = r->hashes;
            ssize_t out = 0;
            for (ssize_t i = 0; i < na; i++) {
                menai_value_retain(a->elements[i]);
                nelems[out] = a->elements[i];
                nhashes[out] = a->hashes[i];
                out++;
            }

            for (ssize_t i = 0; i < nb; i++) {
                ssize_t in_a = menai_ht_lookup(&a->ht, b->elements[i], b->hashes[i]);
                if (in_a < 0) {
                    menai_value_retain(b->elements[i]);
                    nelems[out] = b->elements[i];
                    nhashes[out] = b->hashes[i];
                    out++;
                }
            }

            r->length = out;
            vm_err = menai_ht_init(&r->ht, out);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_value_release(vs, (MenaiValue *)r);
                goto error;
            }

            for (ssize_t i = 0; i < out; i++) {
                menai_ht_insert(&r->ht, nelems[i], nhashes[i], i);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_INTERSECTION: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            ssize_t na = a->length;
            MenaiSet *r = alloc_menai_set(vs, na);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nelems = r->elements;
            hash_t *nhashes = r->hashes;
            ssize_t out = 0;
            for (ssize_t i = 0; i < na; i++) {
                ssize_t in_b = menai_ht_lookup(&b->ht, a->elements[i], a->hashes[i]);
                if (in_b >= 0) {
                    menai_value_retain(a->elements[i]);
                    nelems[out] = a->elements[i];
                    nhashes[out] = a->hashes[i];
                    out++;
                }
            }

            r->length = out;
            vm_err = menai_ht_init(&r->ht, out);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_value_release(vs, (MenaiValue *)r);
                goto error;
            }

            for (ssize_t i = 0; i < out; i++) {
                menai_ht_insert(&r->ht, nelems[i], nhashes[i], i);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_DIFFERENCE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            ssize_t na = a->length;
            MenaiSet *r = alloc_menai_set(vs, na);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **nelems = r->elements;
            hash_t *nhashes = r->hashes;
            ssize_t out = 0;
            for (ssize_t i = 0; i < na; i++) {
                ssize_t in_b = menai_ht_lookup(&b->ht, a->elements[i], a->hashes[i]);
                if (in_b < 0) {
                    menai_value_retain(a->elements[i]);
                    nelems[out] = a->elements[i];
                    nhashes[out] = a->hashes[i];
                    out++;
                }
            }

            r->length = out;
            vm_err = menai_ht_init(&r->ht, out);
            if (MENAI_UNLIKELY(vm_err < 0)) {
                menai_value_release(vs, (MenaiValue *)r);
                goto error;
            }

            for (ssize_t i = 0; i < out; i++) {
                menai_ht_insert(&r->ht, nelems[i], nhashes[i], i);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_SET_SUBSET_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSet *b = (MenaiSet *)frame_regs[src1];
            if (a->length > b->length) {
                bool_store(vs, frame_regs, dest, 0);
                break;
            }

            int is_subset = 1;
            for (ssize_t i = 0; i < a->length; i++) {
                ssize_t idx = menai_ht_lookup(&b->ht, a->elements[i], a->hashes[i]);
                if (idx < 0) {
                    is_subset = 0;
                    break;
                }
            }

            bool_store(vs, frame_regs, dest, is_subset);
            break;
        }

        case OP_SET_TO_LIST: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiSet *a = (MenaiSet *)frame_regs[src0];
            ssize_t set_n = a->length;
            MenaiList *r = alloc_menai_list(vs, set_n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **stl_arr = r->elements;
            for (ssize_t i = 0; i < set_n; i++) {
                menai_value_retain(a->elements[i]);
                stl_arr[i] = a->elements[i];
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_RANGE: {
            /* src0=start, src1=end, src2=step — all integers */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiInteger *a = (MenaiInteger *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *b = (MenaiInteger *)frame_regs[src1];
            int src2 = (int)(word & FIELD_MASK);
            MenaiInteger *c = (MenaiInteger *)frame_regs[src2];
            long start, end, step;
            if (!a->is_big) {
                start = a->fixed;
            } else {
                vm_err = menai_bigint_to_long(&a->big, &start);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (!b->is_big) {
                end = b->fixed;
            } else {
                vm_err = menai_bigint_to_long(&b->big, &end);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (!c->is_big) {
                step = c->fixed;
            } else {
                vm_err = menai_bigint_to_long(&c->big, &step);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            if (step == 0) {
                vm_err = MENAI_ERR_RANGE_ZERO_STEP;
                goto error;
            }

            /* Compute length */
            ssize_t n = 0;
            if (step > 0 && end > start) {
                n = (end - start + step - 1) / step;
            } else if (step < 0 && end < start) {
                n = (start - end - step - 1) / (-step);
            }

            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **rng_arr = r->elements;
            long val = start;
            for (ssize_t i = 0; i < n; i++) {
                MenaiInteger *mi = alloc_menai_integer_from_long(vs, val);
                if (mi == NULL) {
                    for (ssize_t k = 0; k < i; k++) {
                        menai_value_release(vs, rng_arr[k]);
                    }

                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                rng_arr[i] = (MenaiValue *)mi;
                val += step;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_MAKE_LIST: {
            /*
             * MAKE_LIST src0, src1:
             * src0 = base slot of outgoing zone (absolute slot index).
             * src1 = element count.
             * Elements are in slots src0..src0+n-1.
             */
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            int n = src1;
            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue **lst_arr = r->elements;
            for (int i = 0; i < n; i++) {
                lst_arr[i] = frame_regs[src0 + i];
                menai_value_retain(lst_arr[i]);
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_MAKE_SET: {
            /*
             * MAKE_SET src0, src1:
             * src0 = base slot of outgoing zone (absolute slot index).
             * src1 = element count.
             * Elements are in slots src0..src0+n-1.
             */
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            int n = src1;
            MenaiSet *r = alloc_menai_set(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            for (int i = 0; i < n; i++) {
                MenaiValue *elem = frame_regs[src0 + i];
                hash_t h = menai_value_hash(elem);
                if (h == -1) {
                    vm_err = MENAI_ERR_UNHASHABLE_KEY;
                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                menai_value_retain(elem);
                r->elements[i] = elem;
                r->hashes[i] = h;
            }

            r->length = n;
            if (n > 0) {
                vm_err = menai_ht_init(&r->ht, n);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                for (ssize_t i = 0; i < n; i++) {
                    menai_ht_insert(&r->ht, r->elements[i], r->hashes[i], i);
                }
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_MAKE_DICT: {
            /*
             * MAKE_DICT src0, src1:
             * src0 = base slot of outgoing zone (absolute slot index).
             * src1 = pair count.
             * Pairs are interleaved as k0, v0, k1, v1, ... in slots src0..src0+n*2-1.
             */
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            int n = src1;
            MenaiDict *r = alloc_menai_dict(vs, (ssize_t)n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **keys = r->keys;
            MenaiValue **values = r->values;
            hash_t *hashes = r->hashes;

            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            for (int i = 0; i < n; i++) {
                MenaiValue *k = frame_regs[src0 + i * 2];
                MenaiValue *v = frame_regs[src0 + i * 2 + 1];
                hash_t h = menai_value_hash(k);
                if (h == -1) {
                    vm_err = MENAI_ERR_UNHASHABLE_KEY;
                    r->length = (ssize_t)i;
                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                menai_value_retain(k);
                menai_value_retain(v);
                keys[i] = k;
                values[i] = v;
                hashes[i] = h;
            }

            if (menai_ht_init(&r->ht, (ssize_t)n) < 0) {
                r->length = (ssize_t)n;
                menai_value_release(vs, (MenaiValue *)r);
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            for (int i = 0; i < n; i++) {
                menai_ht_insert(&r->ht, keys[i], hashes[i], (ssize_t)i);
            }

            r->length = (ssize_t)n;

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_MAKE_STRUCT: {
            /*
             * MAKE_STRUCT src0, src1:
             * src0 = absolute slot of MenaiStructType descriptor in outgoing zone.
             * src1 = field count. Fields are in slots src0+1..src0+n_fields.
             */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStructType *struct_type = (MenaiStructType *)frame_regs[src0];
            int n_fields = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStruct *instance = alloc_menai_struct(vs, struct_type, &frame_regs[src0 + 1], n_fields);
            if (instance == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)instance);
            break;
        }

        case OP_STRUCT_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_STRUCT(frame_regs[src0]));
            break;
        }

        case OP_STRUCT_IS_INSTANCE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *sval = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStructType *stype = (MenaiStructType *)frame_regs[src1];
            int tag_a = ((MenaiStructType *)sval->struct_type)->tag;
            int tag_b = stype->tag;
            bool_store(vs, frame_regs, dest, tag_a == tag_b);
            break;
        }

        case OP_STRUCT_GET: {
            /* src1 holds a MenaiSymbol field name */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *sval = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSymbol *field_sym = (MenaiSymbol *)frame_regs[src1];
            MenaiStructType *stype = sval->struct_type;
            MenaiString *field_name = field_sym->name;
            hash_t h = menai_string_hash(field_name);
            int fi = (int)menai_ht_lookup(&stype->field_ht, (MenaiValue *)field_name, h);

            if (fi < 0) {
                vm_err = MENAI_ERR_STRUCT_FIELD_NOT_FOUND;
                goto error;
            }

            MenaiValue *fv = sval->items[fi];
            menai_reg_set_borrow(vs, frame_regs, dest, fv);
            break;
        }

        case OP_STRUCT_REF: {
            /* src1 holds a MenaiInteger field index */
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiValue *val = frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiValue *fidx = frame_regs[src1];
            MenaiInteger *fi_io = (MenaiInteger *)fidx;
            long fi_l;
            if (!fi_io->is_big) {
                fi_l = fi_io->fixed;
            } else {
                vm_err = menai_bigint_to_long(&fi_io->big, &fi_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t fi = (ssize_t)fi_l;
            ssize_t nf = ((MenaiStruct *)val)->nfields;
            if (fi < 0 || fi >= nf) {
                vm_err = MENAI_ERR_INDEX_OUT_OF_RANGE;
                goto error;
            }

            MenaiValue *fv = ((MenaiStruct *)val)->items[fi];
            menai_reg_set_borrow(vs, frame_regs, dest, fv);
            break;
        }

        case OP_STRUCT_SET: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *sval = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiSymbol *field_sym = (MenaiSymbol *)frame_regs[src1];
            MenaiStructType *stype = sval->struct_type;
            MenaiString *field_name = field_sym->name;
            hash_t h = menai_string_hash(field_name);
            int fi = (int)menai_ht_lookup(&stype->field_ht, (MenaiValue *)field_name, h);
            if (fi < 0) {
                vm_err = MENAI_ERR_STRUCT_FIELD_NOT_FOUND;
                goto error;
            }

            ssize_t nf = sval->nfields;
            MenaiValue **tmp = (MenaiValue **)malloc(nf * sizeof(MenaiValue *));
            if (!tmp) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src2 = (int)(word & FIELD_MASK);
            MenaiValue *new_val = frame_regs[src2];
            for (ssize_t i = 0; i < nf; i++) {
                tmp[i] = (i == fi) ? new_val : sval->items[i];
            }

            MenaiStruct *r = alloc_menai_struct(vs, stype, tmp, nf);
            free(tmp);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRUCT_SET_REF: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *sval = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiInteger *fi_io = (MenaiInteger *)frame_regs[src1];

            long fi_l;
            if (!fi_io->is_big) {
                fi_l = fi_io->fixed;
            } else {
                vm_err = menai_bigint_to_long(&fi_io->big, &fi_l);
                if (MENAI_UNLIKELY(vm_err < 0)) {
                    goto error;
                }
            }

            ssize_t fi = (ssize_t)fi_l;
            ssize_t nf = sval->nfields;
            if (fi < 0 || fi >= nf) {
                vm_err = MENAI_ERR_INDEX_OUT_OF_RANGE;
                goto error;
            }

            MenaiStructType *stype = sval->struct_type;
            MenaiValue **tmp = (MenaiValue **)malloc(nf * sizeof(MenaiValue *));
            if (!tmp) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            int src2 = (int)(word & FIELD_MASK);
            MenaiValue *new_val = frame_regs[src2];
            for (ssize_t i = 0; i < nf; i++) {
                tmp[i] = (i == fi) ? new_val : sval->items[i];
            }

            MenaiStruct *r = alloc_menai_struct(vs, stype, tmp, nf);
            free(tmp);
            if (r == NULL) {
                goto error;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRUCT_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *a = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStruct *b = (MenaiStruct *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_struct_equal(a, b));
            break;
        }

        case OP_STRUCT_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *a = (MenaiStruct *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStruct *b = (MenaiStruct *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_struct_equal(a, b));
            break;
        }

        case OP_STRUCT_TYPE: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStruct *val = (MenaiStruct *)frame_regs[src0];
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)val->struct_type);
            break;
        }

        case OP_STRUCTTYPE_NAME: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStructType *val = (MenaiStructType *)frame_regs[src0];
            menai_reg_set_borrow(vs, frame_regs, dest, (MenaiValue *)val->name);
            break;
        }

        case OP_STRUCTTYPE_FIELDS: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStructType *st = (MenaiStructType *)frame_regs[src0];
            int n = st->nfields;
            MenaiList *r = alloc_menai_list(vs, n);
            if (!r) {
                vm_err = MENAI_ERR_NOMEM;
                goto error;
            }

            MenaiValue **sf_arr = r->elements;
            for (int i = 0; i < n; i++) {
                MenaiSymbol *sym = alloc_menai_symbol(vs, st->fields[i].name);
                if (sym == NULL) {
                    for (int k = 0; k < i; k++) {
                        menai_value_release(vs, sf_arr[k]);
                    }

                    menai_value_release(vs, (MenaiValue *)r);
                    goto error;
                }

                sf_arr[i] = (MenaiValue *)sym;
            }

            menai_reg_set_own(vs, frame_regs, dest, (MenaiValue *)r);
            break;
        }

        case OP_STRUCTTYPE_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            bool_store(vs, frame_regs, dest, IS_MENAI_STRUCTTYPE(frame_regs[src0]));
            break;
        }

        case OP_STRUCTTYPE_EQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStructType *a = (MenaiStructType *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStructType *b = (MenaiStructType *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, menai_structtype_equal(a, b));
            break;
        }

        case OP_STRUCTTYPE_NEQ_P: {
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK);
            MenaiStructType *a = (MenaiStructType *)frame_regs[src0];
            int src1 = (int)((word >> SRC1_SHIFT) & FIELD_MASK);
            MenaiStructType *b = (MenaiStructType *)frame_regs[src1];
            bool_store(vs, frame_regs, dest, !menai_structtype_equal(a, b));
            break;
        }

        /*
         * Type guard opcodes.
         *
         * Each checks the type of r_src0 and raises MENAI_ERR_TYPE_MISMATCH
         * if the type does not match.  Otherwise the instruction is a no-op.
         * The operational opcodes below rely on these guards having verified
         * operand types and skip their own type checks for performance.
         */
        #define DEFINE_ASSERT_OP(name, type_check) \
        case OP_ASSERT_##name: { \
            int src0 = (int)((word >> SRC0_SHIFT) & FIELD_MASK); \
            MenaiValue *v = frame_regs[src0]; \
            if (MENAI_UNLIKELY(!(type_check))) { \
                vm_err = MENAI_ERR_TYPE_MISMATCH; \
                goto error; \
            } \
            break; \
        }

        DEFINE_ASSERT_OP(NONE, IS_MENAI_NONE(v))
        DEFINE_ASSERT_OP(BOOLEAN, IS_MENAI_BOOLEAN(v))
        DEFINE_ASSERT_OP(INTEGER, IS_MENAI_INTEGER(v))
        DEFINE_ASSERT_OP(FLOAT, IS_MENAI_FLOAT(v))
        DEFINE_ASSERT_OP(COMPLEX, IS_MENAI_COMPLEX(v))
        DEFINE_ASSERT_OP(STRING, IS_MENAI_STRING(v))
        DEFINE_ASSERT_OP(SYMBOL, IS_MENAI_SYMBOL(v))
        DEFINE_ASSERT_OP(LIST, IS_MENAI_LIST(v))
        DEFINE_ASSERT_OP(DICT, IS_MENAI_DICT(v))
        DEFINE_ASSERT_OP(SET, IS_MENAI_SET(v))
        DEFINE_ASSERT_OP(FUNCTION, IS_MENAI_FUNCTION(v))
        DEFINE_ASSERT_OP(BYTES, IS_MENAI_BYTES(v))
        DEFINE_ASSERT_OP(STRUCT, IS_MENAI_STRUCT(v))
        DEFINE_ASSERT_OP(STRUCTTYPE, IS_MENAI_STRUCTTYPE(v))

        #undef DEFINE_ASSERT_OP

        default:
            vm_err = MENAI_ERR_UNIMPLEMENTED_OPCODE;
            goto error;
        }

        continue;

error:
        /* Release all live frames above the sentinel. */
        for (int d = frame_depth; d >= 1; d--) {
            if (frames[d].code_obj) {
                menai_code_object_release(vs, frames[d].code_obj);
            }
        }

        vs->error.code = vm_err;
        vs->error.opcode = opcode;
        vs->error.ip = cur_ip;
        vs->error.call_depth = frame_depth;
        vs->error.user_message = vm_user_message;
        return NULL;
    }
}

/*
 * menai_vm_cancel — atomically set the cancellation flag in the VM state.
 * Thread-safe: may be called from a different thread while the VM is
 * executing (the GIL is released during execution).
 */
void
menai_vm_cancel(MenaiVMState *vs)
{
    _menai_atomic_store((_menai_atomic_int *)&vs->_cancel_flag, 1);
}

/*
 * menai_vm_execute_native — native VM entry point.
 *
 * Executes code using the prelude globals stored in the VM state and an
 * optional extra globals table (or NULL).  Returns a new reference to the
 * result, or NULL on error.  On error, *out_error is filled in.
 */
MenaiValue *
menai_vm_execute_native(MenaiVMState *vs, MenaiCodeObject *code, const GlobalsTable *extra_globals)
{
    vs->error.code = MENAI_OK;
    vs->error.opcode = 0;
    vs->error.ip = 0;
    vs->error.call_depth = 0;
    vs->error.user_message = NULL;

    int max_locals = menai_code_object_max_locals(code);
    if (vs->_globals_valid) {
        for (ssize_t i = 0; i < vs->_globals.count; i++) {
            MenaiValue *val = vs->_globals.entries[i].value;
            if (IS_MENAI_FUNCTION(val)) {
                int n = menai_code_object_max_locals(((MenaiFunction *)val)->bytecode);
                if (n > max_locals) {
                    max_locals = n;
                }
            }
        }
    }

    size_t num_regs = (size_t)(MAX_FRAME_DEPTH + 1) * max_locals;
    MenaiValue **regs = (MenaiValue **)malloc(num_regs * sizeof(MenaiValue *));
    if (regs == NULL) {
        vs->error.code = MENAI_ERR_NOMEM;
        return NULL;
    }

    for (size_t i = 0; i < num_regs; i++) {
        menai_value_retain((MenaiValue *)menai_none(vs));
        regs[i] = (MenaiValue *)menai_none(vs);
    }

    /*
     * Publish the register file on the VM state so that alloc_menai_function
     * can trigger periodic GC using these registers as roots.  Cleared after
     * execute_loop returns, before the register file is released and freed.
     */
    vs->regs = regs;
    vs->num_regs = num_regs;

    MenaiValue *result = execute_loop(vs, code, extra_globals);

    vs->regs = NULL;

    for (size_t i = 0; i < num_regs; i++) {
        menai_value_release(vs, regs[i]);
    }

    free(regs);

    menai_closure_gc_collect(vs, result);

    return result;
}
