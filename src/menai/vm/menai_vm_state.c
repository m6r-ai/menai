/*
 * menai_vm_state.c — MenaiVMState lifecycle management.
 *
 * menai_vm_state_alloc creates and initialises a fresh MenaiVMState: the
 * none/boolean singletons are stored inline and initialised directly, while
 * the integer cache and empty list/dict/set are allocated from this
 * instance's pool so they participate in the same allocator lifecycle as
 * every other value.
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

    vs->none_storage.ob_refcnt = 1;
    vs->none_storage.ob_type = MENAITYPE_NONE;

    vs->true_storage.ob_refcnt = 1;
    vs->true_storage.ob_type = MENAITYPE_BOOLEAN;
    vs->true_storage.value = 1;

    vs->false_storage.ob_refcnt = 1;
    vs->false_storage.ob_type = MENAITYPE_BOOLEAN;
    vs->false_storage.value = 0;

    for (long v = MENAI_INT_CACHE_MIN; v <= MENAI_INT_CACHE_MAX; v++) {
        MenaiInteger *obj = (MenaiInteger *)menai_alloc(vs, sizeof(MenaiInteger));
        if (obj == NULL) {
            menai_vm_state_free(vs);
            return NULL;
        }

        obj->ob_refcnt = 1;
        obj->ob_type = MENAITYPE_INTEGER;
        obj->is_big = 0;
        obj->fixed = v;
        menai_bigint_init(&obj->big);

        vs->integer_cache[v - MENAI_INT_CACHE_MIN] = obj;
    }

    vs->empty_list = alloc_menai_list(vs, 0);
    if (vs->empty_list == NULL) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->empty_dict = alloc_menai_dict(vs);
    if (vs->empty_dict == NULL) {
        menai_vm_state_free(vs);
        return NULL;
    }

    vs->empty_set = alloc_menai_set(vs, 0);
    if (vs->empty_set == NULL) {
        menai_vm_state_free(vs);
        return NULL;
    }

    return vs;
}

void
menai_vm_state_free(MenaiVMState *vs)
{
    if (vs->_globals_valid) {
        globals_free(vs, &vs->_globals);
    }

    for (int bucket = 0; bucket < MENAI_POOL_NUM_BUCKETS; bucket++) {
        void *ptr = vs->_pool_heads[bucket];
        while (ptr != NULL) {
            void *next = *(void **)ptr;
            free(ptr);
            ptr = next;
        }
    }

    free(vs);
}