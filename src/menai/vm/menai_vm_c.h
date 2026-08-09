/*
 * menai_vm_c.h
 */
#ifndef MENAI_VM_C_H
#define MENAI_VM_C_H

#include <memory.h>
#include <limits.h>
#include <sys/types.h>
#include <stddef.h>
#include <stdint.h>
#include <assert.h>

#ifdef _MSC_VER

typedef ptrdiff_t ssize_t;
#define SSIZE_MAX PTRDIFF_MAX

/* MSVC deprecates strdup (POSIX) in favour of _strdup. */
#define strdup _strdup

#endif

#if defined(__GNUC__) || defined(__clang__)
#define MENAI_LIKELY(x) __builtin_expect(!!(x), 1)
#define MENAI_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define MENAI_LIKELY(x) (x)
#define MENAI_UNLIKELY(x) (x)
#endif

/*
 * MenaiType — the type tag for a Menai value.  uint16_t is sufficient for
 * the current types and leaves room for future additions.  The values are
 * chosen to be distinct and non-zero so that ob_type == 0 reliably detects
 * use-after-free (the allocator poisons freed blocks with ob_type = 0).
 */
typedef uint16_t MenaiType;

#define MENAITYPE_NONE 0x0001
#define MENAITYPE_BOOLEAN 0x0002
#define MENAITYPE_FUNCTION 0x0003
#define MENAITYPE_SYMBOL 0x0004
#define MENAITYPE_STRING 0x0005
#define MENAITYPE_INTEGER 0x0006
#define MENAITYPE_FLOAT 0x0007
#define MENAITYPE_COMPLEX 0x0008
#define MENAITYPE_LIST 0x0009
#define MENAITYPE_DICT 0x000a
#define MENAITYPE_SET 0x000b
#define MENAITYPE_STRUCT 0x000c
#define MENAITYPE_STRUCTTYPE 0x000d
#define MENAITYPE_BYTES 0x000e

typedef struct MenaiBigInt MenaiBigInt;
typedef struct MenaiBoolean MenaiBoolean;
typedef struct MenaiBytes MenaiBytes;
typedef struct MenaiCodeObject MenaiCodeObject;
typedef struct MenaiComplex MenaiComplex;
typedef struct MenaiDict MenaiDict;
typedef struct MenaiFloat MenaiFloat;
typedef struct MenaiFunction MenaiFunction;
typedef struct MenaiInteger MenaiInteger;
typedef struct MenaiList MenaiList;
typedef struct MenaiNone MenaiNone;
typedef struct MenaiSet MenaiSet;
typedef struct MenaiString MenaiString;
typedef struct MenaiStruct MenaiStruct;
typedef struct MenaiStructType MenaiStructType;
typedef struct MenaiSymbol MenaiSymbol;
typedef struct MenaiValue MenaiValue;

typedef int64_t hash_t;
typedef uint64_t uhash_t;

typedef struct {
    MenaiValue *key;     /* borrowed ref to MenaiValue *; NULL = empty slot */
    hash_t hash;         /* cached hash of key */
    ssize_t index;       /* index into the owning dict/set's element arrays */
} MenaiHashSlot;

/*
 * MenaiHashTable — open-addressing hash table
 *
 * Maps MenaiValue *keys to ssize_t indices.  Used as the internal
 * acceleration structure for MenaiDict (key -> entry index) and MenaiSet
 * (element -> entry index, for membership testing).
 *
 * Invariants:
 *   - slot_count is always a power of 2 (or 0 for an empty table).
 *   - used <= slot_count * MENAI_HT_MAX_LOAD.
 *   - A slot is empty when its key pointer is NULL.
 *   - Deleted slots are not used (tables are immutable after construction).
 */
typedef struct {
    MenaiHashSlot *slots;
    ssize_t slot_count;  /* power of 2; 0 means uninitialised */
    ssize_t used;
} MenaiHashTable;

struct MenaiCodeObject {
    size_t ob_refcnt;

    uint64_t *instrs;                    /* packed instruction words */
    int code_len;                        /* number of instructions */

    MenaiValue **constants;              /* fast constant pool */
    ssize_t nconst;

    const char **names;                  /* global name strings for OP_LOAD_NAME */
    hash_t *name_hashes;                 /* precomputed FNV-1a hash of each name */
    ssize_t nnames;

    MenaiCodeObject **children;          /* child code objects, one per closure */
    ssize_t nchildren;

    int param_count;
    int local_count;
    int outgoing_arg_slots;
    int is_variadic;
    ssize_t ncap;                        /* number of free variables (capture slots) */

