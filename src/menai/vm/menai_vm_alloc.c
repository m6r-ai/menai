/*
 * menai_vm_alloc.c — power-of-2 pool allocator for the Menai VM.
 *
 * Sizes 32–4096 bytes are handled by 8 buckets (one per power of 2: 32, 64,
 * 128, 256, 512, 1024, 2048, 4096).  Each bucket is a singly-linked free-list
 * threaded through the first sizeof(void *) bytes of the free block.  A
 * per-bucket depth cap of 256 entries prevents unbounded memory retention.
 *
 * menai_alloc writes the pool block into ob_alloc_bucket in the returned header
 * (0 for out-of-pool allocations).  menai_free reads ob_alloc_bucket to route the
 * block back to the correct bucket or to free().
 *
 * The free lists live in MenaiVMState so that each VM instance has its own
 * pool; no state is shared across instances.
 */
#include <stdlib.h>
#include <stddef.h>
#include <assert.h>
#ifdef MENAI_DEBUG_LEAKS
#include <stdio.h>
#include <string.h>
#endif

#include "menai_vm_c.h"

/*
 * Leak detector implementation (only compiled when MENAI_DEBUG_LEAKS is
 * defined).
 *
 * The leak set is an open-addressing hash set of void * pointers.  It uses
 * the same probe sequence as MenaiHashTable (Python dict-style perturbation).
 * The set is per-VM-instance (stored in MenaiVMState._leak_set) so there is
 * no global state.
 *
 * menai_alloc adds every block it returns; menai_free removes every block
 * it receives.  At VM teardown, menai_leak_set_report walks the set and
 * prints any pointer that is still tracked (excluding known singletons).
 */
#ifdef MENAI_DEBUG_LEAKS

#define MENAI_LEAK_SET_INITIAL_CAP 256
#define MENAI_LEAK_SET_MAX_LOAD_NUM 2
#define MENAI_LEAK_SET_MAX_LOAD_DEN 3

