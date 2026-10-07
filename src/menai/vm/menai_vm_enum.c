/*
 * menai_vm_enum.c — MenaiEnum type implementations.
 *
 * MenaiEnum: a bare-tag enum value.  It carries its enum type and the variant's
 * dense index (0..nvariants-1).  There is no payload: a bare-tag variant is fully
 * described by the pair (enum type, variant index).
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiEnum *
alloc_menai_enum(MenaiVMState *vs, MenaiEnumType *enum_type, int variant_index)
{
    MenaiEnum *self = (MenaiEnum *)menai_value_alloc(vs, MENAITYPE_ENUM, sizeof(MenaiEnum));
    if (!self) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    self->variant_index = variant_index;
    menai_value_retain((MenaiValue *)enum_type);
    self->enum_type = enum_type;

    return self;
}
