# ADR-0071: A phi chain is collapsed only through a block that is bypassed, and never through a loop-carried phi

Date: 2026-11-25
Status: Accepted

## Context

The CFG is in SSA form, and a phi's incoming entries are positional against the
predecessor blocks of the block it sits in: the entry for a predecessor names the value the
merge selects on the edge from that predecessor. Two things in the CFG therefore refer to a
loop header's phi by identity rather than through the graph: a back edge carries its
loop-carried updates in a list of parameter values on the terminator, and VCode lowering
derives the back edge's copies from that list instead of reading the target block's phis.
The slot allocator likewise gives each value in that list a fixed slot.

An optimisation that rewrites a phi's incoming list or deletes a phi is sound only while two
properties hold. Every entry still names a block that is a predecessor of the block holding
the phi, or the graph the merge describes is not the graph that executes. And every value a
terminator names still has a definition, or the code that derives copies from that
terminator defines something nothing reads and leaves the merge without an input.

Flattening a chain of phis is exactly such an optimisation: the entries of an intermediate
phi are substituted into a consuming phi so that the intermediate merge can be deleted. That
is valid when the intermediate block is bypassed, because only then do the intermediate
phi's predecessors become predecessors of the consuming block. Inlining a nested loop whose
initial value is the enclosing loop's parameter produces the shape where this is easy to get
wrong: the intermediate block is a loop header holding the enclosing loop's parameter phi,
which the enclosing back edge names, alongside the loop's accumulator phi, which is live
elsewhere and therefore cannot be removed with it.

## Decision

A phi is collapsed out of a chain only when both conditions hold.

The intermediate block is a pass-through — phi instructions and an unconditional jump — and
every phi in it is removed by the same round, so the block is bypassed and its predecessors
become the predecessors of the consuming block. A block that keeps a phi is still on every
path through the program, and no amount of later simplification removes it.

The intermediate phi is not a loop-carried variable. A phi named by any back edge's parameter
list is never a candidate for collapse or removal, because the terminator is the only place
that knows how to update it, and the loop's parameter is not a value that can be merged into
a different loop's merge: two nested loops each own their carried variables.

Trivial-phi elimination, which replaces a phi whose incoming values are all the same value,
is subject to the second rule for the same reason and does not need the first, since it
substitutes a value rather than rewriting a predecessor list.

## Alternatives considered

**Rely on the block becoming empty once the round is finished.** A pass that bypasses empty
blocks runs after this one, so the intermediate block would be cleaned up in time. Rejected:
a block that keeps a live phi is never emptied, and the consuming phi has already been
rewritten by then. The rewritten entries are wrong for the graph that exists, whether or not
another pass later removes something else.

**Rewrite the back edge to name the surviving phi.** The value flows correctly, so the
enclosing loop's variable could be renamed to the inner loop's phi. Rejected: the two
carried variables are updated on different back edges, and one slot cannot be live across
both without being written by both. Each loop owns its own carried variables.

**Keep the phi and copy into the intermediate block.** Rejected: that is precisely what the
phi is, so the transformation would not be an optimisation.

## Consequences

### Positive

Shapes that inlining naturally produces — a nested loop whose initial value is the enclosing
loop's parameter — compile to code that passes validation. A value a terminator names always
has a phi defining it, so the copies derived from the terminator and the merge at its target
cannot disagree.

### Negative

A chain whose intermediate block keeps another live phi stays as it is, so a few redundant
merges survive in functions with nested loops. The pass's conditions are structural rather
than semantic, so they need no analysis beyond the block's contents and the terminators.
