/*
 * menai_vm_gc.c — targeted mark-and-sweep cycle collector for closures.
 *
 * Closures (MenaiFunction) are the only Menai value type that can form
 * reference cycles, via letrec capture slots.  This collector tracks all
 * live closures in a registry and runs a four-phase mark-and-sweep at the
 * end of each execute call and at VM teardown.
 *
 * The four phases (mark, partition, break internal edges, free) are
 * strictly separated — see CLOSURE_GC_DESIGN.md for the full rationale.
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
 * menai_closure_gc_collect — run the four-phase closure cycle collector.
 *
 * Phase 1: Mark all closures reachable from roots.
 * Phase 2: Partition the registry into live (marked) and dead (unmarked).
 * Phase 3: Break internal edges — bare refcount decrements only.
 * Phase 4: Free dead closures — destruction only.
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
     * go into a separate dead array.
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
            dead[dead_count++] = fn;
        }
    }

    vs->_closure_registry_count = live_count;

    /*
     * Phase 3 — Break internal edges (arithmetic only).
     *
     * For each dead closure, decrement each capture's refcount with a bare
     * decrement and NULL the slot.  Nothing may be freed in this phase.
     */
    for (ssize_t i = 0; i < dead_count; i++) {
        MenaiFunction *fn = dead[i];
        for (ssize_t j = 0; j < fn->ncap; j++) {
            MenaiValue *cap = fn->captures[j];
            if (cap != NULL) {
                cap->ob_refcnt--;
                fn->captures[j] = NULL;
            }
        }
    }

    /*
     * Phase 4 — Free (destruction only).
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
     */
    vs->_gc_in_progress = 1;

    for (ssize_t i = 0; i < dead_count; i++) {
        MenaiFunction *fn = dead[i];
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
