/*
 * menai_vm_dict.c — MenaiDict type implementation.
 *
 * MenaiDict stores an ordered sequence of key-value entries as three parallel
 * C arrays (keys, values, hashes) laid out inline in a single allocation,
 * plus a pure-C MenaiHashTable for O(1) index lookup.  Hash values are
 * computed once at construction time via menai_value_hash() and stored in
 * hashes[], so no Python objects are allocated during dict operations.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiDict *
alloc_menai_dict(MenaiVMState *vs, ssize_t cap)
{
    size_t data_size = (size_t)cap * (2 * sizeof(MenaiValue *) + sizeof(hash_t));
    MenaiDict *obj = (MenaiDict *)menai_alloc(vs, sizeof(MenaiDict) + data_size);
    if (!obj) {
        return NULL;
    }

    char *data = (char *)obj->inline_data;

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_DICT;
    obj->keys = (MenaiValue **)data;
    obj->values = (MenaiValue **)(data + (size_t)cap * sizeof(MenaiValue *));
    obj->hashes = (hash_t *)(data + (size_t)cap * 2 * sizeof(MenaiValue *));
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;
    obj->length = 0;

    return obj;
}
