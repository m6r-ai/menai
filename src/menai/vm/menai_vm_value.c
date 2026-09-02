/*
 * menai_vm_value.c
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

void
menai_value_free(MenaiVMState *vs, MenaiValue *v)
{
    switch (v->ob_type) {
    case MENAITYPE_BOOLEAN:
        /*
         * We can't free booleans.
         */
        assert(0);
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
        if (((MenaiList *)v)->head == NULL && ((MenaiList *)v)->tail == NULL) {
            /*
             * We can't free the empty list sentinel.
             */
            assert(0);
            break;
        }
        menai_list_final(vs, (MenaiList *)v);
        break;

    case MENAITYPE_NONE:
        /*
         * We can't free "none".
         */
        assert(0);
        break;

    case MENAITYPE_SET:
        menai_set_final(vs, (MenaiSet *)v);
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

    menai_free(vs, v);
}
