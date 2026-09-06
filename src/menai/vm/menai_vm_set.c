/*
 * menai_vm_set.c — MenaiSet type implementation.
 *
 * MenaiSet stores an ordered sequence of elements as an array of pointers to
 * MenaiSetElement objects, plus a pure-C MenaiHashTable for O(1) membership
 * testing.  Each MenaiSetElement packages a value and its precomputed hash in
 * a single reference-counted allocation.  Because elements are individually
 * ref-counted, they can be shared between different versions of a set —
 * set-add and set-remove only create/replace the element that changes,
 * retaining (sharing) all unchanged elements from the source set.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiSet *
alloc_menai_set(MenaiVMState *vs, ssize_t cap)
{
    size_t sz = sizeof(MenaiSet) + (size_t)cap * sizeof(MenaiSetElement *);
    MenaiSet *obj = (MenaiSet *)menai_value_alloc(vs, MENAITYPE_SET, sz);
    if (!obj) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)obj);
    obj->elements = obj->inline_data;
    obj->length = 0;
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;

    return obj;
}

MenaiSetElement *
alloc_menai_set_element(MenaiVMState *vs, MenaiValue *value, hash_t hash)
{
    MenaiSetElement *elem = (MenaiSetElement *)menai_value_alloc(vs, MENAITYPE_SET_ELEMENT, sizeof(MenaiSetElement));
    if (!elem) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)elem);
    elem->value = value;
    elem->hash = hash;

    return elem;
}
