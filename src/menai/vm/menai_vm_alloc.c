/*
 * menai_vm_alloc.c — pool allocator and MenaiValue allocation wrapper.
 *
 * menai_pool_alloc / menai_pool_free — the inner pool allocator.  A
 * MenaiPoolHeader is prepended to every block to record the bucket index so
 * the block can be returned to the correct free-list on free.  No
 * assumptions are made about what the caller stores in the block.
 *
 * menai_alloc / menai_free — the outer wrapper for MenaiValue-based objects.
 * Calls menai_pool_alloc under the hood, then sets the magic field and
 * registers the block with the leak detector (when MENAI_DEBUG_LEAKS is
 * defined).
 *
 * The pool uses 8 power-of-2 buckets (32–4096 bytes).  Each bucket is a
 * singly-linked free-list threaded through the first sizeof(void *) bytes
 * of the user data area.  A per-bucket depth cap prevents unbounded
 * memory retention.  The free lists live in MenaiVMState so that each VM
 * instance has its own pool; no state is shared across instances.
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
 * The leak set is a chained hash set of void * pointers.  Each entry is a
 * malloc'd MenaiLeakNode linked into a bucket.  Deletion is O(1) — find the
 * node in its bucket and unlink it.  The per-entry allocation is fine for a
 * debug-only tool that is never compiled into production builds.
 *
 * menai_alloc adds every block it returns; menai_free removes every block
 * it receives.  At VM teardown, menai_leak_set_report walks the set and
 * prints any pointer that is still tracked (excluding known singletons).
 */
#ifdef MENAI_DEBUG_LEAKS

#define MENAI_LEAK_SET_INITIAL_CAP 256
#define MENAI_LEAK_SET_MAX_LOAD_NUM 2
#define MENAI_LEAK_SET_MAX_LOAD_DEN 3

static int
_leak_set_grow(MenaiLeakSet *ls)
{
    ssize_t new_cap = ls->bucket_count * 2;
    if (new_cap < MENAI_LEAK_SET_INITIAL_CAP) {
        new_cap = MENAI_LEAK_SET_INITIAL_CAP;
    }

    fprintf(stderr, "leak set buckets: %zd\n", new_cap);

    MenaiLeakNode **new_buckets = (MenaiLeakNode **)calloc((size_t)new_cap, sizeof(MenaiLeakNode *));
    if (!new_buckets) {
        return -1;
    }

    ssize_t new_mask = new_cap - 1;
    for (ssize_t i = 0; i < ls->bucket_count; i++) {
        MenaiLeakNode *node = ls->buckets[i];
        while (node != NULL) {
            MenaiLeakNode *next = node->next;
            ssize_t b = (ssize_t)((uhash_t)(uintptr_t)node->ptr & (uhash_t)new_mask);
            node->next = new_buckets[b];
            new_buckets[b] = node;
            node = next;
        }
    }

    free(ls->buckets);
    ls->buckets = new_buckets;
    ls->bucket_count = new_cap;
    return 0;
}

void
menai_leak_set_init(MenaiLeakSet *ls)
{
    ls->buckets = NULL;
    ls->bucket_count = 0;
    ls->count = 0;
}

void
menai_leak_set_final(MenaiLeakSet *ls)
{
    for (ssize_t i = 0; i < ls->bucket_count; i++) {
        MenaiLeakNode *node = ls->buckets[i];
        while (node != NULL) {
            MenaiLeakNode *next = node->next;
            free(node);
            node = next;
        }
    }

    free(ls->buckets);
    ls->buckets = NULL;
    ls->bucket_count = 0;
    ls->count = 0;
}

void
menai_leak_set_add(MenaiLeakSet *ls, void *ptr)
{
    if (ptr == NULL) {
        return;
    }

    if (ls->bucket_count == 0 ||
        ls->count * MENAI_LEAK_SET_MAX_LOAD_DEN >=
        ls->bucket_count * MENAI_LEAK_SET_MAX_LOAD_NUM) {
        if (_leak_set_grow(ls) < 0) {
            return;
        }
    }

    ssize_t mask = ls->bucket_count - 1;
    ssize_t b = (ssize_t)((uhash_t)(uintptr_t)ptr & (uhash_t)mask);
    for (MenaiLeakNode *node = ls->buckets[b]; node != NULL; node = node->next) {
        if (node->ptr == ptr) {
            return;
        }
    }

    MenaiLeakNode *node = (MenaiLeakNode *)malloc(sizeof(MenaiLeakNode));
    if (!node) {
        return;
    }

    node->ptr = ptr;
    node->next = ls->buckets[b];
    ls->buckets[b] = node;
    ls->count++;
}

