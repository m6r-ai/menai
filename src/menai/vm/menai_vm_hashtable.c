/*
 * menai_vm_hashtable.c — pure-C hash table and value operations.
 *
 * MenaiHashTable is an open-addressing table with power-of-2 slot counts and
 * a 2/3 maximum load factor.  Probing uses the same quadratic-ish sequence
 * CPython uses: slot = (5*slot + 1 + perturb) & mask, perturb >>= 5.
 * Tables are built once and never mutated (Menai collections are immutable),
 * so there is no deletion or rehashing logic.
 */
#define _POSIX_C_SOURCE 200809L
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

hash_t
menai_value_hash(MenaiValue *val)
{
    MenaiPoolHeader *ph = menai_get_pool_header(val);
    MenaiType t = ph->ob_type;

    switch (t) {
    case MENAITYPE_BOOLEAN:
        return menai_boolean_hash((MenaiBoolean *)val);

    case MENAITYPE_BYTES:
        return menai_bytes_hash((MenaiBytes *)val);

    case MENAITYPE_COMPLEX:
        return menai_complex_hash((MenaiComplex *)val);

    case MENAITYPE_ENUM:
        return menai_enum_hash((MenaiEnum *)val);

    case MENAITYPE_ENUMTYPE:
        return menai_enumtype_hash((MenaiEnumType *)val);

    case MENAITYPE_FLOAT:
        return menai_float_hash((MenaiFloat *)val);

    case MENAITYPE_INTEGER:
        return menai_integer_hash((MenaiInteger *)val);

    case MENAITYPE_NONE:
        return menai_none_hash();

    case MENAITYPE_STRING:
        return menai_string_hash((MenaiString *)val);

    case MENAITYPE_STRUCT:
        return menai_struct_hash((MenaiStruct *)val);

    case MENAITYPE_STRUCTTYPE:
        return menai_structtype_hash((MenaiStructType *)val);

    case MENAITYPE_SYMBOL:
        return menai_symbol_hash((MenaiSymbol *)val);
    }

    return -1;
}

int
menai_value_equal(MenaiValue *a, MenaiValue *b)
{
    if (a == b) {
        return 1;
    }

    MenaiPoolHeader *pha = menai_get_pool_header(a);
    MenaiType ta = pha->ob_type;
    MenaiPoolHeader *phb = menai_get_pool_header(b);
    MenaiType tb = phb->ob_type;

    if (ta != tb) {
        return 0;
    }

    switch (ta) {
    case MENAITYPE_BOOLEAN:
        return menai_boolean_equal((MenaiBoolean *)a, (MenaiBoolean *)b);

    case MENAITYPE_BYTES:
        return menai_bytes_equal((MenaiBytes *)a, (MenaiBytes *)b);

    case MENAITYPE_COMPLEX:
        return menai_complex_equal((MenaiComplex *)a, (MenaiComplex *)b);

    case MENAITYPE_DICT:
        return menai_dict_equal((MenaiDict *)a, (MenaiDict *)b);

    case MENAITYPE_FLOAT:
        return menai_float_equal((MenaiFloat *)a, (MenaiFloat *)b);

    case MENAITYPE_INTEGER:
        return menai_integer_equal((MenaiInteger *)a, (MenaiInteger *)b);

    case MENAITYPE_LIST:
        return menai_list_equal((MenaiList *)a, (MenaiList *)b);

    case MENAITYPE_NONE:
        return 1;

    case MENAITYPE_SET:
        return menai_set_equal((MenaiSet *)a, (MenaiSet *)b);

    case MENAITYPE_STRING:
        return menai_string_equal((MenaiString *)a, (MenaiString *)b);

    case MENAITYPE_STRUCT:
        return menai_struct_equal((MenaiStruct *)a, (MenaiStruct *)b);

    case MENAITYPE_STRUCTTYPE:
        return menai_structtype_equal((MenaiStructType *)a, (MenaiStructType *)b);

    case MENAITYPE_ENUM:
        return menai_enum_equal((MenaiEnum *)a, (MenaiEnum *)b);

    case MENAITYPE_ENUMTYPE:
        return menai_enumtype_equal((MenaiEnumType *)a, (MenaiEnumType *)b);

    case MENAITYPE_SYMBOL:
        return menai_symbol_equal((MenaiSymbol *)a, (MenaiSymbol *)b);

    case MENAITYPE_VECTOR:
        return menai_vector_equal((MenaiVector *)a, (MenaiVector *)b);
    }

    return 0;
}

int
menai_ht_init(MenaiVMState *vs, MenaiHashTable *ht, ssize_t n)
{
    if (n == 0) {
        ht->slots = NULL;
        ht->slot_count = 0;
        return 0;
    }

    ssize_t min_slots = (n * MENAI_HT_MAX_LOAD_DEN + MENAI_HT_MAX_LOAD_NUM - 1) / MENAI_HT_MAX_LOAD_NUM;
    ssize_t sc = 4;
    while (sc < min_slots) {
        sc <<= 1;
    }

    ht->slots = (MenaiHashSlot *)menai_pool_alloc(vs, (size_t)sc * sizeof(MenaiHashSlot));
    if (!ht->slots) {
        /*
         * Leave the table fully defined on failure.  A caller that releases
         * a partially-built value will run its finalizer, which calls
         * menai_ht_final; that must see a consistent empty table rather than
         * an uninitialised slot_count.
         */
        ht->slot_count = 0;
        return MENAI_ERR_NOMEM;
    }

    memset(ht->slots, 0, (size_t)sc * sizeof(MenaiHashSlot));
    ht->slot_count = sc;
    return 0;
}

ssize_t
menai_ht_lookup(const MenaiHashTable *ht, MenaiValue *key, hash_t hash)
{
    if (ht->slot_count == 0) {
        return -1;
    }

    ssize_t mask = ht->slot_count - 1;
    uhash_t perturb = (uhash_t)hash;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);

    for (;;) {
        MenaiHashSlot *s = &ht->slots[slot];
        if (s->key == NULL) {
            return -1;
        }

        if (s->hash == hash && menai_value_equal(s->key, key)) {
            return s->index;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}

void
menai_ht_insert(MenaiHashTable *ht, MenaiValue *key, hash_t hash, ssize_t index)
{
    ssize_t mask = ht->slot_count - 1;
    uhash_t perturb = (uhash_t)hash;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);

    for (;;) {
        MenaiHashSlot *s = &ht->slots[slot];
        if (s->key == NULL) {
            s->key = key;
            s->hash = hash;
            s->index = index;
            return;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}

void
menai_ht_replace_key(MenaiHashTable *ht, hash_t hash, ssize_t index, MenaiValue *new_key)
{
    /*
     * Re-point the slot for (hash, index) at a new key object.  Used when a
     * dict or set replaces the element at `index` with a new element whose key
     * is equal to the old one: the slot's key is a borrowed reference to the
     * old element's key, which is about to be released, so it must be updated
     * to the replacement element's key or it would dangle.
     *
     * The probe sequence is the same one menai_ht_insert used, so the slot is
     * found deterministically.  The slot is guaranteed to exist: the caller
     * only calls this for an index that was previously inserted.
     */
    ssize_t mask = ht->slot_count - 1;
    uhash_t perturb = (uhash_t)hash;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);

    for (;;) {
        MenaiHashSlot *s = &ht->slots[slot];
        if (s->key != NULL && s->hash == hash && s->index == index) {
            s->key = new_key;
            return;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}
