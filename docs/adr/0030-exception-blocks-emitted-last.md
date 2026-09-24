# ADR-0030: Exception blocks are laid out after the normal path

Date: 2026-09-24  
Status: Accepted

## Context

A block that ends in a raise is an exception path: it is taken only when the
program is about to abort, and the normal path never executes it. If such blocks
are interleaved with the normal-path blocks, cold instructions sit in the middle
of the hot region, which costs instruction-cache locality on the path that
actually runs.

The layout of emitted code is not the order of the CFG's block list. The backend
derives its layout from the control-flow graph. Reordering the block list alone
therefore does not move anything in the emitted code; the partition has to be
applied to the layout order itself.

## Decision

Blocks that end in a raise are laid out after all normal-path blocks in a
function. The ordering is stable: normal-path blocks keep their relative order,
and exception blocks keep theirs. The entry block stays first and is never
treated as an exception block, even when it is itself terminated by a raise.

Because layout is derived from the graph rather than the block list, the
partition is applied to the layout order explicitly rather than by reordering
the block list and hoping the backend follows it.

## Alternatives considered

### Reorder the block list and rely on the backend to follow it

Rejected: the backend derives layout from the graph, so a block-list reordering
does not affect emitted code. Worse, the natural graph traversal (a depth-first
post-order and its reverse) places a raise block — which is a leaf — *early*,
not late, regardless of where it sits in the block list. The partition cannot be
made to fall out of the block list order; it must be applied to the layout.

### Have the backend decide the partition from the terminator type

Rejected: it splits the intent across the CFG and the backend. The decision of
which blocks are exception blocks belongs with the rest of the CFG ordering,
and the backend's traversal should stay a pure layout concern.

### Lay blocks out in block-list order instead of by graph traversal

Rejected: the graph traversal is what makes fall-through elision and loop
back-edge handling work. Changing it would alter the layout of every function,
not just those containing exception blocks.

## Consequences

### Positive

- Cold exception instructions are laid out after the hot normal path, improving
  instruction-cache locality on the path that runs.
- The change is purely positional: no instruction is added or removed on the
  normal path, and behaviour is unchanged.
- The ordering is stable and deterministic, so layout is reproducible.

### Negative

- An exception edge that previously fell through may now need an explicit jump
  to reach its block. The cost lands on the exception edge, never the normal
  path, which is the intended trade.
- Any ordering decision expressed in the CFG now influences layout, so the
  exception-last ordering must remain the final word on block order.
