/*
 * menai_vm_value.c
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

void
menai_value_free(MenaiVMState *vs, MenaiValue *v)
{
    MenaiPoolHeader *ph = menai_get_pool_header((void *)v);

    switch (ph->ob_type) {
    case MENAITYPE_BOOLEAN:
        menai_boolean_final(vs, (MenaiBoolean *)v);
        break;

    case MENAITYPE_BYTES:
        menai_bytes_final(vs, (MenaiBytes *)v);
        break;

    case MENAITYPE_COMPLEX:
        menai_complex_final(vs, (MenaiComplex *)v);
        break;

    case MENAITYPE_DICT:
        menai_dict_final(vs, (MenaiDict *)v);
        break;

    case MENAITYPE_DICT_ELEMENT:
        menai_dict_element_final(vs, (MenaiDictElement *)v);
        break;

    case MENAITYPE_FLOAT:
        menai_float_final(vs, (MenaiFloat *)v);
        break;

    case MENAITYPE_FUNCTION:
        menai_function_final(vs, (MenaiFunction *)v);
        break;

    case MENAITYPE_INTEGER:
        menai_integer_final(vs, (MenaiInteger *)v);
        break;

    case MENAITYPE_LIST:
        menai_list_final(vs, (MenaiList *)v);
        break;

    case MENAITYPE_NONE:
        menai_none_final(vs, (MenaiNone *)v);
        break;

    case MENAITYPE_SET:
        menai_set_final(vs, (MenaiSet *)v);
        break;

    case MENAITYPE_SET_ELEMENT:
        menai_set_element_final(vs, (MenaiSetElement *)v);
        break;

    case MENAITYPE_STRING:
        menai_string_final(vs, (MenaiString *)v);
        break;

    case MENAITYPE_STRUCT:
        menai_struct_final(vs, (MenaiStruct *)v);
        break;

    case MENAITYPE_STRUCTTYPE:
        menai_structtype_final(vs, (MenaiStructType *)v);
        break;

    case MENAITYPE_SYMBOL:
        menai_symbol_final(vs, (MenaiSymbol *)v);
        break;

    default:
        assert(0);
    }

    MENAI_CLEAR_MAGIC(v);

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_remove(&vs->_leak_set, v);
#endif

    menai_pool_free(vs, v);
}
