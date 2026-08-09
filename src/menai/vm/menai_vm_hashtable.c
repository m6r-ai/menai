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

/*
 * menai_hash_double — hash a C double without any Python API calls.
 *
 * Reinterprets the IEEE 754 bit pattern as a uint64_t via memcpy (safe
 * under strict aliasing rules) then applies a finalisation mix so that
 * nearby values produce well-distributed hashes.  NaN is normalised to a
 * fixed bit pattern before mixing so all NaN values hash identically.
 * The result is mapped away from -1 (the CPython "error" sentinel).
 *
 * This is a Menai-internal hash — it does not need to match Python's
 * float hash, because Menai floats and integers are never equal and are
 * never mixed in the same dict or set.
 */
static inline hash_t
menai_hash_double(double v)
{
    uint64_t bits;
    if (v != v) {
        bits = 0x7FF8000000000000ULL;
    } else {
        memcpy(&bits, &v, sizeof(bits));
    }

    /* Finalisation mix from SplitMix64 */
    bits ^= bits >> 30;
    bits *= 0xbf58476d1ce4e5b9ULL;
    bits ^= bits >> 27;
    bits *= 0x94d049bb133111ebULL;
    bits ^= bits >> 31;
    hash_t h = (hash_t)(bits & (uint64_t)PTRDIFF_MAX);
    return h == -1 ? -2 : h;
}

static inline uhash_t
_hash_combine(uhash_t acc, hash_t h)
{
    return acc * 1000003UL ^ (uhash_t)h;
}

static inline hash_t
_hash_finalise(uhash_t acc, ssize_t n)
{
    acc ^= (uhash_t)n;
    return (hash_t)(acc == (uhash_t)-1 ? (uhash_t)-2 : acc);
}

hash_t
menai_value_hash(MenaiValue *val)
{
    MenaiType t = val->ob_type;

    switch (t) {
    case MENAITYPE_BOOLEAN:
        return (hash_t)((MenaiBoolean *)val)->value;

    case MENAITYPE_BYTES:
        return menai_bytes_hash((MenaiBytes *)val);

    case MENAITYPE_COMPLEX: {
        MenaiComplex *c = (MenaiComplex *)val;
        hash_t hr = menai_hash_double(c->real);
        hash_t hi = menai_hash_double(c->imag);
        uhash_t acc = (uhash_t)hr * 1000003UL ^ (uhash_t)hi;
        hash_t h = (hash_t)(acc & (uhash_t)SSIZE_MAX);
        return h == -1 ? -2 : h;
    }

    case MENAITYPE_FLOAT:
        return menai_hash_double(((MenaiFloat *)val)->value);

    case MENAITYPE_INTEGER: {
        MenaiInteger *obj = (MenaiInteger *)val;
        if (!obj->is_big) {
            hash_t h = (hash_t)obj->fixed;
            return h == -1 ? -2 : h;
        }

        return menai_bigint_hash(&obj->big);
    }

    case MENAITYPE_NONE:
        return (hash_t)0x4e6f6e65UL;

    case MENAITYPE_STRING:
        return menai_string_hash((MenaiString *)val);

    case MENAITYPE_STRUCT: {
        MenaiStruct *s = (MenaiStruct *)val;
        int tag = ((MenaiStructType *)s->struct_type)->tag;
        int n = s->nfields;
        uhash_t acc = 0x345678UL ^ (uhash_t)tag;
        for (int i = 0; i < n; i++) {
            hash_t fh = menai_value_hash(s->items[i]);
            if (fh == -1) {
                return -1;
            }

            acc = _hash_combine(acc, fh);
        }

        return _hash_finalise(acc, n);
    }

    case MENAITYPE_STRUCTTYPE:
        return (hash_t)((MenaiStructType *)val)->tag;

    case MENAITYPE_SYMBOL:
        return menai_string_hash(((MenaiSymbol *)val)->name);
    }

    return -1;
}

int
menai_value_equal(MenaiValue *a, MenaiValue *b)
{
    if (a == b) {
        return 1;
    }

    MenaiType ta = a->ob_type;
    MenaiType tb = b->ob_type;

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

    case MENAITYPE_SYMBOL:
        return menai_symbol_equal((MenaiSymbol *)a, (MenaiSymbol *)b);
    }

    return 0;
}

int
menai_ht_init(MenaiHashTable *ht, ssize_t n)
{
    if (n == 0) {
        ht->slots = NULL;
        ht->slot_count = 0;
        ht->used = 0;
        return 0;
    }

    ssize_t min_slots = (n * MENAI_HT_MAX_LOAD_DEN + MENAI_HT_MAX_LOAD_NUM - 1) / MENAI_HT_MAX_LOAD_NUM;
    ssize_t sc = 4;
    while (sc < min_slots) {
        sc <<= 1;
    }

    ht->slots = (MenaiHashSlot *)malloc((size_t)sc * sizeof(MenaiHashSlot));
    if (!ht->slots) {
        return MENAI_ERR_NOMEM;
    }

    memset(ht->slots, 0, (size_t)sc * sizeof(MenaiHashSlot));
    ht->slot_count = sc;
    ht->used = 0;
    return 0;
}

void
menai_ht_final(MenaiHashTable *ht)
{
    free(ht->slots);
    ht->slots = NULL;
    ht->slot_count = 0;
    ht->used = 0;
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
            ht->used++;
            return;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}
