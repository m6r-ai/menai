/*
 * menai_vm_integer.c — MenaiInteger type implementation.
 *
 * Integers have two representations: a fixnum immediate when the value is in
 * [MENAI_FIXNUM_MIN, MENAI_FIXNUM_MAX], and a boxed MenaiInteger holding a
 * MenaiBigInt otherwise.  The constructors below are the only place that chooses,
 * so a given value is always represented the same way and the immediate and boxed
 * paths for hashing and equality cannot disagree.
 */
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

MenaiValue *
alloc_menai_integer_from_long(MenaiVMState *vs, int64_t n)
{
    if (menai_fixnum_in_range(n)) {
        return menai_make_fixnum(n);
    }

    MenaiBigInt big;
    menai_bigint_init(&big);
    if (menai_bigint_from_long_long(vs, n, &big) < 0) {
        return NULL;
    }

    return alloc_menai_integer_from_bigint(vs, big);
}

MenaiValue *
alloc_menai_integer_from_long_long(MenaiVMState *vs, int64_t n)
{
    return alloc_menai_integer_from_long(vs, n);
}

MenaiValue *
alloc_menai_integer_from_bigint(MenaiVMState *vs, MenaiBigInt src)
{
    /*
     * A bignum that happens to be in fixnum range becomes a fixnum.  Arithmetic
     * that grows past the range and then shrinks back must land on the same
     * representation as a value that never left it, or equality and hashing would
     * depend on how a value was computed.
     *
     * The test is specifically the fixnum range, not whether the value fits a C
     * long: those differ, and demoting to a long would hand the value back to a
     * constructor that re-promotes anything outside the fixnum range.
     */
    if (src.length <= 2) {
        long v;
        if (menai_bigint_to_long(&src, &v) == 0 && menai_fixnum_in_range((int64_t)v)) {
            menai_bigint_final(vs, &src);
            return menai_make_fixnum((int64_t)v);
        }
    }

    MenaiInteger *r = (MenaiInteger *)menai_value_alloc(vs, MENAITYPE_INTEGER, sizeof(MenaiInteger));
    if (r == NULL) {
        menai_bigint_final(vs, &src);
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)r);
    r->big = src; /* transfer ownership */

    return (MenaiValue *)r;
}