    char **param_names;                  /* parameter name strings, parallel to param_count */
    ssize_t nparam_names;                /* number of elements in param_names */

    char *name;                          /* function name for error messages, or NULL */
};

/*
 * Sign-magnitude arbitrary-precision integer.
 */
struct MenaiBigInt {
    uint32_t *digits;  /* little-endian base-2^32 magnitude; NULL when zero */
    ssize_t length;    /* number of valid digits; 0 when zero */
    int sign;          /* -1, 0, or 1 */
};

/*
 * One entry in the MenaiStructType field-index table.
 * name is an owned MenaiString *; index is the 0-based field position.
 */
typedef struct {
    MenaiString *name;
    int index;
} MenaiFieldEntry;

/*
 * MenaiValue_HEAD — common prefix for every Menai value struct.
 *
 * ob_refcnt    — reference count.
 * ob_type      — type tag (MenaiType, uint16_t).
 * ob_alloc     — pool bucket number if this object was served from the
 *                pool allocator, or -1 if it was allocated directly via malloc.
 *                Written by menai_alloc; read by menai_free to determine how
 *                to return the block.
 */
#define MenaiValue_HEAD              \
    uint32_t ob_refcnt;              \
    MenaiType ob_type;               \
    int16_t ob_alloc_bucket;

struct MenaiBoolean {
    MenaiValue_HEAD
    int value;                          /* 0 or 1 */
};

/*
 * MenaiBytes — immutable sequence of bytes (octets, 0–255).
 *
 * Owners store data inline via a flexible array member.  Slice views allocate
 * only the header (sizeof(MenaiBytes)), point data into the owner's inline
 * buffer at an offset, and retain the owner — exactly the same structural
 * sharing pattern as MenaiList.  Views never form chains: all views point
 * directly at the root owner.
 */
struct MenaiBytes {
    MenaiValue_HEAD
    ssize_t length;                     /* logical byte count */
    hash_t hash;                        /* cached hash; -1 = not yet computed */
    MenaiBytes *owner;                  /* non-NULL when this is a slice view */
    uint8_t *data;                      /* points to inline_data for owners, into owner for views */
    uint8_t inline_data[];              /* FAM — storage for owning bytes */
};

struct MenaiComplex {
    MenaiValue_HEAD
    double real;
    double imag;
};

struct MenaiDict {
    MenaiValue_HEAD
    MenaiValue **keys;                  /* C array of owned MenaiValues */
    MenaiValue **values;                /* C array of owned MenaiValues */
    hash_t *hashes;                     /* C array of menai_value_hash(keys[i]) */
    MenaiHashTable ht;                  /* pure-C hash table for O(1) key lookup */
    ssize_t length;
};

struct MenaiFloat {
    MenaiValue_HEAD
    double value;
};

struct MenaiFunction {
    MenaiValue_HEAD
    ssize_t ncap;                       /* number of captured values */
    MenaiCodeObject *bytecode;          /* retained — owns all frame metadata */

    /* Inline capture array — ncap elements follow immediately. */
    MenaiValue *captures[1];            /* flexible array member (C99 [1] for MSVC compat) */
};

/*
 * Three-tier integer representation:
 *
 *   is_big == 0: value is stored inline as a C long in the small field.
 *                For values in [MENAI_INT_CACHE_MIN, MENAI_INT_CACHE_MAX]
 *                the object is a pre-allocated singleton and must never
 *                be freed.
 *
 *   is_big == 1: value is stored as a MenaiBigInt bignum in the big field.
 *                The MenaiBigInt owns its digit array.
 *
 * The ob_type is always &MenaiInteger_Type.
 */
struct MenaiInteger {
    MenaiValue_HEAD
    int is_big;
    long fixed;                         /* valid when is_big == 0 */
    MenaiBigInt big;                    /* valid when is_big == 1 */
};

struct MenaiList {
    MenaiValue_HEAD
    MenaiValue **elements;              /* points to inline_elements for owners, into owner for views */
    ssize_t length;                     /* number of live elements */

    /*
     * owner is non-NULL when this list is a slice view into another list's
     * inline_elements array.  In that case elements points into owner's storage
     * and must not be freed; only menai_value_release(owner) is needed on dealloc.
     * owner always points to a list with owner == NULL (never a chain).
     */
    MenaiList *owner;
    MenaiValue *inline_elements[];      /* FAM — storage for owning lists */
};

struct MenaiNone {
    MenaiValue_HEAD
};

