/*
 * menai_vm_globals.c — GlobalsTable management for the Menai VM.
 *
 * GlobalsTable is an open-addressing hash table mapping C-string global
 * names to MenaiValue pointers.  It is built once by the bridge and cached;
 * the execute loop reads from it directly without copying.
 */
#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <string.h>

#include "menai_vm_c.h"

/*
 * globals_slot_insert — insert a (name, hash, value) triple into the
 * GlobalsTable's open-addressing slot array.  The slot array must already
 * be allocated with slot_count > 0.  Does NOT touch the entries array —
 * the caller is responsible for that.
 */
static void
globals_slot_insert(GlobalsTable *gt, const char *name, hash_t h, MenaiValue *value)
{
    ssize_t mask = gt->slot_count - 1;
    uhash_t perturb = (uhash_t)h;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);
    for (;;) {
        if (gt->slots[slot].name == NULL) {
            gt->slots[slot].name = name;
            gt->slots[slot].hash = h;
            gt->slots[slot].value = value;
            break;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}

/*
 * globals_alloc_slots — allocate the entries and slots arrays for a
 * GlobalsTable that will hold n entries.  Returns 0 on success, -1 on
 * error (no Python exception set — caller handles).
 */
static int
globals_alloc_slots(GlobalsTable *gt, ssize_t n)
{
    gt->slots = NULL;
    gt->entries = NULL;
    gt->slot_count = 0;
    gt->count = 0;

    if (n > 0) {
        gt->entries = (GlobalsEntry *)malloc(n * sizeof(GlobalsEntry));
        if (gt->entries == NULL) {
            return MENAI_ERR_NOMEM;
        }

        ssize_t min_slots = (n * 3 + 1) / 2;
        ssize_t sc = 4;
        while (sc < min_slots) {
            sc <<= 1;
        }

        gt->slots = (GlobalsSlot *)calloc(sc, sizeof(GlobalsSlot));
        if (gt->slots == NULL) {
            free(gt->entries);
            gt->entries = NULL;
            return MENAI_ERR_NOMEM;
        }

        gt->slot_count = sc;
    }

    return 0;
}

/*
 * globals_free — free a GlobalsTable and all its owned resources.
 */
void
globals_free(MenaiVMState *vs, GlobalsTable *gt)
{
    for (ssize_t i = 0; i < gt->count; i++) {
        free((char *)gt->entries[i].name);

        menai_value_release(vs, gt->entries[i].value);
    }

    free(gt->slots);
    free(gt->entries);
    gt->slots = NULL;
    gt->entries = NULL;
    gt->slot_count = 0;
    gt->count = 0;
}

/*
 * globals_build_from_dict — build a GlobalsTable from a native MenaiDict.
 *
 * Builds a complete lookup table with hash slots.  Names are strdup'd from
 * the MenaiString keys via alloc_utf8_from_menai_string.  Values are retained.
 * Returns 0 on success, MENAI_ERR_* on error.
 */
int
globals_build_from_dict(MenaiVMState *vs, GlobalsTable *gt, MenaiDict *d)
{
    ssize_t n = d->length;

    int err = globals_alloc_slots(gt, n);
    if (err < 0) {
        return err;
    }

    for (ssize_t i = 0; i < n; i++) {
        MenaiValue *k = d->keys[i];
        if (MENAI_UNLIKELY(!IS_MENAI_STRING(k))) {
            globals_free(vs, gt);
            return MENAI_ERR_TYPE;
        }

        char *name_copy = alloc_utf8_from_menai_string((MenaiString *)k, NULL);
        if (name_copy == NULL) {
            globals_free(vs, gt);
            return MENAI_ERR_NOMEM;
        }

        menai_value_retain(d->values[i]);
        gt->entries[gt->count].name = name_copy;
        gt->entries[gt->count].value = d->values[i];
        gt->count++;

        hash_t h = menai_name_str_hash(name_copy);
        globals_slot_insert(gt, name_copy, h, d->values[i]);
    }

    return 0;
}

/*
 * globals_lookup — look up a name in a GlobalsTable using a precomputed hash.
 * Returns the value if found, NULL if not found.
 */
MenaiValue *
globals_lookup(const GlobalsTable *gt, const char *name, hash_t h)
{
    if (gt->slot_count == 0) {
        return NULL;
    }

    ssize_t mask = gt->slot_count - 1;
    uhash_t perturb = (uhash_t)h;
    ssize_t slot = (ssize_t)(perturb & (uhash_t)mask);
    for (;;) {
        GlobalsSlot *s = &gt->slots[slot];
        if (s->name == NULL) {
            return NULL;
        }

        if (s->hash == h && strcmp(s->name, name) == 0) {
            return s->value;
        }

        perturb >>= 5;
        slot = (ssize_t)((5 * (uhash_t)slot + 1 + perturb) & (uhash_t)mask);
    }
}