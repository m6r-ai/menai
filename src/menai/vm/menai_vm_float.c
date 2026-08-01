/*
 * menai_vm_float.c — MenaiFloat type implementation.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiFloat *
menai_alloc_float(double value)
{
    MenaiFloat *self = (MenaiFloat *)menai_alloc(sizeof(MenaiFloat));
    if (self == NULL) {
        return NULL;
    }

    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_FLOAT;
    self->value = value;
    return self;
}
