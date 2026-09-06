/*
 * menai_vm_symbol.c — MenaiSymbol type implementation.
 *
 * MenaiSymbol stores its name as an owned MenaiString *.  Equality
 * is determined by menai_string_equal() on the name field.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiSymbol *
alloc_menai_symbol(MenaiVMState *vs, MenaiString *name)
{
    MenaiSymbol *self = (MenaiSymbol *)menai_value_alloc(vs, MENAITYPE_SYMBOL, sizeof(MenaiSymbol));
    if (self == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    menai_value_retain((MenaiValue *)name);
    self->name = name;

    return self;
}
