/*
 * menai_vm_gc.c — targeted mark-and-sweep cycle collector for closures.
 *
 * Closures (MenaiFunction) are the only Menai value type that can form
 * reference cycles, via letrec capture slots.  This collector tracks all
 * live closures in a registry and runs a five-phase mark-and-sweep at the
 * end of each execute call and at VM teardown.
 *
 * Phase 1: Mark all closures reachable from roots.
 * Phase 2: Partition the registry into live (marked) and dead (unmarked).
 * Phase 3: Break internal edges — bare refcount decrements only.
 * Phase 4: Free dead closures — destruction only.
 * Phase 5: Free orphaned non-closure values — destruction only.
 *
 * The phases are strictly separated.  See AGENTS.md for invariants.
 */
#include <stdlib.h>
#include <stdint.h>
#include <assert.h>

#include "menai_vm_c.h"

/*
 * gc_mark_value — recursively trace from a value, marking all reachable
 * closures.  Containers cannot form cycles in Menai, so re-traversal
 * through shared containers is wasted work but harmless.  The gc_mark bit
 * on MenaiFunction terminates cycles among closures.
 */
static void
gc_mark_value(MenaiValue *val)
{
    if (val == NULL) {
        return;
    }

    switch (val->ob_type) {
    case MENAITYPE_NONE:
    case MENAITYPE_BOOLEAN:
    case MENAITYPE_INTEGER:
    case MENAITYPE_FLOAT:
    case MENAITYPE_COMPLEX:
    case MENAITYPE_STRING:
        /* Leaf types — cannot contain closures. */
        break;

    case MENAITYPE_FUNCTION: {
        MenaiFunction *fn = (MenaiFunction *)val;
        if (fn->gc_mark) {
            return;
        }

        fn->gc_mark = 1;
        for (ssize_t i = 0; i < fn->ncap; i++) {
            if (fn->captures[i] != NULL) {
                gc_mark_value(fn->captures[i]);
            }
        }

        break;
    }

    case MENAITYPE_LIST: {
        MenaiList *lst = (MenaiList *)val;
        if (lst->owner != NULL) {
            /* View — trace through the backing owner. */
            gc_mark_value((MenaiValue *)lst->owner);
        } else {
            for (ssize_t i = 0; i < lst->length; i++) {
                gc_mark_value(lst->elements[i]);
            }
        }

        break;
    }

    case MENAITYPE_DICT: {
        MenaiDict *d = (MenaiDict *)val;
        for (ssize_t i = 0; i < d->length; i++) {
            gc_mark_value(d->keys[i]);
            gc_mark_value(d->values[i]);
        }

        break;
    }

    case MENAITYPE_SET: {
        MenaiSet *s = (MenaiSet *)val;
        for (ssize_t i = 0; i < s->length; i++) {
            gc_mark_value(s->elements[i]);
        }

        break;
    }

    case MENAITYPE_STRUCT: {
        MenaiStruct *st = (MenaiStruct *)val;
        gc_mark_value((MenaiValue *)st->struct_type);
        for (int i = 0; i < st->nfields; i++) {
            gc_mark_value(st->items[i]);
        }

        break;
    }

    case MENAITYPE_STRUCTTYPE: {
        MenaiStructType *stt = (MenaiStructType *)val;
        gc_mark_value((MenaiValue *)stt->name);
        for (int i = 0; i < stt->nfields; i++) {
            gc_mark_value((MenaiValue *)stt->fields[i].name);
        }

        break;
    }

    case MENAITYPE_SYMBOL: {
        MenaiSymbol *sym = (MenaiSymbol *)val;
        gc_mark_value((MenaiValue *)sym->name);
        break;
    }

    case MENAITYPE_BYTES: {
        MenaiBytes *b = (MenaiBytes *)val;
        if (b->owner != NULL) {
            gc_mark_value((MenaiValue *)b->owner);
        }

        break;
    }

    default:
        assert(0);
    }
}

/*
 * _gc_is_dead — check if a MenaiValue * is a dead closure in the dead array.
 * Uses gc_mark == 2 as the "dead" marker (set in Phase 2 for dead closures).
 */
static int
_gc_is_dead(MenaiValue *val)
{
    return val != NULL &&
           val->ob_type == MENAITYPE_FUNCTION &&
           ((MenaiFunction *)val)->gc_mark == 2;
}

/*
 * _gc_detach_dead_from_list — NULL out elements of a list that are dead
 * closures, decrementing their refcounts.  This prevents the list's
 * finalizer from releasing already-freed closures when the list itself is
 * freed in Phase 5.  The bare decrement drops the reference the list held,
 * allowing the dead closure's refcnt to reach 0 for Phase 4.
 */
