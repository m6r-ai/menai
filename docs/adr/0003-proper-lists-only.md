# ADR-0003: Proper lists only

Date: 2026-08-24  
Status: Superseded by ADR-0017 (2026-09-02)

The surface-language decision (no cons cells, no improper lists, proper lists
only) remains in force. The internal-representation decision — that list
representation is "an implementation detail of each binding" — is superseded.
ADR-0017 specifies cons cells as the internal representation for lists.

## Context

Lisp-family languages traditionally support cons cells — a pair structure where
the second element can be any value, not necessarily a list. This allows "improper
lists" like `(1 . 2)` where the tail is not a list. Cons cells are fundamental to
Lisp's data representation and enable flexible pattern matching at the cons-cell
level.

However, cons cells and improper lists add significant complexity: they require
special-case handling in every operation that processes lists, they complicate
the type system (is `(1 . 2)` a list?), and they create ambiguity in equality and
printing. In Menai, which does not have pattern-matched list destructuring at the
cons-cell level, the benefit of cons cells is minimal.

## Decision

There are no cons cells and no improper lists. All lists are proper lists —
finite sequences with a well-defined length and no improper tail. List
construction uses `list`, `list-prepend`, and `list-append`, all of which produce
proper lists. The internal representation of lists is an implementation detail of
each binding and may differ from a traditional cons-cell linked list.

## Alternatives considered

### Full cons-cell support (Scheme-style)

This would provide maximum Lisp compatibility and enable cons-cell-level pattern
matching. But Menai does not have cons-cell-level destructuring, so the primary
benefit is absent. The complexity cost remains: every list-processing operation,
type check, equality function, and printer would need to handle improper lists.

### Linked lists with explicit nil

This would use a traditional Lisp linked-list representation (cons cells where the
tail is always a list or nil). This is closer to the decision but still requires
cons cells as the building block. The chosen approach (implementation-defined
representation) is simpler and more efficient for the operations Menai actually
supports.

## Consequences

### Positive

- All lists are proper lists. Any operation that processes lists can assume a
  well-formed list with no improper tail.
- List operations are simpler to implement and reason about.
- Pattern matching on lists uses list patterns, not cons-cell destructuring.
- Each binding is free to choose its own internal representation for lists.

### Negative

- There is no `cons` operation. List construction uses `list`, `list-prepend`,
  and `list-append`, which may be less familiar to programmers from other Lisp
  dialects.
