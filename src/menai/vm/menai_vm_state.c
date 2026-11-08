/*
 * menai_vm_state.c — MenaiVMState lifecycle management.
 *
 * menai_vm_state_alloc creates and initialises a fresh MenaiVMState.  The
 * integer cache and the empty list/dict/set/vector singletons are allocated
 * from this instance's pool so they participate in the same allocator
 * lifecycle as every other value.  None and the booleans are immediates, so
 * they have no per-instance state at all.
 *
 * menai_vm_state_free drains the pool free-lists and frees the struct itself.
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

    /*
     * Set the allocation budget before the first allocation, so that the
     * singleton values are accounted against it like any other value.
     */
    vs->_bytes_in_use = 0;
    vs->_byte_budget = MENAI_DEFAULT_BYTE_BUDGET;

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_init(&vs->_leak_set);
#endif

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

    vs->empty_vector = alloc_menai_vector(vs, 0);
    if (!vs->empty_vector) {
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
     * teardown so they are not misreported as leaks.  With the execute-time
     * collection working, this should find nothing.
     */
    menai_closure_gc_collect(vs, NULL);
    menai_closure_registry_free(vs);

    free(vs->_trace.instr_counts);
    free(vs->_trace.call_counts);

    if (vs->empty_vector) {
        menai_value_free(vs, (MenaiValue *)vs->empty_vector);
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