static void
_gc_detach_dead_from_list(MenaiList *lst)
{
    if (lst->owner != NULL) {
        return;
    }

    for (ssize_t i = 0; i < lst->length; i++) {
        MenaiValue *e = lst->elements[i];
        if (_gc_is_dead(e)) {
            e->ob_refcnt--;
            lst->elements[i] = NULL;
        }
    }
}

/*
 * _gc_detach_dead_from_dict — NULL out keys/values of a dict that are dead
 * closures, decrementing their refcounts.
 */
static void
_gc_detach_dead_from_dict(MenaiDict *d)
{
    for (ssize_t i = 0; i < d->length; i++) {
        MenaiValue *k = d->keys[i];
        if (_gc_is_dead(k)) {
            k->ob_refcnt--;
            d->keys[i] = NULL;
        }

        MenaiValue *v = d->values[i];
        if (_gc_is_dead(v)) {
            v->ob_refcnt--;
            d->values[i] = NULL;
        }
    }
}

/*
 * _gc_detach_dead_from_set — NULL out elements of a set that are dead
 * closures, decrementing their refcounts.
 */
static void
_gc_detach_dead_from_set(MenaiSet *s)
{
    for (ssize_t i = 0; i < s->length; i++) {
        MenaiValue *e = s->elements[i];
        if (_gc_is_dead(e)) {
            e->ob_refcnt--;
            s->elements[i] = NULL;
        }
    }
}

/*
 * _gc_detach_dead_from_struct — NULL out fields of a struct that are dead
 * closures, decrementing their refcounts.
 */
static void
_gc_detach_dead_from_struct(MenaiStruct *st)
{
    for (int i = 0; i < st->nfields; i++) {
        MenaiValue *item = st->items[i];
        if (_gc_is_dead(item)) {
            item->ob_refcnt--;
            st->items[i] = NULL;
        }
    }
}

/*
 * menai_closure_gc_collect — run the closure cycle collector.
 *
 * Phase 1: Mark all closures reachable from roots.
 * Phase 2: Partition the registry into live (marked) and dead (unmarked).
 * Phase 3: Break internal edges — bare refcount decrements only.
 * Phase 4: Free dead closures — destruction only.
 * Phase 5: Free orphaned non-closure values — destruction only.
 *
 * extra_root is the execute result (or NULL at teardown).  When
 * _globals_valid is set, all globals entries are also roots.
 */
