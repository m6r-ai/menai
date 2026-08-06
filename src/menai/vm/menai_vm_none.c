/*
 * menai_vm_none.c — MenaiNone type implementation.
 *
 * MenaiNone is a singleton with no payload.  A single instance lives inline
 * inside MenaiVMState and is returned by menai_none().
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiNone *
menai_none(MenaiVMState *vs)
{
    return &vs->none_storage;
}
