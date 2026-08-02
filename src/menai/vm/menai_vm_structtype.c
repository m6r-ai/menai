/*
 * menai_vm_struct.c — MenaiStructType type implementations.
 *
 * MenaiStructType: field names are stored in an inline C array of
 * (MenaiString name, index) pairs.  A MenaiHashTable built at construction
 * time provides O(1) name-to-index lookup; its slots hold borrowed references
 * into fields[].  All string fields are native MenaiString * values
 * managed with menai_retain/menai_release.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

/*
 * alloc_menai_structtype — native constructor for MenaiStructType.
 * name must be a MenaiString * (borrowed).  tag is a C int.
 * field_names must be an array of MenaiString * values (borrowed).
 * Returns a new reference, or NULL on error.
 */
MenaiStructType *
alloc_menai_structtype(MenaiString *name, int tag, MenaiString **field_names, ssize_t nfields)
{
    size_t sz = sizeof(MenaiStructType) + (size_t)nfields * sizeof(MenaiFieldEntry);
    MenaiStructType *self = (MenaiStructType *)menai_alloc(sz);
    if (!self) {
        return NULL;
    }

    self->ob_refcnt = 1;
    self->ob_type = MENAITYPE_STRUCTTYPE;
    menai_retain((MenaiValue *)name);
    self->field_ht.slots = NULL;
    self->field_ht.slot_count = 0;
    self->field_ht.used = 0;
    self->name = name;
    self->tag = tag;
    self->nfields = (int)nfields;

    for (ssize_t i = 0; i < nfields; i++) {
        menai_retain((MenaiValue *)field_names[i]);
        self->fields[i].name = field_names[i];
        self->fields[i].index = (int)i;
    }

    if (menai_ht_init(&self->field_ht, nfields) < 0) {
        menai_structtype_free(self);
        return NULL;
    }

    for (ssize_t i = 0; i < nfields; i++) {
        hash_t h = menai_string_hash(self->fields[i].name);
        menai_ht_insert(&self->field_ht, (MenaiValue *)self->fields[i].name, h, i);
    }

    return self;
}
