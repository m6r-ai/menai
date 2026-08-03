/*
 * menai_vm_value.c
 */
#include <stdlib.h>
#include <stdint.h>

#include "menai_vm_c.h"

static inline void
menai_boolean_free(MenaiBoolean *self)
{
    /*
     * Singletons are never freed.
     */
    (void)self;
}

static inline void
menai_bytes_free(MenaiBytes *self)
{
    if (self->owner != NULL) {
        /* View — release the backing owner; do not touch the data array. */
        menai_value_release((MenaiValue *)self->owner);
        menai_free(self);
        return;
    }

    /* Owner — the data array is inline, freed with the struct. */
    menai_free(self);
}

static inline void
menai_complex_free(MenaiComplex *self)
{
    menai_free(self);
}

static inline void
menai_dict_free(MenaiDict *self)
{
    ssize_t n = self->length;

    if (self->keys) {
        for (ssize_t i = 0; i < n; i++) {
            menai_value_release(self->keys[i]);
        }

        free(self->keys);
    }

    if (self->values) {
        for (ssize_t i = 0; i < n; i++) {
            menai_value_release(self->values[i]);
        }

        free(self->values);
    }

    free(self->hashes);
    menai_ht_free(&self->ht);
    menai_free(self);
}

static inline void
menai_float_free(MenaiFloat *self)
{
    menai_free(self);
}

static inline void
menai_function_free(MenaiFunction *self)
{
    menai_code_object_release(self->bytecode);
    ssize_t ncap = self->ncap;
    for (ssize_t i = 0; i < ncap; i++) {
        menai_value_xrelease(self->captures[i]);
    }

    menai_free(self);
}

static inline void
menai_integer_free(MenaiInteger *self)
{
    if (!self->is_big) {
        long v = self->small;
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

    menai_free(self);
}

static inline void
menai_list_free(MenaiList *self)
{
    if (self->owner != NULL) {
        /* View — release the backing list; do not touch the element array. */
        menai_value_release((MenaiValue *)self->owner);
        menai_free(self);
        return;
    }

    /* Owner — release all elements then free the combined block. */
    ssize_t n = self->length;
    MenaiValue **arr = self->elements;
    for (ssize_t i = 0; i < n; i++) {
        menai_value_release(*arr++);
    }

    menai_free(self);
}

static inline void
menai_none_free(MenaiNone *self)
{
    /*
     * The singleton is never freed — its refcount should never reach zero.
     */
    (void)self;
}

static inline void
menai_set_free(MenaiSet *self)
{
    ssize_t n = self->length;
    for (ssize_t i = 0; i < n; i++) {
        menai_value_release(self->elements[i]);
    }

    menai_ht_free(&self->ht);
    menai_free(self);
}

static inline void
menai_string_free(MenaiString *self)
{
    menai_free(self);
}

static inline void
menai_struct_free(MenaiStruct *self)
{
    menai_value_xrelease((MenaiValue *)self->struct_type);
    int n = self->nfields;
    for (int i = 0; i < n; i++) {
        menai_value_xrelease(self->items[i]);
    }

    menai_free(self);
}

static inline void
menai_structtype_free(MenaiStructType *self)
{
    menai_ht_free(&self->field_ht);
    menai_value_xrelease((MenaiValue *)self->name);
    int n = self->nfields;
    for (int i = 0; i < n; i++) {
        menai_value_xrelease((MenaiValue *)self->fields[i].name);
    }

    menai_free(self);
}

static inline void
menai_symbol_free(MenaiSymbol *self)
{
    menai_value_xrelease((MenaiValue *)self->name);
    menai_free(self);
}

void
menai_value_free(MenaiValue *v)
{
    switch (v->ob_type) {
    case MENAITYPE_BOOLEAN:
        menai_boolean_free((MenaiBoolean *)v);
        break;

    case MENAITYPE_BYTES:
        menai_bytes_free((MenaiBytes *)v);
        break;

    case MENAITYPE_COMPLEX:
        menai_complex_free((MenaiComplex *)v);
        break;

    case MENAITYPE_DICT:
        menai_dict_free((MenaiDict *)v);
        break;

    case MENAITYPE_FLOAT:
        menai_float_free((MenaiFloat *)v);
        break;

    case MENAITYPE_FUNCTION:
        menai_function_free((MenaiFunction *)v);
        break;

    case MENAITYPE_INTEGER:
        menai_integer_free((MenaiInteger *)v);
        break;

    case MENAITYPE_LIST:
        menai_list_free((MenaiList *)v);
        break;

    case MENAITYPE_NONE:
        menai_none_free((MenaiNone *)v);
        break;

    case MENAITYPE_SET:
        menai_set_free((MenaiSet *)v);
        break;

    case MENAITYPE_STRING:
        menai_string_free((MenaiString *)v);
        break;

    case MENAITYPE_STRUCT:
        menai_struct_free((MenaiStruct *)v);
        break;

    case MENAITYPE_STRUCTTYPE:
        menai_structtype_free((MenaiStructType *)v);
        break;

    case MENAITYPE_SYMBOL:
        menai_symbol_free((MenaiSymbol *)v);
        break;

    default:
        assert(0);
    }
}