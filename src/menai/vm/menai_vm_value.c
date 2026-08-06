/*
 * menai_vm_value.c
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

static inline void
menai_boolean_free(MenaiVMState *vs, MenaiBoolean *self)
{
    /*
     * Singletons are never freed.
     */
    (void)vs;
    (void)self;
}

static inline void
menai_bytes_free(MenaiVMState *vs, MenaiBytes *self)
{
    if (self->owner != NULL) {
        /* View — release the backing owner; do not touch the data array. */
        menai_value_release(vs, (MenaiValue *)self->owner);
        menai_free(vs, self);
        return;
    }

    /* Owner — the data array is inline, freed with the struct. */
    menai_free(vs, self);
}

static inline void
menai_complex_free(MenaiVMState *vs, MenaiComplex *self)
{
    menai_free(vs, self);
}

static inline void
menai_dict_free(MenaiVMState *vs, MenaiDict *self)
{
    ssize_t n = self->length;

    if (self->keys) {
        for (ssize_t i = 0; i < n; i++) {
            menai_value_release(vs, self->keys[i]);
        }

        free(self->keys);
    }

    if (self->values) {
        for (ssize_t i = 0; i < n; i++) {
            menai_value_release(vs, self->values[i]);
        }

        free(self->values);
    }

    free(self->hashes);
    menai_ht_free(&self->ht);
    menai_free(vs, self);
}

static inline void
menai_float_free(MenaiVMState *vs, MenaiFloat *self)
{
    menai_free(vs, self);
}

static inline void
menai_function_free(MenaiVMState *vs, MenaiFunction *self)
{
    menai_code_object_release(vs, self->bytecode);
    ssize_t ncap = self->ncap;
    for (ssize_t i = 0; i < ncap; i++) {
        menai_value_xrelease(vs, self->captures[i]);
    }

    menai_free(vs, self);
}

static inline void
menai_integer_free(MenaiVMState *vs, MenaiInteger *self)
{
    if (!self->is_big) {
        long v = self->fixed;
        if (v >= MENAI_INT_CACHE_MIN && v <= MENAI_INT_CACHE_MAX) {
            /*
             * Cached singleton — must never be freed.  Restore refcount so
             * the object remains live.
             */
            self->ob_refcnt = 1;
            return;
        }
    } else {
        menai_bigint_final(&self->big);
    }

    menai_free(vs, self);
}

static inline void
menai_list_free(MenaiVMState *vs, MenaiList *self)
{
    if (self->owner != NULL) {
        /* View — release the backing list; do not touch the element array. */
        menai_value_release(vs, (MenaiValue *)self->owner);
        menai_free(vs, self);
        return;
    }

    /* Owner — release all elements then free the combined block. */
    ssize_t n = self->length;
    MenaiValue **arr = self->elements;
    for (ssize_t i = 0; i < n; i++) {
        menai_value_release(vs, *arr++);
    }

    menai_free(vs, self);
}

static inline void
menai_none_free(MenaiVMState *vs, MenaiNone *self)
{
    /*
     * The singleton is never freed — its refcount should never reach zero.
     */
    (void)vs;
    (void)self;
}

static inline void
menai_set_free(MenaiVMState *vs, MenaiSet *self)
{
    ssize_t n = self->length;
    for (ssize_t i = 0; i < n; i++) {
        menai_value_release(vs, self->elements[i]);
    }

    menai_ht_free(&self->ht);
    menai_free(vs, self);
}

static inline void
menai_string_free(MenaiVMState *vs, MenaiString *self)
{
    menai_free(vs, self);
}

static inline void
menai_struct_free(MenaiVMState *vs, MenaiStruct *self)
{
    menai_value_xrelease(vs, (MenaiValue *)self->struct_type);
    int n = self->nfields;
    for (int i = 0; i < n; i++) {
        menai_value_xrelease(vs, self->items[i]);
    }

    menai_free(vs, self);
}

static inline void
menai_structtype_free(MenaiVMState *vs, MenaiStructType *self)
{
    menai_ht_free(&self->field_ht);
    menai_value_xrelease(vs, (MenaiValue *)self->name);
    int n = self->nfields;
    for (int i = 0; i < n; i++) {
        menai_value_xrelease(vs, (MenaiValue *)self->fields[i].name);
    }

    menai_free(vs, self);
}

static inline void
menai_symbol_free(MenaiVMState *vs, MenaiSymbol *self)
{
    menai_value_xrelease(vs, (MenaiValue *)self->name);
    menai_free(vs, self);
}

void
menai_value_free(MenaiVMState *vs, MenaiValue *v)
{
    switch (v->ob_type) {
    case MENAITYPE_BOOLEAN:
        menai_boolean_free(vs, (MenaiBoolean *)v);
        break;

    case MENAITYPE_BYTES:
        menai_bytes_free(vs, (MenaiBytes *)v);
        break;

    case MENAITYPE_COMPLEX:
        menai_complex_free(vs, (MenaiComplex *)v);
        break;

    case MENAITYPE_DICT:
        menai_dict_free(vs, (MenaiDict *)v);
        break;

    case MENAITYPE_FLOAT:
        menai_float_free(vs, (MenaiFloat *)v);
        break;

    case MENAITYPE_FUNCTION:
        menai_function_free(vs, (MenaiFunction *)v);
        break;

    case MENAITYPE_INTEGER:
        menai_integer_free(vs, (MenaiInteger *)v);
        break;

    case MENAITYPE_LIST:
        menai_list_free(vs, (MenaiList *)v);
        break;

    case MENAITYPE_NONE:
        menai_none_free(vs, (MenaiNone *)v);
        break;

    case MENAITYPE_SET:
        menai_set_free(vs, (MenaiSet *)v);
        break;

    case MENAITYPE_STRING:
        menai_string_free(vs, (MenaiString *)v);
        break;

    case MENAITYPE_STRUCT:
        menai_struct_free(vs, (MenaiStruct *)v);
        break;

    case MENAITYPE_STRUCTTYPE:
        menai_structtype_free(vs, (MenaiStructType *)v);
        break;

    case MENAITYPE_SYMBOL:
        menai_symbol_free(vs, (MenaiSymbol *)v);
        break;

    default:
        assert(0);
    }
}
