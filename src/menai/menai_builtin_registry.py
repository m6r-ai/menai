"""
Unified builtin function registry for Menai.

This module is the single source of truth for every opcode-backed builtin: the
opcode that implements it and the arity it presents to the surface language.
"""


from dataclasses import dataclass

from menai.bytecode.menai_bytecode import Opcode


@dataclass(frozen=True, slots=True)
class MenaiBuiltinInfo:
    """
    Everything the compiler needs to know about one opcode-backed builtin.

    min_args/max_args describe the surface-language arity: how many arguments a
    call may supply.  max_args is None for a truly variadic builtin (no upper
    bound).  These differ from the opcode's own operand count for variadic and
    optional-argument builtins, where the desugarer lowers a surface call into
    one or more fully-saturated opcode calls.

    opcode_arity is derived from the opcode itself rather than stored, so the
    two can never disagree.
    """

    opcode: Opcode
    min_args: int
    max_args: int | None

    def opcode_arity(self) -> int:
        """Return the number of register operands the opcode takes."""
        return self.opcode.arg_count()


# Every opcode-backed builtin, keyed by its surface-language name.
#
# A builtin is opcode-backed if and only if it appears here.  Pure-Menai prelude
# functions (map-list, filter-list, fold-list, list, set, vector, etc.) are
# implemented as Menai lambdas in prelude.menai and must NOT be added here.
BUILTINS: dict[str, MenaiBuiltinInfo] = {
    'function?': MenaiBuiltinInfo(Opcode.FUNCTION_P, 1, 1),
    'function=?': MenaiBuiltinInfo(Opcode.FUNCTION_EQ_P, 2, 2),
    'function!=?': MenaiBuiltinInfo(Opcode.FUNCTION_NEQ_P, 2, 2),
    'function-min-arity': MenaiBuiltinInfo(Opcode.FUNCTION_MIN_ARITY, 1, 1),
    'function-variadic?': MenaiBuiltinInfo(Opcode.FUNCTION_VARIADIC_P, 1, 1),
    'function-accepts?': MenaiBuiltinInfo(Opcode.FUNCTION_ACCEPTS_P, 2, 2),
    'symbol?': MenaiBuiltinInfo(Opcode.SYMBOL_P, 1, 1),
    'symbol=?': MenaiBuiltinInfo(Opcode.SYMBOL_EQ_P, 2, 2),
    'symbol!=?': MenaiBuiltinInfo(Opcode.SYMBOL_NEQ_P, 2, 2),
    'symbol->string': MenaiBuiltinInfo(Opcode.SYMBOL_TO_STRING, 1, 1),
    'none?': MenaiBuiltinInfo(Opcode.NONE_P, 1, 1),
    'boolean?': MenaiBuiltinInfo(Opcode.BOOLEAN_P, 1, 1),
    'boolean=?': MenaiBuiltinInfo(Opcode.BOOLEAN_EQ_P, 2, None),
    'boolean!=?': MenaiBuiltinInfo(Opcode.BOOLEAN_NEQ_P, 2, None),
    'boolean-not': MenaiBuiltinInfo(Opcode.BOOLEAN_NOT, 1, 1),
    'integer?': MenaiBuiltinInfo(Opcode.INTEGER_P, 1, 1),
    'integer=?': MenaiBuiltinInfo(Opcode.INTEGER_EQ_P, 2, None),
    'integer!=?': MenaiBuiltinInfo(Opcode.INTEGER_NEQ_P, 2, None),
    'integer<?': MenaiBuiltinInfo(Opcode.INTEGER_LT_P, 2, None),
    'integer>?': MenaiBuiltinInfo(Opcode.INTEGER_GT_P, 2, None),
    'integer<=?': MenaiBuiltinInfo(Opcode.INTEGER_LTE_P, 2, None),
    'integer>=?': MenaiBuiltinInfo(Opcode.INTEGER_GTE_P, 2, None),
    'integer-abs': MenaiBuiltinInfo(Opcode.INTEGER_ABS, 1, 1),
    'integer+': MenaiBuiltinInfo(Opcode.INTEGER_ADD, 0, None),
    'integer-': MenaiBuiltinInfo(Opcode.INTEGER_SUB, 2, None),
    'integer*': MenaiBuiltinInfo(Opcode.INTEGER_MUL, 0, None),
    'integer/': MenaiBuiltinInfo(Opcode.INTEGER_DIV, 2, None),
    'integer%': MenaiBuiltinInfo(Opcode.INTEGER_MOD, 2, 2),
    'integer-neg': MenaiBuiltinInfo(Opcode.INTEGER_NEG, 1, 1),
    'integer-expn': MenaiBuiltinInfo(Opcode.INTEGER_EXPN, 2, 2),
    'integer-bit-not': MenaiBuiltinInfo(Opcode.INTEGER_BIT_NOT, 1, 1),
    'integer-bit-shift-left': MenaiBuiltinInfo(Opcode.INTEGER_BIT_SHIFT_LEFT, 2, 2),
    'integer-bit-shift-right': MenaiBuiltinInfo(Opcode.INTEGER_BIT_SHIFT_RIGHT, 2, 2),
    'integer-bit-or': MenaiBuiltinInfo(Opcode.INTEGER_BIT_OR, 2, None),
    'integer-bit-and': MenaiBuiltinInfo(Opcode.INTEGER_BIT_AND, 2, None),
    'integer-bit-xor': MenaiBuiltinInfo(Opcode.INTEGER_BIT_XOR, 2, None),
    'integer-min': MenaiBuiltinInfo(Opcode.INTEGER_MIN, 1, None),
    'integer-max': MenaiBuiltinInfo(Opcode.INTEGER_MAX, 1, None),
    'integer->float': MenaiBuiltinInfo(Opcode.INTEGER_TO_FLOAT, 1, 1),
    'integer->complex': MenaiBuiltinInfo(Opcode.INTEGER_TO_COMPLEX, 1, 2),
    'integer->string': MenaiBuiltinInfo(Opcode.INTEGER_TO_STRING, 1, 2),
    'integer-codepoint->string': MenaiBuiltinInfo(Opcode.INTEGER_CODEPOINT_TO_STRING, 1, 1),
    'float?': MenaiBuiltinInfo(Opcode.FLOAT_P, 1, 1),
    'float=?': MenaiBuiltinInfo(Opcode.FLOAT_EQ_P, 2, None),
    'float!=?': MenaiBuiltinInfo(Opcode.FLOAT_NEQ_P, 2, None),
    'float<?': MenaiBuiltinInfo(Opcode.FLOAT_LT_P, 2, None),
    'float>?': MenaiBuiltinInfo(Opcode.FLOAT_GT_P, 2, None),
    'float<=?': MenaiBuiltinInfo(Opcode.FLOAT_LTE_P, 2, None),
    'float>=?': MenaiBuiltinInfo(Opcode.FLOAT_GTE_P, 2, None),
    'float-abs': MenaiBuiltinInfo(Opcode.FLOAT_ABS, 1, 1),
    'float+': MenaiBuiltinInfo(Opcode.FLOAT_ADD, 0, None),
    'float-': MenaiBuiltinInfo(Opcode.FLOAT_SUB, 2, None),
    'float*': MenaiBuiltinInfo(Opcode.FLOAT_MUL, 0, None),
    'float/': MenaiBuiltinInfo(Opcode.FLOAT_DIV, 2, None),
    'float//': MenaiBuiltinInfo(Opcode.FLOAT_FLOOR_DIV, 2, 2),
    'float%': MenaiBuiltinInfo(Opcode.FLOAT_MOD, 2, 2),
    'float-neg': MenaiBuiltinInfo(Opcode.FLOAT_NEG, 1, 1),
    'float-exp': MenaiBuiltinInfo(Opcode.FLOAT_EXP, 1, 1),
    'float-expn': MenaiBuiltinInfo(Opcode.FLOAT_EXPN, 2, None),
    'float-log': MenaiBuiltinInfo(Opcode.FLOAT_LOG, 1, 1),
    'float-log10': MenaiBuiltinInfo(Opcode.FLOAT_LOG10, 1, 1),
    'float-log2': MenaiBuiltinInfo(Opcode.FLOAT_LOG2, 1, 1),
    'float-logn': MenaiBuiltinInfo(Opcode.FLOAT_LOGN, 2, 2),
    'float-sin': MenaiBuiltinInfo(Opcode.FLOAT_SIN, 1, 1),
    'float-cos': MenaiBuiltinInfo(Opcode.FLOAT_COS, 1, 1),
    'float-tan': MenaiBuiltinInfo(Opcode.FLOAT_TAN, 1, 1),
    'float-sqrt': MenaiBuiltinInfo(Opcode.FLOAT_SQRT, 1, 1),
    'float->integer': MenaiBuiltinInfo(Opcode.FLOAT_TO_INTEGER, 1, 1),
    'float->complex': MenaiBuiltinInfo(Opcode.FLOAT_TO_COMPLEX, 1, 2),
    'float->string': MenaiBuiltinInfo(Opcode.FLOAT_TO_STRING, 1, 1),
    'float-floor': MenaiBuiltinInfo(Opcode.FLOAT_FLOOR, 1, 1),
    'float-ceil': MenaiBuiltinInfo(Opcode.FLOAT_CEIL, 1, 1),
    'float-round': MenaiBuiltinInfo(Opcode.FLOAT_ROUND, 1, 1),
    'float-min': MenaiBuiltinInfo(Opcode.FLOAT_MIN, 1, None),
    'float-max': MenaiBuiltinInfo(Opcode.FLOAT_MAX, 1, None),
    'float-atan2': MenaiBuiltinInfo(Opcode.FLOAT_ATAN2, 2, 2),
    'float-cosh': MenaiBuiltinInfo(Opcode.FLOAT_COSH, 1, 1),
    'float-sinh': MenaiBuiltinInfo(Opcode.FLOAT_SINH, 1, 1),
    'float-tanh': MenaiBuiltinInfo(Opcode.FLOAT_TANH, 1, 1),
    'float-asin': MenaiBuiltinInfo(Opcode.FLOAT_ASIN, 1, 1),
    'float-acos': MenaiBuiltinInfo(Opcode.FLOAT_ACOS, 1, 1),
    'float-atan': MenaiBuiltinInfo(Opcode.FLOAT_ATAN, 1, 1),
    'float-hypot': MenaiBuiltinInfo(Opcode.FLOAT_HYPOT, 2, 2),
    'float-exp2': MenaiBuiltinInfo(Opcode.FLOAT_EXP2, 1, 1),
    'float-cbrt': MenaiBuiltinInfo(Opcode.FLOAT_CBRT, 1, 1),
    'float-expm1': MenaiBuiltinInfo(Opcode.FLOAT_EXPM1, 1, 1),
    'float-log1p': MenaiBuiltinInfo(Opcode.FLOAT_LOG1P, 1, 1),
    'float-trunc': MenaiBuiltinInfo(Opcode.FLOAT_TRUNC, 1, 1),
    'float-copysign': MenaiBuiltinInfo(Opcode.FLOAT_COPYSIGN, 2, 2),
    'complex?': MenaiBuiltinInfo(Opcode.COMPLEX_P, 1, 1),
    'complex=?': MenaiBuiltinInfo(Opcode.COMPLEX_EQ_P, 2, None),
    'complex!=?': MenaiBuiltinInfo(Opcode.COMPLEX_NEQ_P, 2, None),
    'complex-abs': MenaiBuiltinInfo(Opcode.COMPLEX_ABS, 1, 1),
    'complex+': MenaiBuiltinInfo(Opcode.COMPLEX_ADD, 0, None),
    'complex-': MenaiBuiltinInfo(Opcode.COMPLEX_SUB, 2, None),
    'complex*': MenaiBuiltinInfo(Opcode.COMPLEX_MUL, 0, None),
    'complex/': MenaiBuiltinInfo(Opcode.COMPLEX_DIV, 2, None),
    'complex-neg': MenaiBuiltinInfo(Opcode.COMPLEX_NEG, 1, 1),
    'complex-exp': MenaiBuiltinInfo(Opcode.COMPLEX_EXP, 1, 1),
    'complex-expn': MenaiBuiltinInfo(Opcode.COMPLEX_EXPN, 2, None),
    'complex-log': MenaiBuiltinInfo(Opcode.COMPLEX_LOG, 1, 1),
    'complex-log10': MenaiBuiltinInfo(Opcode.COMPLEX_LOG10, 1, 1),
    'complex-logn': MenaiBuiltinInfo(Opcode.COMPLEX_LOGN, 2, 2),
    'complex-sin': MenaiBuiltinInfo(Opcode.COMPLEX_SIN, 1, 1),
    'complex-cos': MenaiBuiltinInfo(Opcode.COMPLEX_COS, 1, 1),
    'complex-tan': MenaiBuiltinInfo(Opcode.COMPLEX_TAN, 1, 1),
    'complex-sqrt': MenaiBuiltinInfo(Opcode.COMPLEX_SQRT, 1, 1),
    'complex->string': MenaiBuiltinInfo(Opcode.COMPLEX_TO_STRING, 1, 1),
    'complex-real': MenaiBuiltinInfo(Opcode.COMPLEX_REAL, 1, 1),
    'complex-imag': MenaiBuiltinInfo(Opcode.COMPLEX_IMAG, 1, 1),
    'string?': MenaiBuiltinInfo(Opcode.STRING_P, 1, 1),
    'string=?': MenaiBuiltinInfo(Opcode.STRING_EQ_P, 2, None),
    'string!=?': MenaiBuiltinInfo(Opcode.STRING_NEQ_P, 2, None),
    'string<?': MenaiBuiltinInfo(Opcode.STRING_LT_P, 2, None),
    'string>?': MenaiBuiltinInfo(Opcode.STRING_GT_P, 2, None),
    'string<=?': MenaiBuiltinInfo(Opcode.STRING_LTE_P, 2, None),
    'string>=?': MenaiBuiltinInfo(Opcode.STRING_GTE_P, 2, None),
    'string-length': MenaiBuiltinInfo(Opcode.STRING_LENGTH, 1, 1),
    'string-upcase': MenaiBuiltinInfo(Opcode.STRING_UPCASE, 1, 1),
    'string-downcase': MenaiBuiltinInfo(Opcode.STRING_DOWNCASE, 1, 1),
    'string-trim': MenaiBuiltinInfo(Opcode.STRING_TRIM, 1, 1),
    'string-trim-left': MenaiBuiltinInfo(Opcode.STRING_TRIM_LEFT, 1, 1),
    'string-trim-right': MenaiBuiltinInfo(Opcode.STRING_TRIM_RIGHT, 1, 1),
    'string->integer': MenaiBuiltinInfo(Opcode.STRING_TO_INTEGER, 1, 2),
    'string->float': MenaiBuiltinInfo(Opcode.STRING_TO_FLOAT, 1, 1),
    'string->complex': MenaiBuiltinInfo(Opcode.STRING_TO_COMPLEX, 1, 1),
    'string->list': MenaiBuiltinInfo(Opcode.STRING_TO_LIST, 1, 2),
    'string-ref': MenaiBuiltinInfo(Opcode.STRING_REF, 2, 2),
    'string-index': MenaiBuiltinInfo(Opcode.STRING_INDEX, 2, 2),
    'string-prefix?': MenaiBuiltinInfo(Opcode.STRING_PREFIX_P, 2, 2),
    'string-suffix?': MenaiBuiltinInfo(Opcode.STRING_SUFFIX_P, 2, 2),
    'string->integer-codepoint': MenaiBuiltinInfo(Opcode.STRING_TO_INTEGER_CODEPOINT, 1, 1),
    'string-concat': MenaiBuiltinInfo(Opcode.STRING_CONCAT, 0, None),
    'string-slice': MenaiBuiltinInfo(Opcode.STRING_SLICE, 2, 3),
    'string-replace': MenaiBuiltinInfo(Opcode.STRING_REPLACE, 3, 3),
    'list?': MenaiBuiltinInfo(Opcode.LIST_P, 1, 1),
    'list=?': MenaiBuiltinInfo(Opcode.LIST_EQ_P, 2, None),
    'list!=?': MenaiBuiltinInfo(Opcode.LIST_NEQ_P, 2, None),
    'list-prepend': MenaiBuiltinInfo(Opcode.LIST_PREPEND, 2, 2),
    'list-append': MenaiBuiltinInfo(Opcode.LIST_APPEND, 2, 2),
    'list-reverse': MenaiBuiltinInfo(Opcode.LIST_REVERSE, 1, 1),
    'list-first': MenaiBuiltinInfo(Opcode.LIST_FIRST, 1, 1),
    'list-rest': MenaiBuiltinInfo(Opcode.LIST_REST, 1, 1),
    'list-last': MenaiBuiltinInfo(Opcode.LIST_LAST, 1, 1),
    'list-length': MenaiBuiltinInfo(Opcode.LIST_LENGTH, 1, 1),
    'list-ref': MenaiBuiltinInfo(Opcode.LIST_REF, 2, 2),
    'list-null?': MenaiBuiltinInfo(Opcode.LIST_NULL_P, 1, 1),
    'list-member?': MenaiBuiltinInfo(Opcode.LIST_MEMBER_P, 2, 2),
    'list-index': MenaiBuiltinInfo(Opcode.LIST_INDEX, 2, 2),
    'list-slice': MenaiBuiltinInfo(Opcode.LIST_SLICE, 2, 3),
    'list-remove': MenaiBuiltinInfo(Opcode.LIST_REMOVE, 2, 2),
    'list-concat': MenaiBuiltinInfo(Opcode.LIST_CONCAT, 0, None),
    'list->string': MenaiBuiltinInfo(Opcode.LIST_TO_STRING, 1, 2),
    'list->set': MenaiBuiltinInfo(Opcode.LIST_TO_SET, 1, 1),
    'dict?': MenaiBuiltinInfo(Opcode.DICT_P, 1, 1),
    'dict=?': MenaiBuiltinInfo(Opcode.DICT_EQ_P, 2, None),
    'dict!=?': MenaiBuiltinInfo(Opcode.DICT_NEQ_P, 2, None),
    'dict-keys': MenaiBuiltinInfo(Opcode.DICT_KEYS, 1, 1),
    'dict-values': MenaiBuiltinInfo(Opcode.DICT_VALUES, 1, 1),
    'dict-length': MenaiBuiltinInfo(Opcode.DICT_LENGTH, 1, 1),
    'dict-has?': MenaiBuiltinInfo(Opcode.DICT_HAS_P, 2, 2),
    'dict-remove': MenaiBuiltinInfo(Opcode.DICT_REMOVE, 2, 2),
    'dict-merge': MenaiBuiltinInfo(Opcode.DICT_MERGE, 2, 2),
    'dict-set': MenaiBuiltinInfo(Opcode.DICT_SET, 3, 3),
    'dict-get': MenaiBuiltinInfo(Opcode.DICT_GET, 2, 3),
    'set?': MenaiBuiltinInfo(Opcode.SET_P, 1, 1),
    'set=?': MenaiBuiltinInfo(Opcode.SET_EQ_P, 2, None),
    'set!=?': MenaiBuiltinInfo(Opcode.SET_NEQ_P, 2, None),
    'set-member?': MenaiBuiltinInfo(Opcode.SET_MEMBER_P, 2, 2),
    'set-add': MenaiBuiltinInfo(Opcode.SET_ADD, 2, 2),
    'set-remove': MenaiBuiltinInfo(Opcode.SET_REMOVE, 2, 2),
    'set-length': MenaiBuiltinInfo(Opcode.SET_LENGTH, 1, 1),
    'set-union': MenaiBuiltinInfo(Opcode.SET_UNION, 2, 2),
    'set-intersection': MenaiBuiltinInfo(Opcode.SET_INTERSECTION, 2, 2),
    'set-difference': MenaiBuiltinInfo(Opcode.SET_DIFFERENCE, 2, 2),
    'set-subset?': MenaiBuiltinInfo(Opcode.SET_SUBSET_P, 2, 2),
    'set->list': MenaiBuiltinInfo(Opcode.SET_TO_LIST, 1, 1),
    'struct?': MenaiBuiltinInfo(Opcode.STRUCT_P, 1, 1),
    'struct-is-instance?': MenaiBuiltinInfo(Opcode.STRUCT_IS_INSTANCE_P, 2, 2),
    'struct-get': MenaiBuiltinInfo(Opcode.STRUCT_GET, 2, 2),
    'struct-set': MenaiBuiltinInfo(Opcode.STRUCT_SET, 3, 3),
    'struct=?': MenaiBuiltinInfo(Opcode.STRUCT_EQ_P, 2, 2),
    'struct!=?': MenaiBuiltinInfo(Opcode.STRUCT_NEQ_P, 2, 2),
    'struct-type': MenaiBuiltinInfo(Opcode.STRUCT_TYPE, 1, 1),
    'structtype?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_P, 1, 1),
    'structtype=?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_EQ_P, 2, 2),
    'structtype!=?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_NEQ_P, 2, 2),
    'structtype-name': MenaiBuiltinInfo(Opcode.STRUCTTYPE_NAME, 1, 1),
    'structtype-fields': MenaiBuiltinInfo(Opcode.STRUCTTYPE_FIELDS, 1, 1),
    'range': MenaiBuiltinInfo(Opcode.RANGE, 2, 3),
    'bytes?': MenaiBuiltinInfo(Opcode.BYTES_P, 1, 1),
    'bytes=?': MenaiBuiltinInfo(Opcode.BYTES_EQ_P, 2, None),
    'bytes!=?': MenaiBuiltinInfo(Opcode.BYTES_NEQ_P, 2, None),
    'bytes-length': MenaiBuiltinInfo(Opcode.BYTES_LENGTH, 1, 1),
    'bytes-ref': MenaiBuiltinInfo(Opcode.BYTES_REF, 2, 2),
    'bytes-append-u8': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U8, 2, 2),
    'list->bytes': MenaiBuiltinInfo(Opcode.LIST_TO_BYTES, 1, 1),
    'bytes-slice': MenaiBuiltinInfo(Opcode.BYTES_SLICE, 2, 3),
    'string->bytes': MenaiBuiltinInfo(Opcode.STRING_TO_BYTES, 1, 1),
    'bytes->string': MenaiBuiltinInfo(Opcode.BYTES_TO_STRING, 1, 1),
    'bytes->list': MenaiBuiltinInfo(Opcode.BYTES_TO_LIST, 1, 1),
    'bytes->string-hex': MenaiBuiltinInfo(Opcode.BYTES_TO_STRING_HEX, 1, 1),
    'string-hex->bytes': MenaiBuiltinInfo(Opcode.STRING_HEX_TO_BYTES, 1, 1),
    'bytes-concat': MenaiBuiltinInfo(Opcode.BYTES_CONCAT, 0, None),
    'bytes-index': MenaiBuiltinInfo(Opcode.BYTES_INDEX, 2, 2),
    'bytes-index-int': MenaiBuiltinInfo(Opcode.BYTES_INDEX_INT, 2, 2),
    'bytes<?': MenaiBuiltinInfo(Opcode.BYTES_LT_P, 2, None),
    'bytes>?': MenaiBuiltinInfo(Opcode.BYTES_GT_P, 2, None),
    'bytes<=?': MenaiBuiltinInfo(Opcode.BYTES_LTE_P, 2, None),
    'bytes>=?': MenaiBuiltinInfo(Opcode.BYTES_GTE_P, 2, None),
    'bytes-read-u8': MenaiBuiltinInfo(Opcode.BYTES_READ_U8, 2, 2),
    'bytes-read-u16-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U16_LE, 2, 2),
    'bytes-read-u24-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U24_LE, 2, 2),
    'bytes-read-u32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U32_LE, 2, 2),
    'bytes-read-u64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U64_LE, 2, 2),
    'bytes-read-u16-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U16_BE, 2, 2),
    'bytes-read-u24-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U24_BE, 2, 2),
    'bytes-read-u32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U32_BE, 2, 2),
    'bytes-read-u64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U64_BE, 2, 2),
    'bytes-read-i8': MenaiBuiltinInfo(Opcode.BYTES_READ_I8, 2, 2),
    'bytes-read-i16-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I16_LE, 2, 2),
    'bytes-read-i24-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I24_LE, 2, 2),
    'bytes-read-i32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I32_LE, 2, 2),
    'bytes-read-i64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I64_LE, 2, 2),
    'bytes-read-i16-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I16_BE, 2, 2),
    'bytes-read-i24-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I24_BE, 2, 2),
    'bytes-read-i32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I32_BE, 2, 2),
    'bytes-read-i64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I64_BE, 2, 2),
    'bytes-append-u16-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U16_LE, 2, 2),
    'bytes-append-u16-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U16_BE, 2, 2),
    'bytes-append-u24-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U24_LE, 2, 2),
    'bytes-append-u24-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U24_BE, 2, 2),
    'bytes-append-u32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U32_LE, 2, 2),
    'bytes-append-u32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U32_BE, 2, 2),
    'bytes-append-u64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U64_LE, 2, 2),
    'bytes-append-u64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U64_BE, 2, 2),
    'bytes-append-i8': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I8, 2, 2),
    'bytes-append-i16-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I16_LE, 2, 2),
    'bytes-append-i16-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I16_BE, 2, 2),
    'bytes-append-i24-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I24_LE, 2, 2),
    'bytes-append-i24-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I24_BE, 2, 2),
    'bytes-append-i32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I32_LE, 2, 2),
    'bytes-append-i32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I32_BE, 2, 2),
    'bytes-append-i64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I64_LE, 2, 2),
    'bytes-append-i64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I64_BE, 2, 2),
    'bytes-write-u8': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U8, 3, 3),
    'bytes-write-u16-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U16_LE, 3, 3),
    'bytes-write-u16-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U16_BE, 3, 3),
    'bytes-write-u24-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U24_LE, 3, 3),
    'bytes-write-u24-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U24_BE, 3, 3),
    'bytes-write-u32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U32_LE, 3, 3),
    'bytes-write-u32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U32_BE, 3, 3),
    'bytes-write-u64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U64_LE, 3, 3),
    'bytes-write-u64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U64_BE, 3, 3),
    'bytes-write-i8': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I8, 3, 3),
    'bytes-write-i16-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I16_LE, 3, 3),
    'bytes-write-i16-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I16_BE, 3, 3),
    'bytes-write-i24-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I24_LE, 3, 3),
    'bytes-write-i24-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I24_BE, 3, 3),
    'bytes-write-i32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I32_LE, 3, 3),
    'bytes-write-i32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I32_BE, 3, 3),
    'bytes-write-i64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I64_LE, 3, 3),
    'bytes-write-i64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I64_BE, 3, 3),
    'bytes-read-uleb128': MenaiBuiltinInfo(Opcode.BYTES_READ_ULEB128, 2, 2),
    'bytes-append-uleb128': MenaiBuiltinInfo(Opcode.BYTES_APPEND_ULEB128, 2, 2),
    'bytes-read-sleb128': MenaiBuiltinInfo(Opcode.BYTES_READ_SLEB128, 2, 2),
    'bytes-append-sleb128': MenaiBuiltinInfo(Opcode.BYTES_APPEND_SLEB128, 2, 2),
    'bytes-hash-sha2-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_256, 1, 1),
    'bytes-hash-sha2-512': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_512, 1, 1),
    'bytes-hash-sha2-512-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_512_256, 1, 1),
    'bytes-hash-sha3-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA3_256, 1, 1),
    'bytes-crc32': MenaiBuiltinInfo(Opcode.BYTES_CRC32, 1, 1),
    'bytes-read-f32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_F32_LE, 2, 2),
    'bytes-read-f32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_F32_BE, 2, 2),
    'bytes-read-f64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_F64_LE, 2, 2),
    'bytes-read-f64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_F64_BE, 2, 2),
    'bytes-append-f32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F32_LE, 2, 2),
    'bytes-append-f32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F32_BE, 2, 2),
    'bytes-append-f64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F64_LE, 2, 2),
    'bytes-append-f64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F64_BE, 2, 2),
    'bytes-write-f32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F32_LE, 3, 3),
    'bytes-write-f32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F32_BE, 3, 3),
    'bytes-write-f64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F64_LE, 3, 3),
    'bytes-write-f64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F64_BE, 3, 3),
    'vector?': MenaiBuiltinInfo(Opcode.VECTOR_P, 1, 1),
    'vector=?': MenaiBuiltinInfo(Opcode.VECTOR_EQ_P, 2, 2),
    'vector!=?': MenaiBuiltinInfo(Opcode.VECTOR_NEQ_P, 2, 2),
    'vector-ref': MenaiBuiltinInfo(Opcode.VECTOR_REF, 2, 2),
    'vector-length': MenaiBuiltinInfo(Opcode.VECTOR_LENGTH, 1, 1),
    'vector-set': MenaiBuiltinInfo(Opcode.VECTOR_SET, 3, 3),
    'vector-slice': MenaiBuiltinInfo(Opcode.VECTOR_SLICE, 2, 3),
    'vector-concat': MenaiBuiltinInfo(Opcode.VECTOR_CONCAT, 2, 2),
    'vector-empty?': MenaiBuiltinInfo(Opcode.VECTOR_EMPTY_P, 1, 1),
    'vector-member?': MenaiBuiltinInfo(Opcode.VECTOR_MEMBER_P, 2, 2),
    'vector-index': MenaiBuiltinInfo(Opcode.VECTOR_INDEX, 2, 2),
    'vector->list': MenaiBuiltinInfo(Opcode.VECTOR_TO_LIST, 1, 1),
    'list->vector': MenaiBuiltinInfo(Opcode.LIST_TO_VECTOR, 1, 1),
}


