/*
 * menai_vm_bytes.c — MenaiBytes type implementation.
 *
 * MenaiBytes stores its data inline in the same allocation as the struct,
 * using a C99 flexible array member.  A single menai_alloc call covers both
 * the header and the data array for owning bytes.  Slice views allocate
 * only the header (sizeof(MenaiBytes)) and point their data pointer into
 * the owner's inline storage, exactly mirroring MenaiList's pattern.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

/*
 * alloc_menai_bytes — allocate an owning MenaiBytes with room for n bytes.
 * length is set to n; hash is set to -1; data is uninitialised.
 * Returns a new reference, or NULL on allocation failure.
 */
MenaiBytes *
alloc_menai_bytes(ssize_t n)
{
    size_t sz = sizeof(MenaiBytes) + (size_t)n;
    MenaiBytes *obj = (MenaiBytes *)menai_alloc(sz);
    if (obj == NULL) {
        return NULL;
    }

    obj->ob_refcnt = 1;
    obj->ob_type = MENAITYPE_BYTES;
    obj->length = n;
    obj->hash = -1;
    obj->owner = NULL;
    obj->data = obj->inline_data;

    return obj;
}

MenaiBytes *
alloc_menai_bytes_from_raw(const uint8_t *src, ssize_t n)
{
    MenaiBytes *obj = alloc_menai_bytes(n);
    if (!obj) {
        return NULL;
    }

    memcpy(obj->inline_data, src, (size_t)n);

    return obj;
}

MenaiBytes *
alloc_menai_bytes_from_slice(MenaiBytes *b, ssize_t start, ssize_t end)
{
    /*
     * Resolve the owner: if b is itself a view, point at its owner so
     * all views are depth-1 from the root data owner.
     */
    MenaiBytes *owner = (b->owner != NULL) ? b->owner : b;

    MenaiBytes *view = (MenaiBytes *)menai_alloc(sizeof(MenaiBytes));
    if (view == NULL) {
        return NULL;
    }

    view->ob_refcnt = 1;
    view->ob_type = MENAITYPE_BYTES;
    menai_value_retain((MenaiValue *)owner);
    view->owner = owner;
    view->data = b->data + start;
    view->length = end - start;
    view->hash = -1;

    return view;
}

MenaiBytes *
alloc_menai_bytes_from_concat(MenaiBytes *a, MenaiBytes *b)
{
    ssize_t la = a->length;
    ssize_t lb = b->length;
    MenaiBytes *obj = alloc_menai_bytes(la + lb);
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

MenaiInteger *
menai_bytes_ref(MenaiValue *b, ssize_t i)
{
    return alloc_menai_integer_from_long((long)((MenaiBytes *)b)->data[i]);
}

MenaiBytes *
alloc_menai_bytes_from_append_u8(MenaiBytes *b, uint8_t value)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(len + 1);
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
alloc_menai_bytes_from_append_multi(MenaiBytes *b, unsigned long long value, int width, int le)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(len + width);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    uint8_t *dest = obj->inline_data + len;
    if (le) {
        for (int i = 0; i < width; i++) {
            dest[i] = (uint8_t)((value >> (i * 8)) & 0xFF);
        }
    } else {
        for (int i = 0; i < width; i++) {
            dest[i] = (uint8_t)((value >> ((width - 1 - i) * 8)) & 0xFF);
        }
    }

    return obj;
}

/*
 * menai_bytes_write_multi — return a copy of b with N bytes at the given
 * offset replaced by the encoded value.  width must be 1–8.
 */
MenaiBytes *
alloc_menai_bytes_from_write_multi(MenaiBytes *b, ssize_t offset, unsigned long long value, int width, int le)
{
    ssize_t len = b->length;
    MenaiBytes *obj = alloc_menai_bytes(len);
    if (!obj) {
        return NULL;
    }

    if (len > 0) {
        memcpy(obj->inline_data, b->data, (size_t)len);
    }

    uint8_t *dest = obj->inline_data + offset;
    if (le) {
        for (int i = 0; i < width; i++) {
            dest[i] = (uint8_t)((value >> (i * 8)) & 0xFF);
        }
    } else {
        for (int i = 0; i < width; i++) {
            dest[i] = (uint8_t)((value >> ((width - 1 - i) * 8)) & 0xFF);
        }
    }

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

    /* FNV-1a over the raw bytes. */
    uint64_t h = 14695981039346656037ULL;
    const unsigned char *p = (const unsigned char *)b->data;
    ssize_t nbytes = b->length;
    for (ssize_t i = 0; i < nbytes; i++) {
        h ^= p[i];
        h *= 1099511628211ULL;
    }

    hash_t result = (hash_t)h;
    if (result == -1) {
        result = -2;
    }

    b->hash = result;

    return result;
}
