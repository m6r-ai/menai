/*
 * menai_vm_set.c — MenaiSet type implementation.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiSet *
alloc_menai_set(ssize_t cap)
{
    size_t sz = sizeof(MenaiSet) + (size_t)cap * (sizeof(MenaiValue *) + sizeof(hash_t));
    MenaiSet *obj = (MenaiSet *)menai_alloc(sz);
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_SET;
    obj->elements = (MenaiValue **)obj->inline_data;
    obj->hashes = (hash_t *)(obj->inline_data + cap);
    obj->length = 0;
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;
    obj->ht.used = 0;

    return obj;
}

MenaiSet *
alloc_empty_menai_set(void)
{
    MenaiSet *obj = (MenaiSet *)menai_alloc(sizeof(MenaiSet));
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_SET;
    obj->elements = NULL;
    obj->hashes = NULL;
    obj->ht.slots = NULL;
    obj->ht.slot_count = 0;
    obj->ht.used = 0;
    obj->length = 0;

    return obj;
}