class MenaiBuiltinRegistry:
    """
    Central registry for all builtin functions.

    Provides arity metadata (consumed by the semantic analyser) and the opcode
    for each builtin (consumed by the desugarer and code generator).
    """

    @staticmethod
    def get_builtin(name: str) -> MenaiBuiltinInfo | None:
        """
        Return the metadata for a known opcode-backed builtin, or None if unknown.
        """
        return BUILTINS.get(name)

    @staticmethod
    def get_function_arity(name: str) -> tuple[int, int | None] | None:
        """
        Return (min_args, max_args) for a known builtin function, or None if unknown.

        max_args is None for truly variadic functions (no upper bound).
        Used by the semantic analyser for call-site arity validation.
        """
        info = BUILTINS.get(name)
        if info is None:
            return None

        return (info.min_args, info.max_args)

    @staticmethod
    def is_primitive_name(name: str) -> bool:
        """
        Return True if name is a valid primitive function name.

        A primitive name has a direct $-prefixed form that the desugarer and
        semantic analyser can use.  The $ prefix is the source-level syntax
        for bypassing the variadic wrapper and calling the primitive directly.
        """
        return name in BUILTINS

    @staticmethod
    def get_primitive_arity(name: str) -> int | None:
        """
        Return the exact argument count of the primitive form of this builtin, or None.

        Returns None if this name has no primitive form (i.e. it is a pure-Menai
        stdlib function with no direct primitive backing).
        Used by the desugarer to decide whether (f a b) can be rewritten to ($f a b).
        """
        info = BUILTINS.get(name)
        if info is None:
            return None

        return info.opcode_arity()