struct MenaiSet {
    MenaiValue_HEAD
    MenaiValue **elements;              /* points into inline_data[0..length-1] */
    hash_t *hashes;                     /* points into inline_data past the elements */
    MenaiHashTable ht;                  /* pure-C hash table for O(1) membership; separate allocation */
    ssize_t length;                     /* number of live elements */
    MenaiValue *inline_data[];          /* FAM: elements[0..cap-1] then hashes[0..cap-1] */
};

struct MenaiString {
    MenaiValue_HEAD
    ssize_t length;                     /* codepoint count */
    hash_t hash;                        /* cached hash; -1 = not yet computed */
    uint32_t data[];                    /* UTF-32 codepoints, flexible array */
};

struct MenaiStruct {
    MenaiValue_HEAD
    int nfields;                        /* number of fields */
    MenaiStructType *struct_type;       /* owned reference to MenaiStructType */
    MenaiValue *items[1];               /* inline field values, nfields entries */
};

struct MenaiStructType {
    MenaiValue_HEAD
    MenaiString *name;                  /* owned MenaiString * — struct type name */
    int tag;                            /* unique integer tag */
    int nfields;                        /* number of fields */
    MenaiHashTable field_ht;            /* name -> index hash table; keys are borrowed from fields[] */
    MenaiFieldEntry fields[];           /* inline field-index table, nfields entries */
};

struct MenaiSymbol {
    MenaiValue_HEAD
    MenaiString *name;                  /* owned MenaiString * */
};

/*
 * MenaiValue — the minimal struct that every MenaiValue pointer can be safely cast to
 */
struct MenaiValue {
    MenaiValue_HEAD
};

/*
 * Menai VM error codes — returned as negative values by leaf modules
 * (bigint, string, hashtable, etc.) and propagated by the VM to the bridge.
 * The bridge translates them into the appropriate Python exception.
 *
 * Functions returning int return MENAI_OK (0) on success or a negative
 * MENAI_ERR_* code on failure.  Functions returning a pointer return NULL
 * on failure; the only failure mode for most pointer-returning functions is
 * allocation failure (MENAI_ERR_NOMEM), so the error code is implicit.
 *
 * The VM sets one of these codes at each error site, then jumps to the
 * error label which assembles a MenaiVMError struct.  The bridge maps
 * the code to the appropriate Python exception type and message.
 */
#define MENAI_OK 0
#define MENAI_ERR_NOMEM -1
#define MENAI_ERR_VALUE -2
#define MENAI_ERR_OVERFLOW -3
#define MENAI_ERR_DIVISION_BY_ZERO -4
#define MENAI_ERR_TYPE -5
#define MENAI_ERR_EVAL -6
#define MENAI_ERR_CANCELLED -7
#define MENAI_ERR_TYPE_MISMATCH -8
#define MENAI_ERR_NOT_SYMBOL -9
#define MENAI_ERR_NOT_SYMBOL_PAIR -10
#define MENAI_ERR_IF_NOT_BOOLEAN -11
#define MENAI_ERR_ERROR_MSG_NOT_STRING -12
#define MENAI_ERR_NOT_CALLABLE -13
#define MENAI_ERR_APPLY_SECOND_NOT_LIST -14
#define MENAI_ERR_APPLY_FIRST_NOT_FUNCTION -15
#define MENAI_ERR_PATCH_CLOSURE_NOT_FUNCTION -16
#define MENAI_ERR_INDEX_NOT_INTEGER -17
#define MENAI_ERR_SLICE_INDICES_NOT_INTEGER -18
#define MENAI_ERR_NOT_SINGLE_CHAR_STRING -19
#define MENAI_ERR_RADIX_NOT_INTEGER -20
#define MENAI_ERR_OFFSET_NOT_INTEGER -21
#define MENAI_ERR_VALUE_NOT_INTEGER -22
#define MENAI_ERR_LIST_ELEMENTS_NOT_INTEGERS -23
#define MENAI_ERR_SLICE_START_NOT_INTEGER -24
#define MENAI_ERR_SLICE_END_NOT_INTEGER -25
#define MENAI_ERR_BYTE_NOT_INTEGER -26
#define MENAI_ERR_LIST_TO_STRING_NOT_STRINGS -27
#define MENAI_ERR_RANGE_NOT_INTEGER -28
#define MENAI_ERR_INDEX_OUT_OF_RANGE -30
#define MENAI_ERR_SLICE_START_OUT_OF_RANGE -31
#define MENAI_ERR_SLICE_END_OUT_OF_RANGE -32
#define MENAI_ERR_OFFSET_OUT_OF_BOUNDS -33
#define MENAI_ERR_MODULO_BY_ZERO -34
#define MENAI_ERR_INVALID_RADIX -35
#define MENAI_ERR_VALUE_OUT_OF_RANGE -36
#define MENAI_ERR_INVALID_CODEPOINT -37
#define MENAI_ERR_NEGATIVE_SLICE_INDEX -38
#define MENAI_ERR_NEGATIVE_EXPONENT -39
#define MENAI_ERR_NEGATIVE_SHIFT -40
#define MENAI_ERR_NEGATIVE_ARGUMENT -41
#define MENAI_ERR_SHIFT_TOO_LARGE -42
#define MENAI_ERR_INVALID_LOG_BASE -43
#define MENAI_ERR_SLICE_START_AFTER_END -44
#define MENAI_ERR_ARITY_MISMATCH -45
#define MENAI_ERR_STRUCT_ARITY_MISMATCH -46
#define MENAI_ERR_UNDEFINED_VARIABLE -47
#define MENAI_ERR_STRUCT_FIELD_NOT_FOUND -48
#define MENAI_ERR_EMPTY_LIST -49
#define MENAI_ERR_CALL_DEPTH_EXCEEDED -50
#define MENAI_ERR_UNHASHABLE_KEY -51
#define MENAI_ERR_INVALID_UTF8 -52
#define MENAI_ERR_HEX_EVEN_LENGTH -53
#define MENAI_ERR_INVALID_HEX_CHAR -54
#define MENAI_ERR_TRUNCATED_LEB128 -55
#define MENAI_ERR_RANGE_ZERO_STEP -56
#define MENAI_ERR_CLOSURE_INDEX_OUT_OF_RANGE -57
#define MENAI_ERR_MISSING_RETURN -58
#define MENAI_ERR_UNIMPLEMENTED_OPCODE -59
#define MENAI_ERR_USER_ERROR -60

