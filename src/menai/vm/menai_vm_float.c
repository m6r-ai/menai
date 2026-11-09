/*
 * menai_vm_float.c — MenaiFloat type implementation.
 *
 * A float is an inline immediate when its exponent falls in the window described by
 * MENAI_FLOAT_EXP_BASE, and a boxed MenaiFloat otherwise.  This constructor is the
 * only place that chooses, so a given value always has the same representation and
 * the two paths for hashing and equality cannot disagree.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiValue *
alloc_menai_float(MenaiVMState *vs, double value)
{
    if (menai_float_is_inline(value)) {
        return menai_make_inline_float(value);
    }

    MenaiFloat *self = (MenaiFloat *)menai_value_alloc(vs, MENAITYPE_FLOAT, sizeof(MenaiFloat));
    if (self == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    self->value = value;
    return (MenaiValue *)self;
}
