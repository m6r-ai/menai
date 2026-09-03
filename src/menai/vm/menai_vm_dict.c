/*
 * menai_vm_dict.c — MenaiDict type implementation.
 *
 * MenaiDict stores an ordered sequence of entries as an array of pointers to
 * MenaiDictElement objects, plus a pure-C MenaiHashTable for O(1) index
 * lookup.  Each MenaiDictElement packages a key, value, and precomputed hash
 * in a single reference-counted allocation.  Because elements are individually
 * ref-counted, they can be shared between different versions of a dictionary —
 * dict-set and dict-remove only create/replace the element that changes,
 * retaining (sharing) all unchanged elements from the source dictionary.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiDict *
alloc_menai_dict(MenaiVMState *vs, ssize_t cap)
{
    size_t data_size = (size_t)cap * sizeof(MenaiDictElement *);
    MenaiDict *obj = (MenaiDict *)menai_alloc(vs, sizeof(MenaiDict) + data_size);
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_DICT;
    obj->elements = obj->inline_data;
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;
    obj->length = 0;

    return obj;
}

MenaiDictElement *
alloc_menai_dict_element(MenaiVMState *vs, MenaiValue *key, MenaiValue *value, hash_t hash)
{
    MenaiDictElement *elem = (MenaiDictElement *)menai_alloc(vs, sizeof(MenaiDictElement));
    if (!elem) {
        return NULL;
    }

    elem->ob_refcnt = 1;
    elem->ob_type = MENAITYPE_DICT_ELEMENT;
    elem->key = key;
    elem->value = value;
    elem->hash = hash;

    return elem;
}