/*
 * MenaiVMError — structured error record produced by the VM.
 *
 * The VM fills this at the error label in execute_loop, capturing the
 * error code and execution context (opcode, instruction pointer, call
 * depth).  The bridge reads it after execution returns and translates
 * it into a Python exception.
 *
 * user_message is only set when code == MENAI_ERR_USER_ERROR; it is
 * a malloc'd C string that the bridge must free after use.
 */
typedef struct {
    int code;               /* MENAI_ERR_* code */
    int opcode;             /* opcode that was executing (0 if unknown) */
    int ip;                 /* instruction pointer (0 if unknown) */
    int call_depth;         /* call stack depth at time of error */
    const char *user_message; /* only for MENAI_ERR_USER_ERROR */
} MenaiVMError;

/*
 * Fast type-check macros
 */
#define IS_MENAI_NONE(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_NONE)
#define IS_MENAI_BOOLEAN(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_BOOLEAN)
#define IS_MENAI_INTEGER(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_INTEGER)
#define IS_MENAI_FLOAT(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_FLOAT)
#define IS_MENAI_COMPLEX(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_COMPLEX)
#define IS_MENAI_STRING(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_STRING)
#define IS_MENAI_SYMBOL(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_SYMBOL)
#define IS_MENAI_LIST(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_LIST)
#define IS_MENAI_DICT(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_DICT)
#define IS_MENAI_SET(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_SET)
#define IS_MENAI_FUNCTION(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_FUNCTION)
#define IS_MENAI_STRUCTTYPE(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_STRUCTTYPE)
#define IS_MENAI_STRUCT(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_STRUCT)
#define IS_MENAI_BYTES(o) (((MenaiValue *)(o))->ob_type == MENAITYPE_BYTES)

/*
 * Pool allocator constants.
 */
#define MENAI_POOL_LOG_MIN_SIZE 5
#define MENAI_POOL_MIN_SIZE (1 << MENAI_POOL_LOG_MIN_SIZE)
#define MENAI_POOL_MAX_SIZE 4096
#define MENAI_POOL_NUM_BUCKETS 8
#define MENAI_POOL_MAX_DEPTH 256

/*
 * Small integer cache — covers [MENAI_INT_CACHE_MIN, MENAI_INT_CACHE_MAX].
 */
#define MENAI_INT_CACHE_MIN (-5)
#define MENAI_INT_CACHE_MAX 256
#define MENAI_INT_CACHE_SIZE (MENAI_INT_CACHE_MAX - MENAI_INT_CACHE_MIN + 1)

/*
 * GlobalsTable — open-addressing hash table for O(1) name lookup.
 *
 * Built once by the bridge and cached.  The cached table is a complete
 * lookup table with hash slots.  It is never copied per-call — the
 * execute loop reads from it directly.  Values and names are owned.
 */
