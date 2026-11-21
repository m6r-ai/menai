/*
 * menai_vm_bytes.c — MenaiBytes type implementation.
 *
 * MenaiBytes stores its data inline in the same allocation as the struct,
 * using a C99 flexible array member.  A single menai_value_alloc call covers
 * both the header and the data array for owning bytes.  Slice views allocate
 * only the header (sizeof(MenaiBytes)) and point their data pointer into
 * the owner's inline storage, exactly mirroring MenaiList's pattern.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

/*
 * alloc_menai_bytes — allocate an owning MenaiBytes with room for n bytes.
 * length and cap are both set to n; hash is set to -1; data is uninitialised.
 * A shared value is therefore always allocated tight, and only the in-place
 * append path asks for slack (see menai_bytes_reserve).
 * Returns a new reference, or NULL on allocation failure.
 */
MenaiBytes *
alloc_menai_bytes(MenaiVMState *vs, ssize_t n)
{
    size_t sz = sizeof(MenaiBytes) + (size_t)n;
    MenaiBytes *obj = (MenaiBytes *)menai_value_alloc(vs, MENAITYPE_BYTES, sz);
    if (obj == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)obj);
    obj->length = n;
    obj->cap = n;
    obj->hash = -1;
    obj->owner = NULL;
    obj->data = obj->inline_data;

    return obj;
}

/*
 * menai_bytes_reserve — return a MenaiBytes with room for `extra` more bytes
 * appended to it.
 *
 * Returns b itself when it already has the room, and otherwise allocates a
 * replacement with geometric headroom and copies the existing bytes.  The
 * caller must store the result into the register that held b: on growth the
 * result is a new object with a single reference, and the caller releases the
 * old one, so a sequence of appends copies 2n bytes in total rather than
 * rebuilding at every step.  b must be uniquely owned and must own its data —
 * both are the caller's in-place test, not this function's.
 *
 * Returns NULL on allocation failure, having left b untouched.
 */
MenaiBytes *
menai_bytes_reserve(MenaiVMState *vs, MenaiBytes *b, ssize_t extra)
{
    ssize_t len = b->length;
    if (extra <= b->cap - len) {
        return b;
    }

    ssize_t new_cap = (len + extra) * 2;
    MenaiBytes *obj = alloc_menai_bytes(vs, new_cap);
    if (obj == NULL) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    obj->length = len;

    return obj;
}

/*
 * menai_bytes_append_slot — append n bytes to the value in *slot, in place.
 *
 * The caller must have established that the value is unobservable — the
 * four conditions at the append opcodes — because this grows or mutates the
 * object the slot holds and leaves the reference in the slot either way.
 * When the buffer is full the replacement is installed in the slot and the
 * old object released, so the instruction that called this simply breaks.
 *
 * Returns 0 on success and -1 on allocation failure, having left *slot alone.
 */
int
menai_bytes_append_slot(MenaiVMState *vs, MenaiValue **slot, const uint8_t *src, ssize_t n)
{
    MenaiBytes *b = (MenaiBytes *)*slot;
    MenaiBytes *grown = menai_bytes_reserve(vs, b, n);
    if (grown == NULL) {
        return -1;
    }

    if (grown != b) {
        menai_value_release(vs, *slot);
        *slot = (MenaiValue *)grown;
    }

    memcpy(grown->inline_data + grown->length, src, (size_t)n);
    grown->length += n;
    grown->hash = -1;

    return 0;
}

MenaiBytes *
alloc_menai_bytes_from_raw(MenaiVMState *vs, const uint8_t *src, ssize_t n)
{
    MenaiBytes *obj = alloc_menai_bytes(vs, n);
    if (!obj) {
        return NULL;
    }

    memcpy(obj->inline_data, src, (size_t)n);

    return obj;
}

MenaiBytes *
alloc_menai_bytes_from_slice(MenaiVMState *vs, MenaiBytes *b, ssize_t start, ssize_t end)
{
    /*
     * Resolve the owner: if b is itself a view, point at its owner so
     * all views are depth-1 from the root data owner.
     */
    MenaiBytes *owner = (b->owner != NULL) ? b->owner : b;

    MenaiBytes *view = (MenaiBytes *)menai_value_alloc(vs, MENAITYPE_BYTES, sizeof(MenaiBytes));
    if (view == NULL) {
        return NULL;
    }

    MENAI_SET_MAGIC((MenaiValue *)view);
    menai_value_retain((MenaiValue *)owner);
    view->owner = owner;
    view->data = b->data + start;
    view->length = end - start;
    view->cap = view->length;
    view->hash = -1;

    return view;
}

MenaiBytes *
alloc_menai_bytes_from_concat(MenaiVMState *vs, MenaiBytes *a, MenaiBytes *b)
{
    ssize_t la = a->length;
    ssize_t lb = b->length;
    MenaiBytes *obj = alloc_menai_bytes(vs, la + lb);
    if (!obj) {
        return NULL;
    }

    if (la > 0) {
        memcpy(obj->inline_data, a->data, (size_t)la);
    }

    if (lb > 0) {
        memcpy(obj->inline_data + la, b->data, (size_t)lb);
    }

    return obj;
}

MenaiBytes *
alloc_menai_bytes_from_append_u8(MenaiVMState *vs, MenaiBytes *b, uint8_t value)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(vs, len + 1);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    obj->inline_data[len] = value;

    return obj;
}

/*
 * menai_bytes_append_multi — append N bytes encoded from an unsigned long
 * value in the specified endianness.  width must be 1–8.
 */
MenaiBytes *
alloc_menai_bytes_from_append_multi(MenaiVMState *vs, MenaiBytes *b, unsigned long long value, int width, int le)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(vs, len + width);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    menai_bytes_encode_u64(obj->inline_data + len, value, width, le);

    return obj;
}

/*
 * menai_bytes_write_multi — return a copy of b with N bytes at the given
 * offset replaced by the encoded value.  width must be 1–8.
 */
MenaiBytes *
alloc_menai_bytes_from_write_multi(MenaiVMState *vs, MenaiBytes *b, ssize_t offset, unsigned long long value, int width, int le)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(vs, len);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    menai_bytes_encode_u64(obj->inline_data + offset, value, width, le);

    return obj;
}

int
menai_bytes_equal(MenaiBytes *a, MenaiBytes *b)
{
    ssize_t la = a->length;
    if (la != b->length) {
        return 0;
    }

    return memcmp(a->data, b->data, (size_t)la) == 0;
}

int
menai_bytes_compare(MenaiBytes *a, MenaiBytes *b)
{
    ssize_t la = a->length;
    ssize_t lb = b->length;
    ssize_t min_len = la < lb ? la : lb;
    int cmp = memcmp(a->data, b->data, (size_t)min_len);
    if (cmp != 0) {
        return cmp < 0 ? -1 : 1;
    }

    if (la < lb) {
        return -1;
    }

    if (la > lb) {
        return 1;
    }

    return 0;
}

hash_t
menai_bytes_hash(MenaiBytes *b)
{
    if (b->hash != -1) {
        return b->hash;
    }

    hash_t result = menai_hash_bytes(b->data, (size_t)b->length);
    b->hash = result;

    return result;
}
