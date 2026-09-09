/*
 * menai_vm_vector.c — MenaiVector type implementation.
 *
 * MenaiVector stores its element pointers inline in the same allocation as
 * the struct, using a C99 flexible array member.  A single menai_value_alloc
 * call covers both the header and the MenaiValue* array for owning vectors.
 * Slice views allocate only the header (sizeof(MenaiVector)) and point their
 * data pointer into the owner's inline storage, exactly mirroring MenaiBytes.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

/*
 * alloc_menai_vector — allocate an owning MenaiVector with room for n
 * elements.  length is set to n; data is uninitialised.  The caller must
 * retain each element before storing it into inline_data.
 * Returns a new reference, or NULL on allocation failure.
 */
MenaiVector *
alloc_menai_vector(MenaiVMState *vs, ssize_t n)
{
    size_t sz = sizeof(MenaiVector) + (size_t)n * sizeof(MenaiValue *);
    MenaiVector *obj = (MenaiVector *)menai_value_alloc(vs, MENAITYPE_VECTOR, sz);
    if (obj == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)obj);
    obj->length = n;
    obj->owner = NULL;
    obj->data = obj->inline_data;

    return obj;
}

MenaiVector *
alloc_menai_vector_from_args(MenaiVMState *vs, MenaiValue **elems, ssize_t n)
{
    MenaiVector *obj = alloc_menai_vector(vs, n);
    if (!obj) {
        return NULL;
    }

    for (ssize_t i = 0; i < n; i++) {
        obj->inline_data[i] = elems[i];
    }

    return obj;
}

MenaiVector *
alloc_menai_vector_from_slice(MenaiVMState *vs, MenaiVector *v, ssize_t start, ssize_t end)
{
    /*
     * Resolve the owner: if v is itself a view, point at its owner so
     * all views are depth-1 from the root data owner.
     */
    MenaiVector *owner = (v->owner != NULL) ? v->owner : v;

    MenaiVector *view = (MenaiVector *)menai_value_alloc(vs, MENAITYPE_VECTOR, sizeof(MenaiVector));
    if (view == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)view);
    menai_value_retain((MenaiValue *)owner);
    view->owner = owner;
    view->data = v->data + start;
    view->length = end - start;

    return view;
}

MenaiVector *
alloc_menai_vector_from_concat(MenaiVMState *vs, MenaiVector *a, MenaiVector *b)
{
    ssize_t la = a->length;
    ssize_t lb = b->length;
    MenaiVector *obj = alloc_menai_vector(vs, la + lb);
    if (!obj) {
        return NULL;
    }

    if (la > 0) {
        memcpy(obj->inline_data, a->data, (size_t)la * sizeof(MenaiValue *));
    }

    if (lb > 0) {
        memcpy(obj->inline_data + la, b->data, (size_t)lb * sizeof(MenaiValue *));
    }

    return obj;
}

MenaiVector *
alloc_menai_vector_from_set(MenaiVMState *vs, MenaiVector *v, ssize_t index, MenaiValue *val)
{
    ssize_t len = v->length;
    MenaiVector *obj = alloc_menai_vector(vs, len);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, v->data, (size_t)len * sizeof(MenaiValue *));
    }

    obj->inline_data[index] = val;

    return obj;
}

MenaiValue *
menai_vector_ref(MenaiVMState *vs, MenaiVector *v, ssize_t i)
{
    (void)vs;
    return v->data[i];
}

int
menai_vector_equal(MenaiVector *a, MenaiVector *b)
{
    ssize_t la = a->length;
    if (la != b->length) {
        return 0;
    }

    for (ssize_t i = 0; i < la; i++) {
        if (!menai_value_equal(a->data[i], b->data[i])) {
            return 0;
        }
    }

    return 1;
}
