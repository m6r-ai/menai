/*
 * menai_vm_integer.c — MenaiInteger type implementation.
 *
 * Three-tier representation: small integer cache for [-5, 256], inline long
 * for values that fit in a C long, and MenaiBigInt bignum for everything else.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiInteger *
alloc_menai_integer_from_long(MenaiVMState *vs, long n)
{
    if (n >= MENAI_INT_CACHE_MIN && n <= MENAI_INT_CACHE_MAX) {
        MenaiInteger *cached = vs->integer_cache[n - MENAI_INT_CACHE_MIN];
        menai_value_retain((MenaiValue *)cached);
        return cached;
    }

    MenaiInteger *r = (MenaiInteger *)menai_alloc(vs, sizeof(MenaiInteger));
    if (r == NULL) {
        return NULL;
    }

    r->ob_refcnt = 1;
    r->ob_type = MENAITYPE_INTEGER;
    MENAI_SET_MAGIC((MenaiValue *)r);
    r->is_big = 0;
    r->fixed = n;
    menai_bigint_init(&r->big);

    return r;
}

MenaiInteger *
alloc_menai_integer_from_long_long(MenaiVMState *vs, long long n)
{
    if (n >= (long long)MENAI_INT_CACHE_MIN &&
            n <= (long long)MENAI_INT_CACHE_MAX) {
        MenaiInteger *cached = vs->integer_cache[(int)n - MENAI_INT_CACHE_MIN];
        menai_value_retain((MenaiValue *)cached);
        return cached;
    }

    if (n >= (long long)LONG_MIN && n <= (long long)LONG_MAX) {
        return alloc_menai_integer_from_long(vs, (long)n);
    }

    MenaiBigInt big;
    menai_bigint_init(&big);
    if (menai_bigint_from_long_long(vs, n, &big) < 0) {
        return NULL;
    }

    return alloc_menai_integer_from_bigint(vs, big);
}

MenaiInteger *
alloc_menai_integer_from_bigint(MenaiVMState *vs, MenaiBigInt src)
{
    /*
     * If the value fits in a long, demote to small representation so the
     * inline fast path is used for subsequent operations.
     */
    if (src.length <= 2 && menai_bigint_fits_long(&src)) {
        long v;
        if (menai_bigint_to_long(&src, &v) < 0) {
            menai_bigint_final(vs, &src);
            return NULL;
        }

        menai_bigint_final(vs, &src);
        return alloc_menai_integer_from_long(vs, v);
    }

    MenaiInteger *r = (MenaiInteger *)menai_alloc(vs, sizeof(MenaiInteger));
    if (r == NULL) {
        menai_bigint_final(vs, &src);
        return NULL;
    }

    r->ob_refcnt = 1;
    r->ob_type = MENAITYPE_INTEGER;
    MENAI_SET_MAGIC((MenaiValue *)r);
    r->is_big = 1;
    r->fixed = 0;
    r->big = src; /* transfer ownership */

    return r;
}
