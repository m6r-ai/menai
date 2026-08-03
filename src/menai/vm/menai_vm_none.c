/*
 * menai_vm_none.c — MenaiNone type implementation.
 *
 * MenaiNone is a singleton with no payload.  A single instance (_Menai_NONE)
 * is created at init time and returned by menai_none().
 */
#include <stdlib.h>

#include "menai_vm_c.h"

static MenaiNone _none_storage;

MenaiNone *
menai_none(void)
{
    return &_none_storage;
}

void
menai_init_none(void)
{
    _none_storage.ob_refcnt = 1;
    _none_storage.ob_type = MENAITYPE_NONE;
}
