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

#include "menai_vm_c.h"

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
            ((MenaiValue *)ptr)->ob_alloc_bucket = -1;
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
    return ptr;
}

void
menai_free(MenaiVMState *vs, void *ptr)
{
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

/*
 * menai_vm_state_alloc — allocate and initialise a fresh MenaiVMState.
 *
 * The none/boolean singletons are stored inline and initialised directly.
 * The integer cache and empty list/dict/set are allocated from this
 * instance's pool so they participate in the same allocator lifecycle as
 * every other value.
 */
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
    if (vs->_cached_globals_gt_valid) {
        globals_free(vs, &vs->_cached_globals_gt);
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
