/*
 * menai_vm_list.c — MenaiList type implementation.
 *
 * MenaiList is a cons-cell based linked list.  Each cell is a fixed-size
 * allocation holding one element (head) and a pointer to the rest (tail).
 * The empty list is a shared sentinel stored inline in MenaiVMState.
 *
 * alloc_menai_list allocates a single cons cell with head and tail set to
 * NULL and length 0.  The caller fills in head, tail, and length.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiList *
alloc_menai_list(MenaiVMState *vs)
{
    MenaiList *obj = (MenaiList *)menai_alloc(vs, sizeof(MenaiList));
    if (!obj) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_LIST;
    obj->head = NULL;
    obj->tail = NULL;
    obj->length = 0;

    return obj;
}
