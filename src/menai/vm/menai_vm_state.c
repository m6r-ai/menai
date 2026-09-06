/*
 * menai_vm_state.c — MenaiVMState lifecycle management.
 *
 * menai_vm_state_alloc creates and initialises a fresh MenaiVMState: the
 * none/boolean/empty-list singletons are stored inline and initialised
 * directly, while the integer cache and empty dict/set are allocated from
 * this instance's pool so they participate in the same allocator lifecycle
 * as every other value.
 *
 * menai_vm_state_free tears down the globals, drains the pool free-lists, and
 * frees the struct itself.
 */
#include <stdlib.h>

#include "menai_vm_c.h"

MenaiVMState *
menai_vm_state_alloc(void)
{
    MenaiVMState *vs = (MenaiVMState *)calloc(1, sizeof(MenaiVMState));
    if (vs == NULL) {
        return NULL;
    }

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_init(&vs->_leak_set);
#endif

    vs->none = menai_value_alloc(vs, MENAITYPE_NONE, sizeof(MenaiNone));
    if (!vs->none) {
        menai_vm_state_free(vs);
        return NULL;
    }

    MENAI_SET_MAGIC(vs->none);

    vs->boolean_true = menai_value_alloc(vs, MENAITYPE_BOOLEAN, sizeof(MenaiBoolean));
    if (!vs->boolean_true) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->boolean_true->value = 1;
    MENAI_SET_MAGIC(vs->boolean_true);

    vs->boolean_false = menai_value_alloc(vs, MENAITYPE_BOOLEAN, sizeof(MenaiBoolean));
    if (!vs->boolean_false) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->boolean_false->value = 0;
    MENAI_SET_MAGIC(vs->boolean_false);

    for (long v = MENAI_INT_CACHE_MIN; v <= MENAI_INT_CACHE_MAX; v++) {
        MenaiInteger *obj = (MenaiInteger *)menai_value_alloc(vs, MENAITYPE_INTEGER, sizeof(MenaiInteger));
        if (obj == NULL) {
            menai_vm_state_free(vs);
            return NULL;
        }

        MENAI_SET_MAGIC((MenaiValue *)obj);
        obj->is_big = 0;
        obj->fixed = v;
        menai_bigint_init(&obj->big);

        vs->integer_cache[v - MENAI_INT_CACHE_MIN] = obj;
    }

    vs->empty_list = alloc_menai_list(vs);
    if (!vs->empty_list) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->empty_dict = alloc_menai_dict(vs, 0);
    if (!vs->empty_dict) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->empty_set = alloc_menai_set(vs, 0);
    if (!vs->empty_set) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->_gc_threshold = GC_THRESHOLD;

    return vs;
}

void
menai_vm_state_free(MenaiVMState *vs)
{
    /*
     * Final collection — reclaim any remaining cyclic closures before
     * globals are freed so they are not misreported as leaks.  With the
     * execute-time collection working, this should find nothing.
     */
    menai_closure_gc_collect(vs, NULL);
    menai_closure_registry_free(vs);

    if (vs->_globals_valid) {
        globals_free(vs, &vs->_globals);
    }

    if (vs->empty_set) {
        menai_value_free(vs, (MenaiValue *)vs->empty_set);
    }

    if (vs->empty_dict) {
        menai_value_free(vs, (MenaiValue *)vs->empty_dict);
    }

    if (vs->empty_list) {
        menai_value_free(vs, (MenaiValue *)vs->empty_list);
    }

    for (long v = MENAI_INT_CACHE_MIN; v <= MENAI_INT_CACHE_MAX; v++) {
        MenaiInteger *cached_int = vs->integer_cache[v - MENAI_INT_CACHE_MIN];
        if (cached_int) {
            menai_value_free(vs, (MenaiValue *)cached_int);
        }
    }

    if (vs->boolean_false) {
        menai_value_free(vs, (MenaiValue *)vs->boolean_false);
    }

    if (vs->boolean_true) {
        menai_value_free(vs, (MenaiValue *)vs->boolean_true);
    }

    if (vs->none) {
        menai_value_free(vs, (MenaiValue *)vs->none);
    }

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_report(vs);
    menai_leak_set_final(&vs->_leak_set);
#endif

    BucketEntry *pool_bucket = vs->pool;
    for (int bucket = 0; bucket < MENAI_POOL_NUM_BUCKETS; bucket++, pool_bucket++) {
        void *ptr = pool_bucket->head;
        while (ptr) {
            void *next = *(void **)ptr;
            /* ptr is the user-visible area; the raw malloc block is behind the header */
            free((char *)ptr - sizeof(MenaiPoolHeader));
            ptr = next;
        }
    }

    free(vs);
}
