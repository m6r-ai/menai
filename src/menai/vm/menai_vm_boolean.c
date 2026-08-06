/*
 * menai_vm_boolean.c — MenaiBoolean type implementation.
 *
 * MenaiBoolean stores a C int (0 or 1).  Two singletons live inline
 * inside MenaiVMState.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiBoolean *
menai_boolean_true(MenaiVMState *vs)
{
    return &vs->true_storage;
}

MenaiBoolean *
menai_boolean_false(MenaiVMState *vs)
{
    return &vs->false_storage;
}
