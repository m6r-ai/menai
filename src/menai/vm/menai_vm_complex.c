/*
 * menai_vm_complex.c — MenaiComplex type implementation.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiComplex *
alloc_menai_complex(MenaiVMState *vs, double real, double imag)
{
    MenaiComplex *self = (MenaiComplex *)menai_value_alloc(vs, MENAITYPE_COMPLEX, sizeof(MenaiComplex));
    if (!self) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    self->real = real;
    self->imag = imag;

    return self;
}
