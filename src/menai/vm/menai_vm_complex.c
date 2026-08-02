/*
 * menai_vm_complex.c — MenaiComplex type implementation.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiComplex *
alloc_menai_complex(double real, double imag)
{
    MenaiComplex *self = (MenaiComplex *)menai_alloc(sizeof(MenaiComplex));
    if (!self) {
        return NULL;
    }

    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_COMPLEX;
    self->real = real;
    self->imag = imag;

    return self;
}
