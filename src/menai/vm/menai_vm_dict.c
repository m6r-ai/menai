/*
 * menai_vm_dict.c — MenaiDict type implementation.
 *
 * MenaiDict stores an ordered sequence of key-value entries as three parallel
 * C arrays (keys, values, hashes) plus a pure-C MenaiHashTable for O(1) index
 * lookup.  Hash values are computed once at construction time via
 * menai_value_hash() and stored in hashes[], so no Python objects are
 * allocated during dict operations.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiDict *
alloc_menai_dict(MenaiVMState *vs)
{
    MenaiDict *obj = (MenaiDict *)menai_alloc(vs, sizeof(MenaiDict));
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_DICT;
    obj->keys = NULL;
    obj->values = NULL;
    obj->hashes = NULL;
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;
    obj->ht.used = 0;
    obj->length = 0;

    return obj;
}
