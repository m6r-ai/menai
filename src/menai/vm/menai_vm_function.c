/*
 * menai_vm_function.c — MenaiFunction type implementation.
 *
 * MenaiFunction represents a Menai closure.  It holds a retained reference to
 * a MenaiCodeObject (which owns all frame metadata) and an inline C array of
 * captured MenaiValue *s.  No Python objects are referenced after construction.
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

MenaiFunction *
alloc_menai_function(MenaiVMState *vs, MenaiCodeObject *co)
{
    ssize_t ncap = co->ncap;
    size_t sz = sizeof(MenaiFunction) + (size_t)ncap * sizeof(MenaiValue *);
    MenaiFunction *self = (MenaiFunction *)menai_alloc(vs, sz);
    if (!self) {
        return NULL;
    }

    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_FUNCTION;
    self->ncap = ncap;
    self->gc_mark = 0;
    menai_code_object_retain(co);
    self->bytecode = co;

    MenaiValue *none_val = (MenaiValue *)menai_none(vs);
    for (ssize_t i = 0; i < ncap; i++) {
        menai_value_retain((MenaiValue *)none_val);
        self->captures[i] = (MenaiValue *)none_val;
    }

    /* Register in the closure cycle collector registry. */
    if (vs->_closure_registry_count >= vs->_closure_registry_capacity) {
        ssize_t new_cap = vs->_closure_registry_capacity * 2;
        if (new_cap == 0) {
            new_cap = 64;
        }

        MenaiFunction **new_reg = (MenaiFunction **)realloc(
            vs->_closure_registry, (size_t)new_cap * sizeof(MenaiFunction *));
        if (new_reg != NULL) {
            vs->_closure_registry = new_reg;
            vs->_closure_registry_capacity = new_cap;
        }
    }

    if (vs->_closure_registry_count < vs->_closure_registry_capacity) {
        vs->_closure_registry[vs->_closure_registry_count++] = self;
    }

    return self;
}
