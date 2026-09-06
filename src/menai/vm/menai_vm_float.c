/*
 * menai_vm_float.c — MenaiFloat type implementation.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiFloat *
alloc_menai_float(MenaiVMState *vs, double value)
{
    MenaiFloat *self = (MenaiFloat *)menai_value_alloc(vs, MENAITYPE_FLOAT, sizeof(MenaiFloat));
    if (self == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    self->value = value;
    return self;
}
