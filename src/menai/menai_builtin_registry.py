"""
Unified builtin function registry for Menai.

This module is the single source of truth for every opcode-backed builtin: the
opcode that implements it, the arity it presents to the surface language, and
whether it can raise.
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

    may_raise is True when the builtin can raise a runtime error for some
    argument values, even when every argument has the type the builtin
    requires.  A builtin that raises a type error only is not classified as
    raising, because the CFG's type facts and guard insertion establish
    argument types.  It is the classification an optimisation pass consults
    before moving an expression to a point where it executes on more paths
    than before; see ADR-0049.  It is a required field so that a builtin
    cannot be declared without its raising behaviour being considered.
    """

    opcode: Opcode
    min_args: int
    max_args: int | None
    may_raise: bool

    def opcode_arity(self) -> int:
        """Return the number of register operands the opcode takes."""
        return self.opcode.arg_count()


# Every opcode-backed builtin, keyed by its surface-language name.
#
# A builtin is opcode-backed if and only if it appears here.  Pure-Menai prelude
# functions (map-list, filter-list, fold-list, list, set, vector, etc.) are
# implemented as Menai lambdas in prelude.menai and must NOT be added here.
BUILTINS: dict[str, MenaiBuiltinInfo] = {
    'function?': MenaiBuiltinInfo(Opcode.FUNCTION_P, 1, 1, may_raise=False),
    'function=?': MenaiBuiltinInfo(Opcode.FUNCTION_EQ_P, 2, 2, may_raise=False),
    'function!=?': MenaiBuiltinInfo(Opcode.FUNCTION_NEQ_P, 2, 2, may_raise=False),
    'function-min-arity': MenaiBuiltinInfo(Opcode.FUNCTION_MIN_ARITY, 1, 1, may_raise=False),
    'function-variadic?': MenaiBuiltinInfo(Opcode.FUNCTION_VARIADIC_P, 1, 1, may_raise=False),
    'function-accepts?': MenaiBuiltinInfo(Opcode.FUNCTION_ACCEPTS_P, 2, 2, may_raise=False),
    'symbol?': MenaiBuiltinInfo(Opcode.SYMBOL_P, 1, 1, may_raise=False),
    'symbol=?': MenaiBuiltinInfo(Opcode.SYMBOL_EQ_P, 2, 2, may_raise=False),
    'symbol!=?': MenaiBuiltinInfo(Opcode.SYMBOL_NEQ_P, 2, 2, may_raise=False),
    'symbol->string': MenaiBuiltinInfo(Opcode.SYMBOL_TO_STRING, 1, 1, may_raise=False),
    'none?': MenaiBuiltinInfo(Opcode.NONE_P, 1, 1, may_raise=False),
    'boolean?': MenaiBuiltinInfo(Opcode.BOOLEAN_P, 1, 1, may_raise=False),
    'boolean=?': MenaiBuiltinInfo(Opcode.BOOLEAN_EQ_P, 2, None, may_raise=False),
    'boolean!=?': MenaiBuiltinInfo(Opcode.BOOLEAN_NEQ_P, 2, None, may_raise=False),
    'boolean-not': MenaiBuiltinInfo(Opcode.BOOLEAN_NOT, 1, 1, may_raise=False),
    'integer?': MenaiBuiltinInfo(Opcode.INTEGER_P, 1, 1, may_raise=False),
    'integer=?': MenaiBuiltinInfo(Opcode.INTEGER_EQ_P, 2, None, may_raise=False),
    'integer!=?': MenaiBuiltinInfo(Opcode.INTEGER_NEQ_P, 2, None, may_raise=False),
    'integer<?': MenaiBuiltinInfo(Opcode.INTEGER_LT_P, 2, None, may_raise=False),
    'integer>?': MenaiBuiltinInfo(Opcode.INTEGER_GT_P, 2, None, may_raise=False),
    'integer<=?': MenaiBuiltinInfo(Opcode.INTEGER_LTE_P, 2, None, may_raise=False),
    'integer>=?': MenaiBuiltinInfo(Opcode.INTEGER_GTE_P, 2, None, may_raise=False),
    'integer-abs': MenaiBuiltinInfo(Opcode.INTEGER_ABS, 1, 1, may_raise=False),
    'integer+': MenaiBuiltinInfo(Opcode.INTEGER_ADD, 0, None, may_raise=False),
    'integer-': MenaiBuiltinInfo(Opcode.INTEGER_SUB, 2, None, may_raise=False),
    'integer*': MenaiBuiltinInfo(Opcode.INTEGER_MUL, 0, None, may_raise=False),
    'integer/': MenaiBuiltinInfo(Opcode.INTEGER_DIV, 2, None, may_raise=True),
    'integer%': MenaiBuiltinInfo(Opcode.INTEGER_MOD, 2, 2, may_raise=True),
    'integer-neg': MenaiBuiltinInfo(Opcode.INTEGER_NEG, 1, 1, may_raise=False),
    'integer-expn': MenaiBuiltinInfo(Opcode.INTEGER_EXPN, 2, 2, may_raise=True),
    'integer-bit-not': MenaiBuiltinInfo(Opcode.INTEGER_BIT_NOT, 1, 1, may_raise=False),
    'integer-bit-shift-left': MenaiBuiltinInfo(Opcode.INTEGER_BIT_SHIFT_LEFT, 2, 2, may_raise=True),
    'integer-bit-shift-right': MenaiBuiltinInfo(Opcode.INTEGER_BIT_SHIFT_RIGHT, 2, 2, may_raise=True),
    'integer-bit-or': MenaiBuiltinInfo(Opcode.INTEGER_BIT_OR, 2, None, may_raise=False),
    'integer-bit-and': MenaiBuiltinInfo(Opcode.INTEGER_BIT_AND, 2, None, may_raise=False),
    'integer-bit-xor': MenaiBuiltinInfo(Opcode.INTEGER_BIT_XOR, 2, None, may_raise=False),
    'integer-min': MenaiBuiltinInfo(Opcode.INTEGER_MIN, 1, None, may_raise=False),
    'integer-max': MenaiBuiltinInfo(Opcode.INTEGER_MAX, 1, None, may_raise=False),
    'integer-gcd': MenaiBuiltinInfo(Opcode.INTEGER_GCD, 2, 2, may_raise=False),
    'integer-lcm': MenaiBuiltinInfo(Opcode.INTEGER_LCM, 2, 2, may_raise=False),
    'integer->float': MenaiBuiltinInfo(Opcode.INTEGER_TO_FLOAT, 1, 1, may_raise=False),
    'integer->complex': MenaiBuiltinInfo(Opcode.INTEGER_TO_COMPLEX, 1, 2, may_raise=False),
    'integer->string': MenaiBuiltinInfo(Opcode.INTEGER_TO_STRING, 1, 2, may_raise=False),
    'integer-codepoint->string': MenaiBuiltinInfo(Opcode.INTEGER_CODEPOINT_TO_STRING, 1, 1, may_raise=True),
    'float?': MenaiBuiltinInfo(Opcode.FLOAT_P, 1, 1, may_raise=False),
    'float=?': MenaiBuiltinInfo(Opcode.FLOAT_EQ_P, 2, None, may_raise=False),
    'float!=?': MenaiBuiltinInfo(Opcode.FLOAT_NEQ_P, 2, None, may_raise=False),
    'float<?': MenaiBuiltinInfo(Opcode.FLOAT_LT_P, 2, None, may_raise=False),
    'float>?': MenaiBuiltinInfo(Opcode.FLOAT_GT_P, 2, None, may_raise=False),
    'float<=?': MenaiBuiltinInfo(Opcode.FLOAT_LTE_P, 2, None, may_raise=False),
    'float>=?': MenaiBuiltinInfo(Opcode.FLOAT_GTE_P, 2, None, may_raise=False),
    'float-abs': MenaiBuiltinInfo(Opcode.FLOAT_ABS, 1, 1, may_raise=False),
    'float+': MenaiBuiltinInfo(Opcode.FLOAT_ADD, 0, None, may_raise=False),
    'float-': MenaiBuiltinInfo(Opcode.FLOAT_SUB, 2, None, may_raise=False),
    'float*': MenaiBuiltinInfo(Opcode.FLOAT_MUL, 0, None, may_raise=False),
    'float/': MenaiBuiltinInfo(Opcode.FLOAT_DIV, 2, None, may_raise=False),
    'float//': MenaiBuiltinInfo(Opcode.FLOAT_FLOOR_DIV, 2, 2, may_raise=False),
    'float%': MenaiBuiltinInfo(Opcode.FLOAT_MOD, 2, 2, may_raise=False),
    'float-neg': MenaiBuiltinInfo(Opcode.FLOAT_NEG, 1, 1, may_raise=False),
    'float-exp': MenaiBuiltinInfo(Opcode.FLOAT_EXP, 1, 1, may_raise=False),
    'float-expn': MenaiBuiltinInfo(Opcode.FLOAT_EXPN, 2, None, may_raise=False),
    'float-log': MenaiBuiltinInfo(Opcode.FLOAT_LOG, 1, 1, may_raise=True),
    'float-log10': MenaiBuiltinInfo(Opcode.FLOAT_LOG10, 1, 1, may_raise=True),
    'float-log2': MenaiBuiltinInfo(Opcode.FLOAT_LOG2, 1, 1, may_raise=True),
    'float-logn': MenaiBuiltinInfo(Opcode.FLOAT_LOGN, 2, 2, may_raise=True),
    'float-sin': MenaiBuiltinInfo(Opcode.FLOAT_SIN, 1, 1, may_raise=False),
    'float-cos': MenaiBuiltinInfo(Opcode.FLOAT_COS, 1, 1, may_raise=False),
    'float-tan': MenaiBuiltinInfo(Opcode.FLOAT_TAN, 1, 1, may_raise=False),
    'float-sqrt': MenaiBuiltinInfo(Opcode.FLOAT_SQRT, 1, 1, may_raise=True),
    'float->integer': MenaiBuiltinInfo(Opcode.FLOAT_TO_INTEGER, 1, 1, may_raise=False),
    'float->complex': MenaiBuiltinInfo(Opcode.FLOAT_TO_COMPLEX, 1, 2, may_raise=False),
    'float->string': MenaiBuiltinInfo(Opcode.FLOAT_TO_STRING, 1, 1, may_raise=False),
    'float-floor': MenaiBuiltinInfo(Opcode.FLOAT_FLOOR, 1, 1, may_raise=False),
    'float-ceil': MenaiBuiltinInfo(Opcode.FLOAT_CEIL, 1, 1, may_raise=False),
    'float-round': MenaiBuiltinInfo(Opcode.FLOAT_ROUND, 1, 1, may_raise=False),
    'float-min': MenaiBuiltinInfo(Opcode.FLOAT_MIN, 1, None, may_raise=False),
    'float-max': MenaiBuiltinInfo(Opcode.FLOAT_MAX, 1, None, may_raise=False),
    'float-atan2': MenaiBuiltinInfo(Opcode.FLOAT_ATAN2, 2, 2, may_raise=False),
    'float-cosh': MenaiBuiltinInfo(Opcode.FLOAT_COSH, 1, 1, may_raise=False),
    'float-sinh': MenaiBuiltinInfo(Opcode.FLOAT_SINH, 1, 1, may_raise=False),
    'float-tanh': MenaiBuiltinInfo(Opcode.FLOAT_TANH, 1, 1, may_raise=False),
    'float-asin': MenaiBuiltinInfo(Opcode.FLOAT_ASIN, 1, 1, may_raise=True),
    'float-acos': MenaiBuiltinInfo(Opcode.FLOAT_ACOS, 1, 1, may_raise=True),
    'float-atan': MenaiBuiltinInfo(Opcode.FLOAT_ATAN, 1, 1, may_raise=False),
    'float-hypot': MenaiBuiltinInfo(Opcode.FLOAT_HYPOT, 2, 2, may_raise=False),
    'float-exp2': MenaiBuiltinInfo(Opcode.FLOAT_EXP2, 1, 1, may_raise=False),
    'float-cbrt': MenaiBuiltinInfo(Opcode.FLOAT_CBRT, 1, 1, may_raise=False),
    'float-expm1': MenaiBuiltinInfo(Opcode.FLOAT_EXPM1, 1, 1, may_raise=False),
    'float-log1p': MenaiBuiltinInfo(Opcode.FLOAT_LOG1P, 1, 1, may_raise=True),
    'float-trunc': MenaiBuiltinInfo(Opcode.FLOAT_TRUNC, 1, 1, may_raise=False),
    'float-copysign': MenaiBuiltinInfo(Opcode.FLOAT_COPYSIGN, 2, 2, may_raise=False),
    'complex?': MenaiBuiltinInfo(Opcode.COMPLEX_P, 1, 1, may_raise=False),
    'complex=?': MenaiBuiltinInfo(Opcode.COMPLEX_EQ_P, 2, None, may_raise=False),
    'complex!=?': MenaiBuiltinInfo(Opcode.COMPLEX_NEQ_P, 2, None, may_raise=False),
    'complex-abs': MenaiBuiltinInfo(Opcode.COMPLEX_ABS, 1, 1, may_raise=False),
    'complex+': MenaiBuiltinInfo(Opcode.COMPLEX_ADD, 0, None, may_raise=False),
    'complex-': MenaiBuiltinInfo(Opcode.COMPLEX_SUB, 2, None, may_raise=False),
    'complex*': MenaiBuiltinInfo(Opcode.COMPLEX_MUL, 0, None, may_raise=False),
    'complex/': MenaiBuiltinInfo(Opcode.COMPLEX_DIV, 2, None, may_raise=True),
    'complex-neg': MenaiBuiltinInfo(Opcode.COMPLEX_NEG, 1, 1, may_raise=False),
    'complex-exp': MenaiBuiltinInfo(Opcode.COMPLEX_EXP, 1, 1, may_raise=False),
    'complex-expn': MenaiBuiltinInfo(Opcode.COMPLEX_EXPN, 2, None, may_raise=False),
    'complex-log': MenaiBuiltinInfo(Opcode.COMPLEX_LOG, 1, 1, may_raise=False),
    'complex-log10': MenaiBuiltinInfo(Opcode.COMPLEX_LOG10, 1, 1, may_raise=False),
    'complex-logn': MenaiBuiltinInfo(Opcode.COMPLEX_LOGN, 2, 2, may_raise=False),
    'complex-sin': MenaiBuiltinInfo(Opcode.COMPLEX_SIN, 1, 1, may_raise=False),
    'complex-cos': MenaiBuiltinInfo(Opcode.COMPLEX_COS, 1, 1, may_raise=False),
    'complex-tan': MenaiBuiltinInfo(Opcode.COMPLEX_TAN, 1, 1, may_raise=False),
    'complex-sqrt': MenaiBuiltinInfo(Opcode.COMPLEX_SQRT, 1, 1, may_raise=False),
    'complex->string': MenaiBuiltinInfo(Opcode.COMPLEX_TO_STRING, 1, 1, may_raise=False),
    'complex-real': MenaiBuiltinInfo(Opcode.COMPLEX_REAL, 1, 1, may_raise=False),
    'complex-imag': MenaiBuiltinInfo(Opcode.COMPLEX_IMAG, 1, 1, may_raise=False),
    'string?': MenaiBuiltinInfo(Opcode.STRING_P, 1, 1, may_raise=False),
    'string=?': MenaiBuiltinInfo(Opcode.STRING_EQ_P, 2, None, may_raise=False),
    'string!=?': MenaiBuiltinInfo(Opcode.STRING_NEQ_P, 2, None, may_raise=False),
    'string<?': MenaiBuiltinInfo(Opcode.STRING_LT_P, 2, None, may_raise=False),
    'string>?': MenaiBuiltinInfo(Opcode.STRING_GT_P, 2, None, may_raise=False),
    'string<=?': MenaiBuiltinInfo(Opcode.STRING_LTE_P, 2, None, may_raise=False),
    'string>=?': MenaiBuiltinInfo(Opcode.STRING_GTE_P, 2, None, may_raise=False),
    'string-length': MenaiBuiltinInfo(Opcode.STRING_LENGTH, 1, 1, may_raise=False),
    'string-upcase': MenaiBuiltinInfo(Opcode.STRING_UPCASE, 1, 1, may_raise=False),
    'string-downcase': MenaiBuiltinInfo(Opcode.STRING_DOWNCASE, 1, 1, may_raise=False),
    'string-trim': MenaiBuiltinInfo(Opcode.STRING_TRIM, 1, 1, may_raise=False),
    'string-trim-left': MenaiBuiltinInfo(Opcode.STRING_TRIM_LEFT, 1, 1, may_raise=False),
    'string-trim-right': MenaiBuiltinInfo(Opcode.STRING_TRIM_RIGHT, 1, 1, may_raise=False),
    'string->integer': MenaiBuiltinInfo(Opcode.STRING_TO_INTEGER, 1, 2, may_raise=True),
    'string->float': MenaiBuiltinInfo(Opcode.STRING_TO_FLOAT, 1, 1, may_raise=False),
    'string->complex': MenaiBuiltinInfo(Opcode.STRING_TO_COMPLEX, 1, 1, may_raise=False),
    'string->list': MenaiBuiltinInfo(Opcode.STRING_TO_LIST, 1, 2, may_raise=False),
    'string-nth': MenaiBuiltinInfo(Opcode.STRING_NTH, 2, 2, may_raise=True),
    'string-index': MenaiBuiltinInfo(Opcode.STRING_INDEX, 2, 2, may_raise=False),
    'string-prefix?': MenaiBuiltinInfo(Opcode.STRING_PREFIX_P, 2, 2, may_raise=False),
    'string-suffix?': MenaiBuiltinInfo(Opcode.STRING_SUFFIX_P, 2, 2, may_raise=False),
    'string->integer-codepoint': MenaiBuiltinInfo(Opcode.STRING_TO_INTEGER_CODEPOINT, 1, 1, may_raise=True),
    'string-concat': MenaiBuiltinInfo(Opcode.STRING_CONCAT, 0, None, may_raise=False),
    'string-slice': MenaiBuiltinInfo(Opcode.STRING_SLICE, 2, 3, may_raise=True),
    'string-replace': MenaiBuiltinInfo(Opcode.STRING_REPLACE, 3, 3, may_raise=False),
    'list?': MenaiBuiltinInfo(Opcode.LIST_P, 1, 1, may_raise=False),
    'list=?': MenaiBuiltinInfo(Opcode.LIST_EQ_P, 2, None, may_raise=False),
    'list!=?': MenaiBuiltinInfo(Opcode.LIST_NEQ_P, 2, None, may_raise=False),
    'list-prepend': MenaiBuiltinInfo(Opcode.LIST_PREPEND, 2, 2, may_raise=False),
    'list-append': MenaiBuiltinInfo(Opcode.LIST_APPEND, 2, 2, may_raise=False),
    'list-reverse': MenaiBuiltinInfo(Opcode.LIST_REVERSE, 1, 1, may_raise=False),
    'list-first': MenaiBuiltinInfo(Opcode.LIST_FIRST, 1, 1, may_raise=True),
    'list-rest': MenaiBuiltinInfo(Opcode.LIST_REST, 1, 1, may_raise=False),
    'list-last': MenaiBuiltinInfo(Opcode.LIST_LAST, 1, 1, may_raise=True),
    'list-length': MenaiBuiltinInfo(Opcode.LIST_LENGTH, 1, 1, may_raise=False),
    'list-nth': MenaiBuiltinInfo(Opcode.LIST_NTH, 2, 2, may_raise=True),
    'list-null?': MenaiBuiltinInfo(Opcode.LIST_NULL_P, 1, 1, may_raise=False),
    'list-member?': MenaiBuiltinInfo(Opcode.LIST_MEMBER_P, 2, 2, may_raise=False),
    'list-index': MenaiBuiltinInfo(Opcode.LIST_INDEX, 2, 2, may_raise=False),
    'list-slice': MenaiBuiltinInfo(Opcode.LIST_SLICE, 2, 3, may_raise=True),
    'list-without': MenaiBuiltinInfo(Opcode.LIST_WITHOUT, 2, 2, may_raise=False),
    'list-concat': MenaiBuiltinInfo(Opcode.LIST_CONCAT, 0, None, may_raise=False),
    'list->string': MenaiBuiltinInfo(Opcode.LIST_TO_STRING, 1, 2, may_raise=True),
    'list->set': MenaiBuiltinInfo(Opcode.LIST_TO_SET, 1, 1, may_raise=True),
    'dict?': MenaiBuiltinInfo(Opcode.DICT_P, 1, 1, may_raise=False),
    'dict=?': MenaiBuiltinInfo(Opcode.DICT_EQ_P, 2, None, may_raise=False),
    'dict!=?': MenaiBuiltinInfo(Opcode.DICT_NEQ_P, 2, None, may_raise=False),
    'dict-keys': MenaiBuiltinInfo(Opcode.DICT_KEYS, 1, 1, may_raise=False),
    'dict-values': MenaiBuiltinInfo(Opcode.DICT_VALUES, 1, 1, may_raise=False),
    'dict-length': MenaiBuiltinInfo(Opcode.DICT_LENGTH, 1, 1, may_raise=False),
    'dict-has?': MenaiBuiltinInfo(Opcode.DICT_HAS_P, 2, 2, may_raise=False),
    'dict-without': MenaiBuiltinInfo(Opcode.DICT_WITHOUT, 2, 2, may_raise=True),
    'dict-merge': MenaiBuiltinInfo(Opcode.DICT_MERGE, 2, 2, may_raise=True),
    'dict-with': MenaiBuiltinInfo(Opcode.DICT_WITH, 3, 3, may_raise=True),
    'dict-get': MenaiBuiltinInfo(Opcode.DICT_GET, 2, 3, may_raise=True),
    'set?': MenaiBuiltinInfo(Opcode.SET_P, 1, 1, may_raise=False),
    'set=?': MenaiBuiltinInfo(Opcode.SET_EQ_P, 2, None, may_raise=False),
    'set!=?': MenaiBuiltinInfo(Opcode.SET_NEQ_P, 2, None, may_raise=False),
    'set-member?': MenaiBuiltinInfo(Opcode.SET_MEMBER_P, 2, 2, may_raise=False),
    'set-with': MenaiBuiltinInfo(Opcode.SET_WITH, 2, 2, may_raise=True),
    'set-without': MenaiBuiltinInfo(Opcode.SET_WITHOUT, 2, 2, may_raise=True),
    'set-length': MenaiBuiltinInfo(Opcode.SET_LENGTH, 1, 1, may_raise=False),
    'set-union': MenaiBuiltinInfo(Opcode.SET_UNION, 2, 2, may_raise=True),
    'set-intersection': MenaiBuiltinInfo(Opcode.SET_INTERSECTION, 2, 2, may_raise=True),
    'set-difference': MenaiBuiltinInfo(Opcode.SET_DIFFERENCE, 2, 2, may_raise=True),
    'set-subset?': MenaiBuiltinInfo(Opcode.SET_SUBSET_P, 2, 2, may_raise=False),
    'set->list': MenaiBuiltinInfo(Opcode.SET_TO_LIST, 1, 1, may_raise=False),
    'struct?': MenaiBuiltinInfo(Opcode.STRUCT_P, 1, 1, may_raise=False),
    'struct-is-instance?': MenaiBuiltinInfo(Opcode.STRUCT_IS_INSTANCE_P, 2, 2, may_raise=False),
    'struct-get': MenaiBuiltinInfo(Opcode.STRUCT_GET, 2, 2, may_raise=True),
    'struct-with': MenaiBuiltinInfo(Opcode.STRUCT_WITH, 3, 3, may_raise=True),
    'struct=?': MenaiBuiltinInfo(Opcode.STRUCT_EQ_P, 2, 2, may_raise=False),
    'struct!=?': MenaiBuiltinInfo(Opcode.STRUCT_NEQ_P, 2, 2, may_raise=False),
    'struct-type': MenaiBuiltinInfo(Opcode.STRUCT_TYPE, 1, 1, may_raise=False),
    'structtype?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_P, 1, 1, may_raise=False),
    'structtype=?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_EQ_P, 2, 2, may_raise=False),
    'structtype!=?': MenaiBuiltinInfo(Opcode.STRUCTTYPE_NEQ_P, 2, 2, may_raise=False),
    'structtype-name': MenaiBuiltinInfo(Opcode.STRUCTTYPE_NAME, 1, 1, may_raise=False),
    'structtype-fields': MenaiBuiltinInfo(Opcode.STRUCTTYPE_FIELDS, 1, 1, may_raise=False),
    'enum?': MenaiBuiltinInfo(Opcode.ENUM_P, 1, 1, may_raise=False),
    'enum=?': MenaiBuiltinInfo(Opcode.ENUM_EQ_P, 2, 2, may_raise=False),
    'enum!=?': MenaiBuiltinInfo(Opcode.ENUM_NEQ_P, 2, 2, may_raise=False),
    'enum-variant': MenaiBuiltinInfo(Opcode.ENUM_VARIANT, 1, 1, may_raise=False),
    'enum-type': MenaiBuiltinInfo(Opcode.ENUM_TYPE, 1, 1, may_raise=False),
    'enum-is-instance?': MenaiBuiltinInfo(Opcode.ENUM_IS_INSTANCE_P, 2, 2, may_raise=False),
    'enumtype?': MenaiBuiltinInfo(Opcode.ENUMTYPE_P, 1, 1, may_raise=False),
    'enumtype=?': MenaiBuiltinInfo(Opcode.ENUMTYPE_EQ_P, 2, 2, may_raise=False),
    'enumtype!=?': MenaiBuiltinInfo(Opcode.ENUMTYPE_NEQ_P, 2, 2, may_raise=False),
    'enumtype-name': MenaiBuiltinInfo(Opcode.ENUMTYPE_NAME, 1, 1, may_raise=False),
    'enumtype-variants': MenaiBuiltinInfo(Opcode.ENUMTYPE_VARIANTS, 1, 1, may_raise=False),
    'range': MenaiBuiltinInfo(Opcode.RANGE, 2, 3, may_raise=True),
    'bytes?': MenaiBuiltinInfo(Opcode.BYTES_P, 1, 1, may_raise=False),
    'bytes=?': MenaiBuiltinInfo(Opcode.BYTES_EQ_P, 2, None, may_raise=False),
    'bytes!=?': MenaiBuiltinInfo(Opcode.BYTES_NEQ_P, 2, None, may_raise=False),
    'bytes-length': MenaiBuiltinInfo(Opcode.BYTES_LENGTH, 1, 1, may_raise=False),
    'bytes-nth': MenaiBuiltinInfo(Opcode.BYTES_NTH, 2, 2, may_raise=True),
    'bytes-append-u8': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U8, 2, 2, may_raise=True),
    'list->bytes': MenaiBuiltinInfo(Opcode.LIST_TO_BYTES, 1, 1, may_raise=True),
    'bytes-slice': MenaiBuiltinInfo(Opcode.BYTES_SLICE, 2, 3, may_raise=True),
    'string->bytes': MenaiBuiltinInfo(Opcode.STRING_TO_BYTES, 1, 1, may_raise=False),
    'bytes->string': MenaiBuiltinInfo(Opcode.BYTES_TO_STRING, 1, 1, may_raise=True),
    'bytes->list': MenaiBuiltinInfo(Opcode.BYTES_TO_LIST, 1, 1, may_raise=False),
    'bytes->string-hex': MenaiBuiltinInfo(Opcode.BYTES_TO_STRING_HEX, 1, 1, may_raise=False),
    'string-hex->bytes': MenaiBuiltinInfo(Opcode.STRING_HEX_TO_BYTES, 1, 1, may_raise=True),
    'bytes-concat': MenaiBuiltinInfo(Opcode.BYTES_CONCAT, 0, None, may_raise=False),
    'bytes-index': MenaiBuiltinInfo(Opcode.BYTES_INDEX, 2, 2, may_raise=False),
    'bytes-index-int': MenaiBuiltinInfo(Opcode.BYTES_INDEX_INT, 2, 2, may_raise=False),
    'bytes<?': MenaiBuiltinInfo(Opcode.BYTES_LT_P, 2, None, may_raise=False),
    'bytes>?': MenaiBuiltinInfo(Opcode.BYTES_GT_P, 2, None, may_raise=False),
    'bytes<=?': MenaiBuiltinInfo(Opcode.BYTES_LTE_P, 2, None, may_raise=False),
    'bytes>=?': MenaiBuiltinInfo(Opcode.BYTES_GTE_P, 2, None, may_raise=False),
    'bytes-read-u8': MenaiBuiltinInfo(Opcode.BYTES_READ_U8, 2, 2, may_raise=True),
    'bytes-read-u16-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U16_LE, 2, 2, may_raise=True),
    'bytes-read-u24-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U24_LE, 2, 2, may_raise=True),
    'bytes-read-u32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U32_LE, 2, 2, may_raise=True),
    'bytes-read-u64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_U64_LE, 2, 2, may_raise=True),
    'bytes-read-u16-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U16_BE, 2, 2, may_raise=True),
    'bytes-read-u24-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U24_BE, 2, 2, may_raise=True),
    'bytes-read-u32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U32_BE, 2, 2, may_raise=True),
    'bytes-read-u64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_U64_BE, 2, 2, may_raise=True),
    'bytes-read-i8': MenaiBuiltinInfo(Opcode.BYTES_READ_I8, 2, 2, may_raise=True),
    'bytes-read-i16-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I16_LE, 2, 2, may_raise=True),
    'bytes-read-i24-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I24_LE, 2, 2, may_raise=True),
    'bytes-read-i32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I32_LE, 2, 2, may_raise=True),
    'bytes-read-i64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_I64_LE, 2, 2, may_raise=True),
    'bytes-read-i16-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I16_BE, 2, 2, may_raise=True),
    'bytes-read-i24-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I24_BE, 2, 2, may_raise=True),
    'bytes-read-i32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I32_BE, 2, 2, may_raise=True),
    'bytes-read-i64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_I64_BE, 2, 2, may_raise=True),
    'bytes-append-u16-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U16_LE, 2, 2, may_raise=True),
    'bytes-append-u16-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U16_BE, 2, 2, may_raise=True),
    'bytes-append-u24-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U24_LE, 2, 2, may_raise=True),
    'bytes-append-u24-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U24_BE, 2, 2, may_raise=True),
    'bytes-append-u32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U32_LE, 2, 2, may_raise=True),
    'bytes-append-u32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U32_BE, 2, 2, may_raise=True),
    'bytes-append-u64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U64_LE, 2, 2, may_raise=True),
    'bytes-append-u64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_U64_BE, 2, 2, may_raise=True),
    'bytes-append-i8': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I8, 2, 2, may_raise=True),
    'bytes-append-i16-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I16_LE, 2, 2, may_raise=True),
    'bytes-append-i16-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I16_BE, 2, 2, may_raise=True),
    'bytes-append-i24-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I24_LE, 2, 2, may_raise=True),
    'bytes-append-i24-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I24_BE, 2, 2, may_raise=True),
    'bytes-append-i32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I32_LE, 2, 2, may_raise=True),
    'bytes-append-i32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I32_BE, 2, 2, may_raise=True),
    'bytes-append-i64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I64_LE, 2, 2, may_raise=True),
    'bytes-append-i64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_I64_BE, 2, 2, may_raise=True),
    'bytes-write-u8': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U8, 3, 3, may_raise=True),
    'bytes-write-u16-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U16_LE, 3, 3, may_raise=True),
    'bytes-write-u16-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U16_BE, 3, 3, may_raise=True),
    'bytes-write-u24-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U24_LE, 3, 3, may_raise=True),
    'bytes-write-u24-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U24_BE, 3, 3, may_raise=True),
    'bytes-write-u32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U32_LE, 3, 3, may_raise=True),
    'bytes-write-u32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U32_BE, 3, 3, may_raise=True),
    'bytes-write-u64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U64_LE, 3, 3, may_raise=True),
    'bytes-write-u64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_U64_BE, 3, 3, may_raise=True),
    'bytes-write-i8': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I8, 3, 3, may_raise=True),
    'bytes-write-i16-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I16_LE, 3, 3, may_raise=True),
    'bytes-write-i16-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I16_BE, 3, 3, may_raise=True),
    'bytes-write-i24-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I24_LE, 3, 3, may_raise=True),
    'bytes-write-i24-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I24_BE, 3, 3, may_raise=True),
    'bytes-write-i32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I32_LE, 3, 3, may_raise=True),
    'bytes-write-i32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I32_BE, 3, 3, may_raise=True),
    'bytes-write-i64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I64_LE, 3, 3, may_raise=True),
    'bytes-write-i64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_I64_BE, 3, 3, may_raise=True),
    'bytes-read-uleb128': MenaiBuiltinInfo(Opcode.BYTES_READ_ULEB128, 2, 2, may_raise=True),
    'bytes-append-uleb128': MenaiBuiltinInfo(Opcode.BYTES_APPEND_ULEB128, 2, 2, may_raise=False),
    'bytes-read-sleb128': MenaiBuiltinInfo(Opcode.BYTES_READ_SLEB128, 2, 2, may_raise=True),
    'bytes-append-sleb128': MenaiBuiltinInfo(Opcode.BYTES_APPEND_SLEB128, 2, 2, may_raise=False),
    'bytes-hash-sha2-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_256, 1, 1, may_raise=False),
    'bytes-hash-sha2-512': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_512, 1, 1, may_raise=False),
    'bytes-hash-sha2-512-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA2_512_256, 1, 1, may_raise=False),
    'bytes-hash-sha3-256': MenaiBuiltinInfo(Opcode.BYTES_HASH_SHA3_256, 1, 1, may_raise=False),
    'bytes-crc32': MenaiBuiltinInfo(Opcode.BYTES_CRC32, 1, 1, may_raise=False),
    'bytes-read-f32-le': MenaiBuiltinInfo(Opcode.BYTES_READ_F32_LE, 2, 2, may_raise=True),
    'bytes-read-f32-be': MenaiBuiltinInfo(Opcode.BYTES_READ_F32_BE, 2, 2, may_raise=True),
    'bytes-read-f64-le': MenaiBuiltinInfo(Opcode.BYTES_READ_F64_LE, 2, 2, may_raise=True),
    'bytes-read-f64-be': MenaiBuiltinInfo(Opcode.BYTES_READ_F64_BE, 2, 2, may_raise=True),
    'bytes-append-f32-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F32_LE, 2, 2, may_raise=True),
    'bytes-append-f32-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F32_BE, 2, 2, may_raise=True),
    'bytes-append-f64-le': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F64_LE, 2, 2, may_raise=True),
    'bytes-append-f64-be': MenaiBuiltinInfo(Opcode.BYTES_APPEND_F64_BE, 2, 2, may_raise=True),
    'bytes-write-f32-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F32_LE, 3, 3, may_raise=True),
    'bytes-write-f32-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F32_BE, 3, 3, may_raise=True),
    'bytes-write-f64-le': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F64_LE, 3, 3, may_raise=True),
    'bytes-write-f64-be': MenaiBuiltinInfo(Opcode.BYTES_WRITE_F64_BE, 3, 3, may_raise=True),
    'vector?': MenaiBuiltinInfo(Opcode.VECTOR_P, 1, 1, may_raise=False),
    'vector=?': MenaiBuiltinInfo(Opcode.VECTOR_EQ_P, 2, 2, may_raise=False),
    'vector!=?': MenaiBuiltinInfo(Opcode.VECTOR_NEQ_P, 2, 2, may_raise=False),
    'vector-nth': MenaiBuiltinInfo(Opcode.VECTOR_NTH, 2, 2, may_raise=True),
    'vector-length': MenaiBuiltinInfo(Opcode.VECTOR_LENGTH, 1, 1, may_raise=False),
    'vector-with': MenaiBuiltinInfo(Opcode.VECTOR_WITH, 3, 3, may_raise=True),
    'vector-slice': MenaiBuiltinInfo(Opcode.VECTOR_SLICE, 2, 3, may_raise=True),
    'vector-concat': MenaiBuiltinInfo(Opcode.VECTOR_CONCAT, 2, 2, may_raise=False),
    'vector-empty?': MenaiBuiltinInfo(Opcode.VECTOR_EMPTY_P, 1, 1, may_raise=False),
    'vector-member?': MenaiBuiltinInfo(Opcode.VECTOR_MEMBER_P, 2, 2, may_raise=False),
    'vector-index': MenaiBuiltinInfo(Opcode.VECTOR_INDEX, 2, 2, may_raise=False),
    'vector->list': MenaiBuiltinInfo(Opcode.VECTOR_TO_LIST, 1, 1, may_raise=False),
    'list->vector': MenaiBuiltinInfo(Opcode.LIST_TO_VECTOR, 1, 1, may_raise=False),
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

    @staticmethod
    def builtin_may_raise(name: str) -> bool:
        """
        Return True if the named builtin can raise for some argument values.

        The result is True only when every argument has the type the builtin
        requires: a builtin that raises a type error only is not classified as
        raising, because the CFG's type facts and guard insertion establish
        argument types.  An unknown name returns False, matching the treatment
        of a name that is not an opcode-backed builtin.

        This is the classification an optimisation pass consults before moving
        an expression to a point where it executes on more paths than before;
        see ADR-0049.
        """
        info = BUILTINS.get(name)
        return info.may_raise if info is not None else False
