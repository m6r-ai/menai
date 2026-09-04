/*
 * menai_vm_struct.c — MenaiStruct type implementations.
 *
 * MenaiStruct: field values are stored in an inline C array (nfields entries),
 * eliminating the Python tuple previously heap-allocated on every struct
 * construction.
 */
#include "menai_vm_c.h"

MenaiStruct *
alloc_menai_struct(MenaiVMState *vs, MenaiStructType *struct_type, MenaiValue **field_values, ssize_t nfields)
{
    size_t sz = sizeof(MenaiStruct) + (size_t)nfields * sizeof(MenaiValue *);
    MenaiStruct *self = (MenaiStruct *)menai_alloc(vs, sz);
    if (!self) {
        return NULL;
    }

    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_STRUCT;
    self->nfields = (int)nfields;
    menai_value_retain((MenaiValue *)struct_type);
    self->struct_type = struct_type;

    for (ssize_t i = 0; i < nfields; i++) {
        menai_value_retain(field_values[i]);
        self->items[i] = field_values[i];
    }

    return self;
}

MenaiStruct *
alloc_menai_struct_from_set_operation(MenaiVMState *vs, MenaiStruct *src, ssize_t field_index, MenaiValue *new_val)
{
    ssize_t nf = src->nfields;
    size_t sz = sizeof(MenaiStruct) + (size_t)nf * sizeof(MenaiValue *);
    MenaiStruct *self = (MenaiStruct *)menai_alloc(vs, sz);
    if (!self) {
        return NULL;
    }

    MenaiStructType *struct_type = src->struct_type;
    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_STRUCT;
    self->nfields = (int)nf;
    menai_value_retain((MenaiValue *)struct_type);
    self->struct_type = struct_type;

    for (ssize_t i = 0; i < nf; i++) {
        MenaiValue *fv = (i == field_index) ? new_val : src->items[i];
        menai_value_retain(fv);
        self->items[i] = fv;
    }

    return self;
}