void
menai_closure_gc_collect(MenaiVMState *vs, MenaiValue *extra_root)
{
    /*
     * Phase 1 — Mark.
     */
    if (vs->_globals_valid) {
        for (ssize_t i = 0; i < vs->_globals.count; i++) {
            gc_mark_value(vs->_globals.entries[i].value);
        }
    }

    if (extra_root != NULL) {
        gc_mark_value(extra_root);
    }

    /*
     * Phase 2 — Partition.
     *
     * Walk the registry.  Marked closures survive: clear their gc_mark bit
     * and compact them into the front of the registry.  Unmarked closures
     * go into a separate dead array and are tagged gc_mark = 2 so they can
     * be identified as dead by the orphan-detachment pass in Phase 3.
     */
    ssize_t live_count = 0;
    ssize_t dead_count = 0;

    MenaiFunction **dead = (MenaiFunction **)malloc(
        (size_t)vs->_closure_registry_count * sizeof(MenaiFunction *));
    if (dead == NULL) {
        /* Bail — clear all marks and try next time. */
        for (ssize_t i = 0; i < vs->_closure_registry_count; i++) {
            vs->_closure_registry[i]->gc_mark = 0;
        }

        return;
    }

    for (ssize_t i = 0; i < vs->_closure_registry_count; i++) {
        MenaiFunction *fn = vs->_closure_registry[i];
        if (fn->gc_mark) {
            fn->gc_mark = 0;
            vs->_closure_registry[live_count++] = fn;
        } else {
            fn->gc_mark = 2;
            dead[dead_count++] = fn;
        }
    }

    vs->_closure_registry_count = live_count;

    /*
     * Phase 3 — Break internal edges (arithmetic only).
     *
     * For each dead closure, decrement each capture's refcount with a bare
     * decrement and NULL the slot.  Nothing may be freed in this phase.
     *
     * Non-closure captures that reach refcnt == 0 are collected into the
     * orphans array for Phase 5.  They cannot be freed here because a
     * cascading finalizer (e.g. menai_list_final releasing its elements)
     * could free a dead closure that is still referenced by the dead array,
     * causing a use-after-free in Phase 4.
     */
    ssize_t orphan_count = 0;
    MenaiValue **orphans = NULL;
    if (dead_count > 0) {
        orphans = (MenaiValue **)malloc(
            (size_t)dead_count * sizeof(MenaiValue *));
        /* If malloc fails, orphans are leaked — acceptable in OOM. */
    }

    for (ssize_t i = 0; i < dead_count; i++) {
        MenaiFunction *fn = dead[i];
        for (ssize_t j = 0; j < fn->ncap; j++) {
            MenaiValue *cap = fn->captures[j];
            if (cap != NULL) {
                cap->ob_refcnt--;
                fn->captures[j] = NULL;

                if (cap->ob_type != MENAITYPE_FUNCTION &&
                    cap->ob_refcnt == 0 && orphans != NULL) {
                    orphans[orphan_count++] = cap;
                }
            }
        }
    }

    /*
     * Phase 3b — Detach dead closures from orphaned containers.
     *
     * Orphaned containers (lists, dicts, sets, structs) may hold references
     * to dead closures.  When the container is freed in Phase 5, its
     * finalizer would release those elements — but the dead closures are
     * freed in Phase 4, causing a use-after-free.  To prevent this, NULL
     * out any elements that are dead closures (gc_mark == 2) and decrement
     * their refcounts with a bare decrement.  This drops the reference the
     * container held, allowing the dead closure's refcnt to reach 0 for
     * Phase 4.  Without this decrement, the dead closure would retain a
     * dangling refcnt > 0 and never be freed.
     */
    for (ssize_t i = 0; i < orphan_count; i++) {
        MenaiValue *v = orphans[i];
        switch (v->ob_type) {
        case MENAITYPE_LIST:
            _gc_detach_dead_from_list((MenaiList *)v);
            break;

        case MENAITYPE_DICT:
            _gc_detach_dead_from_dict((MenaiDict *)v);
            break;

        case MENAITYPE_SET:
            _gc_detach_dead_from_set((MenaiSet *)v);
            break;

        case MENAITYPE_STRUCT:
            _gc_detach_dead_from_struct((MenaiStruct *)v);
            break;

        default:
            break;
        }
    }

    /*
     * Phase 4 — Free dead closures (destruction only).
     *
     * After Phase 3, a dead closure's refcount is zero only if every
     * reference came from other dead closures' capture slots.  A closure
     * with refcnt > 0 still has an external reference (e.g. from a code
     * object's constant pool, which is released after the GC runs).  Such
     * closures must not be freed — they are returned to the registry and
     * will be reclaimed when the external reference is released (or by a
     * future collection once the external reference is gone).
     *
     * For closures with refcnt == 0, call menai_value_free directly — do
     * not use menai_value_release, which would decrement again.  The
     * finalizer releases any captures that pointed at live values (those
     * edges were not broken in Phase 3 because the capture pointed outside
     * the dead set).  The _gc_in_progress flag prevents the finalizer from
     * touching the already-compacted registry.
     *
     * Clear gc_mark before freeing so the finalizer sees a clean state.
     */
    vs->_gc_in_progress = 1;

    for (ssize_t i = 0; i < dead_count; i++) {
        MenaiFunction *fn = dead[i];
        fn->gc_mark = 0;
        if (fn->ob_refcnt == 0) {
            menai_value_free(vs, (MenaiValue *)fn);
        } else {
            /*
             * External reference still exists — return to the registry.
             * This closure is not part of a pure cycle; it will be
             * reclaimed when the external reference is released.
             */
            vs->_closure_registry[vs->_closure_registry_count++] = fn;
        }
    }

    /*
     * Phase 5 — Free orphaned non-closure values (destruction only).
     *
     * Non-closure values whose last reference was a dead closure capture
     * have refcnt == 0 after Phase 3 but were not freed there (to avoid
     * cascading frees that could corrupt the dead array).  Now that all
     * dead closures have been destroyed in Phase 4, it is safe to free all
     * orphans.  Container finalizers are safe because Phase 3b already
     * NULLed out dead closure elements, and no orphan can contain another
     * orphan: a value held by a container has refcnt > 0 (from the
     * container), so it would not have reached refcnt == 0 in Phase 3 and
     * would not be in the orphans array.
     */
    for (ssize_t i = 0; i < orphan_count; i++) {
        menai_value_free(vs, orphans[i]);
    }

    free(orphans);
    vs->_gc_in_progress = 0;
    free(dead);
}

/*
 * menai_closure_registry_free — free the registry array itself.
 * Called from menai_vm_state_free after the final collection.
 */
void
menai_closure_registry_free(MenaiVMState *vs)
{
    free(vs->_closure_registry);
    vs->_closure_registry = NULL;
    vs->_closure_registry_count = 0;
    vs->_closure_registry_capacity = 0;
}
