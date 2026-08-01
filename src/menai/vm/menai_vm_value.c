/*
 * menai_vm_value.c
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

void menai_value_free(MenaiValue *v)
{
    switch (v->ob_type) {
    case MENAITYPE_NONE:
        menai_none_free((MenaiNone *)v);
        break;

    case MENAITYPE_BOOLEAN:
        menai_boolean_free((MenaiBoolean *)v);
        break;

    case MENAITYPE_INTEGER:
        menai_integer_free((MenaiInteger *)v);
        break;

    case MENAITYPE_FLOAT:
        menai_float_free((MenaiFloat *)v);
        break;

    case MENAITYPE_COMPLEX:
        menai_complex_free((MenaiComplex *)v);
        break;

    case MENAITYPE_STRING:
        menai_string_free((MenaiString *)v);
        break;

    case MENAITYPE_SYMBOL:
        menai_symbol_free((MenaiSymbol *)v);
        break;

    case MENAITYPE_LIST:
        menai_list_free((MenaiList *)v);
        break;

    case MENAITYPE_DICT:
        menai_dict_free((MenaiDict *)v);
        break;

    case MENAITYPE_SET:
        menai_set_free((MenaiSet *)v);
        break;

    case MENAITYPE_FUNCTION:
        menai_function_free((MenaiFunction *)v);
        break;

    case MENAITYPE_STRUCTTYPE:
        menai_free_structtype((MenaiStructType *)v);
        break;

    case MENAITYPE_STRUCT:
        menai_free_struct((MenaiStruct *)v);
        break;

    case MENAITYPE_BYTES:
        menai_bytes_free((MenaiBytes *)v);
        break;

    default:
        assert(0);
    }
}