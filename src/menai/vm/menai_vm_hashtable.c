/*
 * menai_vm_hashtable.c — pure-C hash table and value operations.
 *
 * MenaiHashTable is an open-addressing table with power-of-2 slot counts and
 * a 2/3 maximum load factor.  Probing uses the same quadratic-ish sequence
 * CPython uses: slot = (5*slot + 1 + perturb) & mask, perturb >>= 5.
 *
 * There is no deletion: a dictionary or set that loses an element rebuilds its
 * table (menai_ht_clear, then one insert per surviving entry).  Growth is not
 * needed while the collection is shared, because a shared update copies into a
 * table sized for the new entry count, but a uniquely-owned dictionary can be
 * appended to in place, and that path grows the slot array with
 * menai_ht_ensure.
 */
#define _POSIX_C_SOURCE 200809L
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

hash_t
menai_value_hash(MenaiValue *val)
{
    switch (menai_tag(val)) {
    case MENAI_TAG_POINTER:
        break;

    case MENAI_TAG_FIXNUM:
        return menai_integer_hash(val);

    case MENAI_TAG_FLOAT:
        return menai_float_hash(val);

    case MENAI_TAG_NONE:
        return menai_none_hash();

    case MENAI_TAG_TRUE:
    case MENAI_TAG_FALSE:
        return menai_boolean_hash(val);

    default:
        assert(0);
    }

    MenaiPoolHeader *ph = menai_get_pool_header(val);
    MenaiType t = ph->ob_type;

    switch (t) {
    case MENAITYPE_BYTES:
        return menai_bytes_hash((MenaiBytes *)val);

    case MENAITYPE_COMPLEX:
        return menai_complex_hash((MenaiComplex *)val);

    case MENAITYPE_ENUM:
        return menai_enum_hash((MenaiEnum *)val);

    case MENAITYPE_ENUMTYPE:
        return menai_enumtype_hash((MenaiEnumType *)val);

    case MENAITYPE_FLOAT:
        return menai_float_hash(val);

    case MENAITYPE_INTEGER:
        return menai_integer_hash(val);

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

    /*
     * Different words.  Each immediate value has exactly one encoding, so if
     * either side is an immediate the two are different values and nothing below
     * can make them equal.  Identity is checked first because it is the common
     * answer for a key that is already in the table.
     */
    if (((uintptr_t)a & MENAI_TAG_MASK) != MENAI_TAG_POINTER ||
            ((uintptr_t)b & MENAI_TAG_MASK) != MENAI_TAG_POINTER) {
        return 0;
    }

    MenaiPoolHeader *pha = menai_get_pool_header(a);
    MenaiType ta = pha->ob_type;
    MenaiPoolHeader *phb = menai_get_pool_header(b);
    MenaiType tb = phb->ob_type;

    if (ta != tb) {
        return 0;
    }

    switch (ta) {
    case MENAITYPE_BYTES:
        return menai_bytes_equal((MenaiBytes *)a, (MenaiBytes *)b);

    case MENAITYPE_COMPLEX:
        return menai_complex_equal((MenaiComplex *)a, (MenaiComplex *)b);

    case MENAITYPE_DICT:
        return menai_dict_equal((MenaiDict *)a, (MenaiDict *)b);

    case MENAITYPE_FLOAT:
        return menai_float_equal(a, b);

    case MENAITYPE_INTEGER:
        return menai_integer_equal(a, b);

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

/*
 * menai_ht_slots_for — the slot count needed to hold n entries at the maximum
 * load factor: the smallest power of two at least 4, or 0 for an empty table.
 */
static inline ssize_t
menai_ht_slots_for(ssize_t n)
{
    if (n <= 0) {
        return 0;
    }

    ssize_t min_slots = (n * MENAI_HT_MAX_LOAD_DEN + MENAI_HT_MAX_LOAD_NUM - 1) / MENAI_HT_MAX_LOAD_NUM;
    ssize_t sc = 4;
    while (sc < min_slots) {
        sc <<= 1;
    }

    return sc;
}

int
menai_ht_init(MenaiVMState *vs, MenaiHashTable *ht, ssize_t n)
{
    ssize_t sc = menai_ht_slots_for(n);
    if (sc == 0) {
        ht->slots = NULL;
        ht->slot_count = 0;
        return 0;
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

/*
 * menai_ht_ensure — grow the slot array if it cannot hold n entries.
 *
 * The existing entries are re-probed into a larger array and the old one is
 * returned to the pool.  Nothing is allocated when the table is already large
 * enough, so this is cheap to call on every in-place append.
 *
 * On allocation failure the table is left exactly as it was, so the caller's
 * value keeps its invariants and can still be released.
 */
int
menai_ht_ensure(MenaiVMState *vs, MenaiHashTable *ht, ssize_t n)
{
    ssize_t sc = menai_ht_slots_for(n);
    if (sc <= ht->slot_count) {
        return MENAI_OK;
    }

    MenaiHashSlot *new_slots = (MenaiHashSlot *)menai_pool_alloc(vs, (size_t)sc * sizeof(MenaiHashSlot));
    if (!new_slots) {
        return MENAI_ERR_NOMEM;
    }

    memset(new_slots, 0, (size_t)sc * sizeof(MenaiHashSlot));

    MenaiHashSlot *old_slots = ht->slots;
    ssize_t old_count = ht->slot_count;

    ht->slots = new_slots;
    ht->slot_count = sc;

    for (ssize_t i = 0; i < old_count; i++) {
        if (old_slots[i].key != NULL) {
            menai_ht_insert(ht, old_slots[i].key, old_slots[i].hash, old_slots[i].index);
        }
    }

    if (old_slots != NULL) {
        menai_pool_free(vs, old_slots);
    }

    return MENAI_OK;
}

/*
 * menai_ht_clear — empty the table without releasing the slot array.
 *
 * Used when a dictionary or set removes an element: entries after the removed
 * one shift down, so every stored index changes and the table must be rebuilt.
 * Clearing and re-inserting reuses the existing allocation instead of sizing a
 * new one, and avoids implementing deletion in an open-addressing table whose
 * probe sequence is not linear.
 */
void
menai_ht_clear(MenaiHashTable *ht)
{
    if (ht->slots != NULL && ht->slot_count > 0) {
        memset(ht->slots, 0, (size_t)ht->slot_count * sizeof(MenaiHashSlot));
    }
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
