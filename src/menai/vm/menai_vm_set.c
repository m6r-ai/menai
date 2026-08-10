/*
 * menai_vm_set.c — MenaiSet type implementation.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiSet *
alloc_menai_set(MenaiVMState *vs, ssize_t cap)
{
    size_t sz = sizeof(MenaiSet) + (size_t)cap * (sizeof(MenaiValue *) + sizeof(hash_t));
    MenaiSet *obj = (MenaiSet *)menai_alloc(vs, sz);
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

    return obj;
}
