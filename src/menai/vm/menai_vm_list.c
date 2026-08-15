/*
 * menai_vm_list.c — MenaiList type implementation.
 *
 * MenaiList stores its elements inline in the same allocation as the struct,
 * using a C99 flexible array member.  A single menai_alloc call covers both
 * the header and the element array for owning lists.  Slice views allocate
 * only the header (sizeof(MenaiList)) and point their elements pointer into
 * the owner's inline storage.
 *
 * The primary constructor is menai_list_alloc(n), which allocates
 * sizeof(MenaiList) + n * sizeof(MenaiValue *) bytes and returns a list with
 * uninitialised elements ready for the caller to fill and retain.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiList *
alloc_menai_list(MenaiVMState *vs, ssize_t n)
{
    MenaiList *obj = (MenaiList *)menai_alloc(vs, sizeof(MenaiList) + (size_t)n * sizeof(MenaiValue *));
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_LIST;
    obj->elements = obj->inline_elements;
    obj->length = n;
    obj->owner = NULL;

    return obj;
}
