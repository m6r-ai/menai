/*
 * menai_vm_enumtype.c — MenaiEnumType type implementations.
 *
 * MenaiEnumType: variant names are stored in an inline C array of
 * (MenaiString name, index) pairs.  A MenaiHashTable built at construction
 * time provides O(1) name-to-index lookup; its slots hold borrowed references
 * into variants[].  All string fields are native MenaiString * values
 * managed with menai_value_retain/menai_value_release.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

/*
 * alloc_menai_enumtype — native constructor for MenaiEnumType.
 * name must be a MenaiString * (borrowed).  tag is a C int.
 * variant_names must be an array of MenaiString * values (borrowed).
 * Returns a new reference, or NULL on error.
 */
MenaiEnumType *
alloc_menai_enumtype(MenaiVMState *vs, MenaiString *name, int tag, MenaiString **variant_names, ssize_t nvariants)
{
    size_t sz = sizeof(MenaiEnumType) + (size_t)nvariants * sizeof(MenaiFieldEntry);
    MenaiEnumType *self = (MenaiEnumType *)menai_value_alloc(vs, MENAITYPE_ENUMTYPE, sz);
    if (!self) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)self);
    menai_value_retain((MenaiValue *)name);
    self->name = name;
    self->tag = tag;
    self->nvariants = (int)nvariants;

    for (ssize_t i = 0; i < nvariants; i++) {
        menai_value_retain((MenaiValue *)variant_names[i]);
        self->variants[i].name = variant_names[i];
        self->variants[i].index = (int)i;
    }

    /*
     * menai_value_free runs menai_enumtype_final, which releases the name
     * and every variant name and finalises the variant table.  Do not release
     * them here as well: menai_ht_init leaves the table in a defined empty
     * state on failure, so the finalizer is safe.
     */
    if (menai_ht_init(vs, &self->variant_ht, nvariants) < 0) {
        menai_value_free(vs, (MenaiValue *)self);
        return NULL;
    }

    for (ssize_t i = 0; i < nvariants; i++) {
        hash_t h = menai_string_hash(self->variants[i].name);
        menai_ht_insert(&self->variant_ht, (MenaiValue *)self->variants[i].name, h, i);
    }

    return self;
}