static ssize_t
_leak_set_probe(void **slots, ssize_t mask, void *key)
{
    uhash_t perturb = (uhash_t)(uintptr_t)key;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);
    for (;;) {
        if (slots[slot] == NULL || slots[slot] == key) {
            return slot;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}

static int
_leak_set_grow(MenaiLeakSet *ls)
{
    ssize_t new_cap = ls->slot_count * 2;
    if (new_cap < MENAI_LEAK_SET_INITIAL_CAP) {
        new_cap = MENAI_LEAK_SET_INITIAL_CAP;
    }

    void **new_slots = (void **)calloc((size_t)new_cap, sizeof(void *));
    if (!new_slots) {
        return -1;
    }

    ssize_t new_mask = new_cap - 1;
    for (ssize_t i = 0; i < ls->slot_count; i++) {
        void *entry = ls->slots[i];
        if (entry != NULL) {
            ssize_t slot = _leak_set_probe(new_slots, new_mask, entry);
            new_slots[slot] = entry;
        }
    }

    free(ls->slots);
    ls->slots = new_slots;
    ls->slot_count = new_cap;
    return 0;
}

void
menai_leak_set_init(MenaiLeakSet *ls)
{
    ls->slots = NULL;
    ls->slot_count = 0;
    ls->count = 0;
}

void
menai_leak_set_final(MenaiLeakSet *ls)
{
    free(ls->slots);
    ls->slots = NULL;
    ls->slot_count = 0;
    ls->count = 0;
}

void
menai_leak_set_add(MenaiLeakSet *ls, void *ptr)
{
    if (ptr == NULL) {
        return;
    }

    if (ls->slot_count == 0 ||
        ls->count * MENAI_LEAK_SET_MAX_LOAD_DEN >=
        ls->slot_count * MENAI_LEAK_SET_MAX_LOAD_NUM) {
        if (_leak_set_grow(ls) < 0) {
            return;
        }
    }

    ssize_t mask = ls->slot_count - 1;
    ssize_t slot = _leak_set_probe(ls->slots, mask, ptr);
    if (ls->slots[slot] == NULL) {
        ls->slots[slot] = ptr;
        ls->count++;
    }
}

void
menai_leak_set_remove(MenaiLeakSet *ls, void *ptr)
{
    if (ptr == NULL || ls->slot_count == 0) {
        return;
    }

    ssize_t mask = ls->slot_count - 1;
    ssize_t slot = _leak_set_probe(ls->slots, mask, ptr);
    if (ls->slots[slot] != ptr) {
        return;
    }

    /*
     * Remove the entry, then rebuild the entire set from scratch.
     * This is O(n) per removal but avoids the fragile cluster-rehash
     * that can trigger a grow mid-iteration.  Acceptable for a debug
     * tool that is never compiled into production builds.
     */
    ls->slots[slot] = NULL;
    ls->count--;

    void **old_slots = ls->slots;
    ssize_t old_cap = ls->slot_count;
    ls->slots = NULL;
    ls->slot_count = 0;
    ls->count = 0;

    for (ssize_t i = 0; i < old_cap; i++) {
        if (old_slots[i] != NULL) {
            menai_leak_set_add(ls, old_slots[i]);
        }
    }

    free(old_slots);
}

static const char *
_type_name(MenaiType t)
{
    switch (t) {
    case MENAITYPE_NONE:
        return "none";

    case MENAITYPE_BOOLEAN:
        return "boolean";

    case MENAITYPE_FUNCTION:
        return "function";

    case MENAITYPE_SYMBOL:
        return "symbol";

    case MENAITYPE_STRING:
        return "string";

    case MENAITYPE_INTEGER:
        return "integer";

    case MENAITYPE_FLOAT:
        return "float";

    case MENAITYPE_COMPLEX:
        return "complex";

    case MENAITYPE_LIST:
        return "list";

    case MENAITYPE_DICT:
        return "dict";

    case MENAITYPE_SET:
        return "set";

    case MENAITYPE_STRUCT:
        return "struct";

    case MENAITYPE_STRUCTTYPE:
        return "structtype";

    case MENAITYPE_BYTES:
        return "bytes";

    default:
        return "unknown";
    }
}

static int
_is_singleton(MenaiVMState *vs, void *ptr)
{
    for (int i = 0; i < MENAI_INT_CACHE_SIZE; i++) {
        if ((void *)vs->integer_cache[i] == ptr) {
            return 1;
        }
    }

    if ((void *)vs->empty_list == ptr ||
        (void *)vs->empty_dict == ptr ||
        (void *)vs->empty_set == ptr) {
        return 1;
    }

    return 0;
}

void
menai_leak_set_report(MenaiVMState *vs)
{
    MenaiLeakSet *ls = &vs->_leak_set;
    ssize_t leaks = 0;

    for (ssize_t i = 0; i < ls->slot_count; i++) {
        void *entry = ls->slots[i];
        if (entry == NULL) {
            continue;
        }

        if (_is_singleton(vs, entry)) {
            continue;
        }

        MenaiValue *v = (MenaiValue *)entry;
        const char *type_name = _type_name(v->ob_type);

        if (v->ob_type == MENAITYPE_FUNCTION) {
            MenaiFunction *fn = (MenaiFunction *)v;
            const char *fn_name = fn->bytecode->name ? fn->bytecode->name : "<anonymous>";
            fprintf(stderr,
                "MENAI LEAK: type=%s refcnt=%u ptr=%p name=%s ncap=%zd\n",
                type_name, v->ob_refcnt, (void *)v, fn_name, fn->ncap);
        } else {
            fprintf(stderr,
                "MENAI LEAK: type=%s refcnt=%u ptr=%p\n",
                type_name, v->ob_refcnt, (void *)v);
        }

        leaks++;
    }

    if (leaks > 0) {
        fprintf(stderr, "MENAI LEAK: %zd leaked value(s) detected\n", leaks);
    }
}

#endif /* MENAI_DEBUG_LEAKS */

/*
 * _bucket_for — return the bucket index for a given size, and the rounded-up
 * block size that will be allocated from that bucket.
 *
 * size must be in [MENAI_POOL_MIN_SIZE, MENAI_POOL_MAX_SIZE].
 */
static inline int
_bucket_for(size_t size, size_t *block_size_out)
{
#if defined(__GNUC__) || defined(__clang__)
    return size <= MENAI_POOL_MIN_SIZE ? 0 : 31 - __builtin_clz(size - 1) + 1 - MENAI_POOL_LOG_MIN_SIZE;
#else
    size_t block = MENAI_POOL_MIN_SIZE;
    int bucket = 0;
    while (block < size) {
        block <<= 1;
        bucket++;
    }

    *block_size_out = block;
    return bucket;
#endif
}

void *
menai_alloc(MenaiVMState *vs, size_t size)
{
    if (size > MENAI_POOL_MAX_SIZE) {
        void *ptr = malloc(size);
        if (ptr) {
            MENAI_SET_MAGIC((MenaiValue *)ptr);
            ((MenaiValue *)ptr)->ob_alloc_bucket = -1;
#ifdef MENAI_DEBUG_LEAKS
            menai_leak_set_add(&vs->_leak_set, ptr);
#endif
        }

        return ptr;
    }

    size_t block_size;
    int bucket = _bucket_for(size, &block_size);

    void *ptr;
    if (vs->_pool_heads[bucket] != NULL) {
        ptr = vs->_pool_heads[bucket];
        vs->_pool_heads[bucket] = *(void **)ptr;
        vs->_pool_depths[bucket]--;
        assert(((MenaiValue *)ptr)->ob_type == 0);
    } else {
        ptr = malloc((size_t)1 << (bucket + MENAI_POOL_LOG_MIN_SIZE));
        if (!ptr) {
            return NULL;
        }
    }

    ((MenaiValue *)ptr)->ob_alloc_bucket = (int16_t)bucket;
    MENAI_SET_MAGIC((MenaiValue *)ptr);

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_add(&vs->_leak_set, ptr);
#endif

    return ptr;
}

void
menai_free(MenaiVMState *vs, void *ptr)
{
#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_remove(&vs->_leak_set, ptr);
#endif

    int16_t bucket = ((MenaiValue *)ptr)->ob_alloc_bucket;
    if (bucket == -1) {
        /* Out-of-pool allocation — return directly to malloc. */
        free(ptr);
        return;
    }

    if (vs->_pool_depths[bucket] < MENAI_POOL_MAX_DEPTH) {
        assert(((MenaiValue *)ptr)->ob_type != 0);
        ((MenaiValue *)ptr)->ob_type = 0;
        *(void **)ptr = vs->_pool_heads[bucket];
        vs->_pool_heads[bucket] = ptr;
        vs->_pool_depths[bucket]++;
        return;
    }

    free(ptr);
}