# ADR-0005: IR tree immutability — passes return new trees

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

Compiler optimisation passes transform an IR (intermediate representation) tree.
If passes mutate their input tree in place, a bug in one pass can corrupt the tree
in ways that only manifest later in an unrelated pass, making bugs hard to isolate.
It also makes passes non-composable: the same pass cannot be safely applied to the
same tree multiple times, and passes cannot be reordered without risk.

## Decision

No IR optimisation pass may mutate its input tree in place. Each pass receives an
IR tree and returns a new one, along with a boolean indicating whether anything
changed. The pass manager uses that flag to drive the fixed-point loop.

## Alternatives considered

### In-place mutation with copy-on-need

This would avoid the overhead of copying but would require each pass to know when
it needs to copy, adding complexity and creating opportunities for bugs. The
immutability guarantee would not be statically enforceable.

### Functional-persistent data structures

This would reduce copying overhead by sharing unchanged subtrees. The current
approach already achieves this in practice — unchanged subtrees are shared and
only modified subtrees are rebuilt. A formal persistent data structure library
was not adopted to avoid adding external dependencies (Menai has zero external
runtime dependencies).

## Consequences

### Positive

- Passes are composable: the same pass can be applied multiple times, and passes
  can be reordered without risk of corruption.
- Bugs are easier to isolate: a pass receives a known-good input and produces a
  new output. If the output is wrong, the bug is in that pass.
- The pass manager's fixed-point loop is straightforward: run passes until no
  pass reports a change.

### Negative

- There is some copying overhead, though unchanged subtrees are shared, so the
  overhead is proportional to the size of the modified portion, not the whole
  tree.
- This invariant spans all IR optimisation passes, making it easy to violate
  accidentally when writing a new pass.