void
menai_leak_set_remove(MenaiLeakSet *ls, void *ptr)
{
    if (ptr == NULL || ls->bucket_count == 0) {
        return;
    }

    ssize_t mask = ls->bucket_count - 1;
    ssize_t b = (ssize_t)((uhash_t)(uintptr_t)ptr & (uhash_t)mask);

    MenaiLeakNode *prev = NULL;
    for (MenaiLeakNode *node = ls->buckets[b]; node != NULL; node = node->next) {
        if (node->ptr == ptr) {
            if (prev != NULL) {
                prev->next = node->next;
            } else {
                ls->buckets[b] = node->next;
            }

            free(node);
            ls->count--;
            return;
        }

        prev = node;
    }
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

    if ((void *)&vs->empty_list_storage == ptr ||
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

    for (ssize_t i = 0; i < ls->bucket_count; i++) {
        for (MenaiLeakNode *node = ls->buckets[i]; node != NULL; node = node->next) {
            void *entry = node->ptr;

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
    }

    if (leaks > 0) {
        fprintf(stderr, "MENAI LEAK: %zd leaked value(s) detected\n", leaks);
    }
}

#endif /* MENAI_DEBUG_LEAKS */

/*
 * _bucket_for — return the bucket index for a given total block size.
 *
 * size must be in [MENAI_POOL_MIN_SIZE, MENAI_POOL_MAX_SIZE].  The returned
 * bucket index satisfies: (1 << (bucket + MENAI_POOL_LOG_MIN_SIZE)) >= size.
 */
static inline int
_bucket_for(size_t size)
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

    return bucket;
#endif
}

static inline MenaiPoolHeader *
_header_of(void *user_ptr)
{
    return (MenaiPoolHeader *)((char *)user_ptr - sizeof(MenaiPoolHeader));
}

/*
 * menai_pool_alloc — inner pool allocator.  Allocates a block of at least
 * size bytes from the per-instance power-of-2 free-list pool.  A
 * MenaiPoolHeader is prepended to record the bucket index.  The returned
 * pointer is suitable for any use — no MenaiValue assumptions.
 */
void *
menai_pool_alloc(MenaiVMState *vs, size_t size)
{
    size_t total = size + sizeof(MenaiPoolHeader);

    if (total > MENAI_POOL_MAX_SIZE) {
        char *raw = (char *)malloc(total);
        if (!raw) {
            return NULL;
        }

        MenaiPoolHeader *hdr = (MenaiPoolHeader *)raw;
        hdr->bucket = -1;
        return raw + sizeof(MenaiPoolHeader);
    }

    int bucket = _bucket_for(total);

    void *user_ptr;
    BucketEntry *pool_bucket = &vs->pool[bucket];
    if (pool_bucket->head) {
        user_ptr = pool_bucket->head;
        pool_bucket->head = *(void **)user_ptr;
        pool_bucket->depth--;
    } else {
        char *raw = (char *)malloc((size_t)1 << (bucket + MENAI_POOL_LOG_MIN_SIZE));
        if (!raw) {
            return NULL;
        }
        user_ptr = raw + sizeof(MenaiPoolHeader);
    }

    _header_of(user_ptr)->bucket = (int16_t)bucket;
    return user_ptr;
}

/*
 * menai_pool_free — return a block to the pool.  Reads the bucket index from
 * the hidden MenaiPoolHeader to route the block to the correct free-list, or
 * frees it directly if it was an out-of-pool allocation.
 */
void
menai_pool_free(MenaiVMState *vs, void *ptr)
{
    if (ptr == NULL) {
        return;
    }

    MenaiPoolHeader *hdr = _header_of(ptr);
    int16_t bucket = hdr->bucket;

    if (bucket == -1) {
        free(hdr);
        return;
    }

    BucketEntry *pool_bucket = &vs->pool[bucket];
    if (pool_bucket->depth < MENAI_POOL_MAX_DEPTH) {
        *(void **)ptr = pool_bucket->head;
        pool_bucket->head = ptr;
        pool_bucket->depth++;
        return;
    }

    free(hdr);
}

/*
 * menai_alloc — outer wrapper for MenaiValue-based objects.  Calls
 * menai_pool_alloc, then registers with the leak detector (when enabled).
 * Callers are responsible for setting the magic field via MENAI_SET_MAGIC.
 */
void *
menai_alloc(MenaiVMState *vs, size_t size)
{
    void *ptr = menai_pool_alloc(vs, size);
    if (!ptr) {
        return NULL;
    }

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_add(&vs->_leak_set, ptr);
#endif

    return ptr;
}

/*
 * menai_free — outer wrapper for MenaiValue-based objects.  Unregisters from
 * the leak detector (when enabled), then calls menai_pool_free.
 */
void
menai_free(MenaiVMState *vs, void *ptr)
{
    if (ptr == NULL) {
        return;
    }

#ifdef MENAI_DEBUG_LEAKS
    menai_leak_set_remove(&vs->_leak_set, ptr);
#endif

    menai_pool_free(vs, ptr);
}
