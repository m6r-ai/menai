/*
 * menai_vm_boolean.c — MenaiBoolean type implementation.
 *
 * MenaiBoolean stores a C int (0 or 1).  Two singletons (_Menai_TRUE and
 * _Menai_FALSE) are created at init time.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

static MenaiBoolean _true_storage;
static MenaiBoolean _false_storage;

MenaiBoolean *
menai_boolean_true(void)
{
    return &_true_storage;
}

MenaiBoolean *
menai_boolean_false(void)
{
    return &_false_storage;
}

void
menai_init_boolean(void)
{
    _true_storage.ob_refcnt = 1;
    _true_storage.ob_type = MENAITYPE_BOOLEAN;
    _true_storage.value = 1;

    _false_storage.ob_refcnt = 1;
    _false_storage.ob_type = MENAITYPE_BOOLEAN;
    _false_storage.value = 0;
}