typedef struct {
    const char *name;
    hash_t hash;
    MenaiValue *value;
} GlobalsSlot;

typedef struct {
    const char *name;
    MenaiValue *value;
} GlobalsEntry;

typedef struct {
    GlobalsSlot *slots;
    GlobalsEntry *entries;
    ssize_t slot_count;
    ssize_t count;
} GlobalsTable;

/*
 * MenaiVMState — per-instance VM state.
 *
 * Owns all mutable state that must not be shared across VM instances:
 * the pool allocator free-lists, singleton values, and the prelude globals.
 * Each MenaiVM Python object allocates one MenaiVMState and passes it
 * explicitly to every function that needs it.
 */
typedef struct MenaiVMState {
    /* Pool allocator — per-instance free lists */
    void *_pool_heads[MENAI_POOL_NUM_BUCKETS];
    int _pool_depths[MENAI_POOL_NUM_BUCKETS];

    /* Singletons — per-instance */
    MenaiNone none_storage;             /* inline, not heap */
    MenaiBoolean true_storage;          /* inline */
    MenaiBoolean false_storage;         /* inline */
    MenaiInteger *integer_cache[MENAI_INT_CACHE_SIZE];  /* heap, from this pool */

    MenaiList *empty_list;              /* heap, from this pool */
    MenaiDict *empty_dict;              /* heap, from this pool */
    MenaiSet *empty_set;                /* heap, from this pool */

    /* Prelude globals — per-instance, set once via menai_vm_set_prelude */
    GlobalsTable _globals;
    int _globals_valid;

    int _cancel_flag;
} MenaiVMState;

MenaiVMState *menai_vm_state_alloc(void);
void menai_vm_state_free(MenaiVMState *vs);

void *menai_alloc(MenaiVMState *vs, size_t size);
void menai_free(MenaiVMState *vs, void *ptr);

void menai_value_free(MenaiVMState *vs, MenaiValue *v);

/*
 * menai_value_retain — claim an interest in val.
 */
static inline void
menai_value_retain(MenaiValue *val)
{
    assert(val->ob_type != 0);
    val->ob_refcnt++;
}

/*
 * menai_value_release — relinquish an interest in val.
 *
 * val must not be NULL.  When ob_refcnt reaches zero, we call the registered destructor.
 */
static inline void
menai_value_release(MenaiVMState *vs, MenaiValue *val)
{
    assert(val->ob_type != 0);
    if (--val->ob_refcnt == 0) {
        menai_value_free(vs, val);
    }
}

/*
 * menai_name_str_hash — FNV-1a hash of a UTF-8 C string.
 *
 * Used to precompute hashes for global name strings stored in
 * MenaiCodeObject name_hashes, and to hash entries when building
 * GlobalsTable slots.  Returns a value in [0, PY_SSIZE_T_MAX]; never -1.
 */
static inline hash_t
menai_name_str_hash(const char *s)
{
    uhash_t h = 14695981039346656037ULL;  /* FNV offset basis */
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        h ^= (uhash_t)*p++;
        h *= 1099511628211ULL;              /* FNV prime */
    }

    hash_t r = (hash_t)(h & (uhash_t)PTRDIFF_MAX);
    return r == -1 ? -2 : r;
}

/*
 * menai_hash_double — hash a C double without any Python API calls.
 *
 * Reinterprets the IEEE 754 bit pattern as a uint64_t via memcpy (safe
 * under strict aliasing rules) then applies a finalisation mix so that
 * nearby values produce well-distributed hashes.  NaN is normalised to a
 * fixed bit pattern before mixing so all NaN values hash identically.
 * The result is mapped away from -1 (the CPython "error" sentinel).
 *
 * This is a Menai-internal hash — it does not need to match Python's
 * float hash, because Menai floats and integers are never equal and are
 * never mixed in the same dict or set.
 */
static inline hash_t
menai_hash_double(double v)
{
    uint64_t bits;
    if (v != v) {
        bits = 0x7FF8000000000000ULL;
    } else {
        memcpy(&bits, &v, sizeof(bits));
    }

    /* Finalisation mix from SplitMix64 */
    bits ^= bits >> 30;
    bits *= 0xbf58476d1ce4e5b9ULL;
    bits ^= bits >> 27;
    bits *= 0x94d049bb133111ebULL;
    bits ^= bits >> 31;
    hash_t h = (hash_t)(bits & (uint64_t)PTRDIFF_MAX);
    return h == -1 ? -2 : h;
}

hash_t menai_value_hash(MenaiValue *val);
int menai_value_equal(MenaiValue *a, MenaiValue *b);

#define MENAI_HT_MAX_LOAD_NUM 2   /* load factor numerator   */
#define MENAI_HT_MAX_LOAD_DEN 3   /* load factor denominator */

int menai_ht_init(MenaiHashTable *ht, ssize_t n);
void menai_ht_final(MenaiHashTable *ht);
ssize_t menai_ht_lookup(const MenaiHashTable *ht, MenaiValue *key, hash_t hash);
void menai_ht_insert(MenaiHashTable *ht, MenaiValue *key, hash_t hash, ssize_t index);

/*
 * menai_code_object_retain — increment the reference count.
 */
static inline void
menai_code_object_retain(MenaiCodeObject *co)
{
    co->ob_refcnt++;
}

void menai_code_object_release(MenaiVMState *vs, MenaiCodeObject *co);
int menai_code_object_max_locals(const MenaiCodeObject *co);

static inline void
menai_bigint_init(MenaiBigInt *a)
{
    a->digits = NULL;
    a->length = 0;
    a->sign = 0;
}

void menai_bigint_final(MenaiBigInt *a);
void menai_bigint_normalize(MenaiBigInt *a);
int menai_bigint_copy(const MenaiBigInt *src, MenaiBigInt *dst);
int menai_bigint_from_long(long v, MenaiBigInt *a);
int menai_bigint_from_long_long(long long v, MenaiBigInt *a);
int menai_bigint_from_unsigned_long_long(unsigned long long v, MenaiBigInt *a);
int menai_bigint_from_string(const char *s, int base, MenaiBigInt *a);
int menai_bigint_from_codepoints(const uint32_t *data, ssize_t len, int base, MenaiBigInt *a);
int menai_bigint_from_double(double v, MenaiBigInt *a);
int menai_bigint_fits_long(const MenaiBigInt *a);
int menai_bigint_to_long(const MenaiBigInt *a, long *out);
int menai_bigint_fits_long_long(const MenaiBigInt *a);
int menai_bigint_to_long_long(const MenaiBigInt *a, long long *out);
int menai_bigint_to_double(const MenaiBigInt *a, double *out);
int menai_bigint_fits_unsigned_long_long(const MenaiBigInt *a);
int menai_bigint_to_unsigned_long_long(const MenaiBigInt *a, unsigned long long *out);
MenaiString *menai_bigint_to_menai_string(MenaiVMState *vs, const MenaiBigInt *a, int base);
hash_t menai_bigint_hash(const MenaiBigInt *a);
int menai_bigint_add(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_sub(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_mul(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_floordiv(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_mod(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_divmod(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *quotient, MenaiBigInt *remainder);
int menai_bigint_neg(const MenaiBigInt *a, MenaiBigInt *result);
int menai_bigint_abs(const MenaiBigInt *a, MenaiBigInt *result);
int menai_bigint_pow(const MenaiBigInt *a, const MenaiBigInt *exp, MenaiBigInt *result);
int menai_bigint_and(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_or(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_xor(const MenaiBigInt *a, const MenaiBigInt *b, MenaiBigInt *result);
int menai_bigint_not(const MenaiBigInt *a, MenaiBigInt *result);
int menai_bigint_shift_left(const MenaiBigInt *a, ssize_t shift, MenaiBigInt *result);
int menai_bigint_shift_right(const MenaiBigInt *a, ssize_t shift, MenaiBigInt *result);
int menai_bigint_eq(const MenaiBigInt *a, const MenaiBigInt *b);
int menai_bigint_ne(const MenaiBigInt *a, const MenaiBigInt *b);
int menai_bigint_lt(const MenaiBigInt *a, const MenaiBigInt *b);
int menai_bigint_gt(const MenaiBigInt *a, const MenaiBigInt *b);
int menai_bigint_le(const MenaiBigInt *a, const MenaiBigInt *b);
int menai_bigint_ge(const MenaiBigInt *a, const MenaiBigInt *b);

MenaiNone *menai_none(MenaiVMState *vs);

static inline hash_t
menai_none_hash(void)
{
    return (hash_t)0x4e6f6e65UL;
}

MenaiBoolean *menai_boolean_true(MenaiVMState *vs);
MenaiBoolean *menai_boolean_false(MenaiVMState *vs);

static inline hash_t
menai_boolean_hash(MenaiBoolean *b)
{
    return (hash_t)b->value;
}

static inline int
menai_boolean_equal(MenaiBoolean *a, MenaiBoolean *b)
{
    return a->value == b->value;
}

MenaiFloat *alloc_menai_float(MenaiVMState *vs, double value);

static inline hash_t
menai_float_hash(MenaiFloat *f)
{
    return menai_hash_double(f->value);
}

static inline int
menai_float_equal(MenaiFloat *a, MenaiFloat *b)
{
    return a->value == b->value;
}

MenaiComplex *alloc_menai_complex(MenaiVMState *vs, double real, double imag);

static inline hash_t
menai_complex_hash(MenaiComplex *c)
{
    hash_t hr = menai_hash_double(c->real);
    hash_t hi = menai_hash_double(c->imag);
    uhash_t acc = (uhash_t)hr * 1000003UL ^ (uhash_t)hi;
    hash_t h = (hash_t)(acc & (uhash_t)SSIZE_MAX);
    return h == -1 ? -2 : h;
}

static inline int
menai_complex_equal(MenaiComplex *a, MenaiComplex *b)
{
    return a->real == b->real && a->imag == b->imag;
}

MenaiFunction *alloc_menai_function(MenaiVMState *vs, MenaiCodeObject *co, MenaiNone *none_val);

static inline int
menai_function_equal(MenaiValue *a, MenaiValue *b)
{
    return a == b;
}

MenaiString *alloc_menai_string(MenaiVMState *vs, ssize_t len);
MenaiString *alloc_menai_string_from_utf8(MenaiVMState *vs, const char *utf8, ssize_t nbytes);
char *alloc_utf8_from_menai_string(MenaiString *s, ssize_t *out_nbytes);
int menai_string_compare(MenaiString *a, MenaiString *b);
int menai_string_equal(MenaiString *a, MenaiString *b);
hash_t menai_string_hash(MenaiString *s);
void menai_string_concat(MenaiString *a, MenaiString *b, MenaiString *r);
ssize_t menai_string_upcase_length(MenaiString *s);
void menai_string_upcase(MenaiString *s, MenaiString *r);
void menai_string_downcase(MenaiString *s, MenaiString *r);
MenaiString *alloc_menai_string_from_trim(MenaiVMState *vs, MenaiString *s);
MenaiString *alloc_menai_string_from_trim_left(MenaiVMState *vs, MenaiString *s);
MenaiString *alloc_menai_string_from_trim_right(MenaiVMState *vs, MenaiString *s);
ssize_t menai_string_find(MenaiString *haystack, MenaiString *needle);
MenaiString *alloc_menai_string_from_replace(MenaiVMState *vs, MenaiString *s, MenaiString *from, MenaiString *to);
MenaiString *alloc_menai_string_from_float(MenaiVMState *vs, double v);
MenaiString *alloc_menai_string_from_complex(MenaiVMState *vs, double real, double imag);

MenaiSymbol *alloc_menai_symbol(MenaiVMState *vs, MenaiString *name);

static inline hash_t
menai_symbol_hash(MenaiSymbol *sym)
{
    return menai_string_hash(sym->name);
}

static inline int
menai_symbol_equal(MenaiSymbol *a, MenaiSymbol *b)
{
    return menai_string_equal(a->name, b->name);
}

MenaiInteger *alloc_menai_integer_from_long(MenaiVMState *vs, long n);
MenaiInteger *alloc_menai_integer_from_long_long(MenaiVMState *vs, long long n);
MenaiInteger *alloc_menai_integer_from_bigint(MenaiVMState *vs, MenaiBigInt src);

static inline MenaiInteger *
alloc_menai_integer_from_ssize_t(MenaiVMState *vs, ssize_t n)
{
    return alloc_menai_integer_from_long(vs, (long)n);
}

static inline hash_t
menai_integer_hash(MenaiInteger *obj)
{
    if (!obj->is_big) {
        hash_t h = (hash_t)obj->fixed;
        return h == -1 ? -2 : h;
    }

    return menai_bigint_hash(&obj->big);
}

static inline int
menai_integer_equal(MenaiInteger *a, MenaiInteger *b)
{
    if (!a->is_big && !b->is_big) {
        return a->fixed == b->fixed;
    }

    if (a->is_big != b->is_big) {
        return 0;
    }

    return menai_bigint_eq(&a->big, &b->big);
}

MenaiBytes *alloc_menai_bytes(MenaiVMState *vs, ssize_t n);
MenaiBytes *alloc_menai_bytes_from_raw(MenaiVMState *vs, const uint8_t *src, ssize_t n);
MenaiBytes *alloc_menai_bytes_from_slice(MenaiVMState *vs, MenaiBytes *b, ssize_t start, ssize_t end);
MenaiBytes *alloc_menai_bytes_from_concat(MenaiVMState *vs, MenaiBytes *a, MenaiBytes *b);
MenaiBytes *alloc_menai_bytes_from_append_u8(MenaiVMState *vs, MenaiBytes *b, uint8_t value);
MenaiBytes *alloc_menai_bytes_from_append_multi(MenaiVMState *vs, MenaiBytes *b, unsigned long long value, int width, int le);
MenaiBytes *alloc_menai_bytes_from_write_multi(MenaiVMState *vs, MenaiBytes *b, ssize_t offset, unsigned long long value, int width, int le);
int menai_bytes_equal(MenaiBytes *a, MenaiBytes *b);
int menai_bytes_compare(MenaiBytes *a, MenaiBytes *b);
hash_t menai_bytes_hash(MenaiBytes *b);

MenaiStruct *alloc_menai_struct(MenaiVMState *vs, MenaiStructType *struct_type, MenaiValue **field_values, ssize_t nfields);

static inline int
menai_structtype_equal(MenaiStructType *a, MenaiStructType *b)
{
    return a->tag == b->tag;
}

static inline int
menai_struct_equal(MenaiStruct *a, MenaiStruct *b)
{
    if (((MenaiStructType *)a->struct_type)->tag !=
            ((MenaiStructType *)b->struct_type)->tag) {
        return 0;
    }

    int n = a->nfields;
    if (n != b->nfields) {
        return 0;
    }

    for (int i = 0; i < n; i++) {
        if (!menai_value_equal(a->items[i], b->items[i])) {
            return 0;
        }
    }

    return 1;
}

static inline hash_t
menai_struct_hash(MenaiStruct *s)
{
    int tag = ((MenaiStructType *)s->struct_type)->tag;
    int n = s->nfields;
    uhash_t acc = 0x345678UL ^ (uhash_t)tag;
    for (int i = 0; i < n; i++) {
        hash_t fh = menai_value_hash(s->items[i]);
        if (fh == -1) {
            return -1;
        }

        acc = acc * 1000003UL ^ (uhash_t)fh;
    }

    acc ^= (uhash_t)n;
    return (hash_t)(acc == (uhash_t)-1 ? (uhash_t)-2 : acc);
}

MenaiStructType *alloc_menai_structtype(MenaiVMState *vs, MenaiString *name, int tag, MenaiString **field_names, ssize_t nfields);

static inline hash_t
menai_structtype_hash(MenaiStructType *st)
{
    return (hash_t)st->tag;
}

MenaiDict *alloc_menai_dict(MenaiVMState *vs);

static inline int
menai_dict_equal(MenaiDict *a, MenaiDict *b)
{
    if (a->length != b->length) {
        return 0;
    }

    for (ssize_t i = 0; i < a->length; i++) {
        if (a->hashes[i] != b->hashes[i] ||
                !menai_value_equal(a->keys[i], b->keys[i]) ||
                !menai_value_equal(a->values[i], b->values[i])) {
            return 0;
        }
    }

    return 1;
}

MenaiList *alloc_menai_list(MenaiVMState *vs, ssize_t n);
void menai_list_rest(MenaiList *lst, MenaiList *r);
void menai_list_slice(MenaiList *lst, ssize_t start, ssize_t end, MenaiList *r);

static inline int
menai_list_equal(MenaiList *a, MenaiList *b)
{
    if (a->length != b->length) {
        return 0;
    }

    for (ssize_t i = 0; i < a->length; i++) {
        if (!menai_value_equal(a->elements[i], b->elements[i])) {
            return 0;
        }
    }

    return 1;
}

MenaiSet *alloc_menai_set(MenaiVMState *vs, ssize_t cap);

static inline int
menai_set_equal(MenaiSet *a, MenaiSet *b)
{
    if (a->length != b->length) {
        return 0;
    }

    for (ssize_t i = 0; i < a->length; i++) {
        if (menai_ht_lookup(&b->ht, a->elements[i], a->hashes[i]) == -1) {
            return 0;
        }
    }

    return 1;
}

int menai_vm_bridge_init(void);

void globals_free(MenaiVMState *vs, GlobalsTable *gt);
int globals_build_from_dict(MenaiVMState *vs, GlobalsTable *gt, MenaiDict *d);
MenaiValue *globals_lookup(const GlobalsTable *gt, const char *name, hash_t h);

MenaiValue *menai_vm_execute_native(MenaiVMState *vs,
                                    MenaiCodeObject *code,
                                    const GlobalsTable *extra_globals,
                                    MenaiVMError *out_error);

void menai_vm_cancel(MenaiVMState *vs);

#endif /* MENAI_VM_C_H */
