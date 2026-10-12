# ADR-0049: Hoisting must not introduce a raise on a path that did not have one

Date: 2026-10-12  
Status: Accepted

## Context

Menai is pure: it has no side effects, no mutation, and no state. ADR-0007
records the consequence that an expression whose result is unused can be
discarded unconditionally, because discarding it cannot change observable
behaviour. Optimisation passes rely on this without checking for side effects.

Purity also makes an expression's *result* a pure function of its operands, so
two evaluations of the same expression with the same operands produce the same
result. This is what makes it tempting to conclude that an expression may be
*moved* freely: hoist it out of a loop, or above a branch, and the value it
produces is unchanged.

That conclusion is wrong, because Menai expressions can also **raise**. A raise
is not a side effect in the purity sense, but it is observable: the language has
no exception handlers, so a raise terminates the program. The observable
behaviour of a program therefore includes *whether it raises at all*, and moving
an expression can change that. Hoisting a potentially-raising expression above
the branch that guarded it makes it execute on paths where it did not execute
before, so a program that previously completed normally can be made to raise.

This is not a hypothetical. `MenaiCFGLICM` hoisted loop-invariant instructions
from every block in the loop region into a preamble that executes once on
function entry, justified by the claim that "any instruction whose operands are
all loop-invariant can be hoisted unconditionally" because Menai is pure. A
loop-invariant division by a zero-valued free variable, guarded by a condition
that was never true, was hoisted into the preamble and executed unconditionally,
turning a program that returned a value into one that raised division by zero.

The hazard is not specific to loop-invariant code motion. It applies to any pass
that moves an expression to a point where it executes on more paths than before,
including one that merges two computations of the same expression reached on
different paths.

## Decision

An optimisation pass may not move a potentially-raising expression to a point
where it executes on a path that did not execute it before.

Concretely:

- An expression that **cannot raise** may be moved freely, subject to the
  pass's other constraints. Purity already licenses this.
- An expression that **can raise** may be moved only to a point where it is
  guaranteed to execute on every path that reached any of its original
  execution points. Hoisting it above a branch, or merging it across paths, is
  permitted only when it already executed on all of them.

The classification of which operations can raise is builtin metadata and lives
in the builtin registry alongside the opcode and arity, so it cannot drift from
the set of builtins it describes (see ADR-0026). A call, `apply`, and `error`
can raise unconditionally and are treated as raising.

For loop-invariant code motion specifically, the condition is that the
instruction's block must dominate every back-edge block of the loop: such a
block executes on every iteration, so hoisting a raising instruction out of it
cannot introduce a raise on an iteration that did not previously execute it.

## Alternatives considered

### Treat raising like a side effect and forbid moving any raising expression

Sound, and simpler than a dominance check. Rejected because it forfeits the
legitimate hoisting of a raising expression that executes on every iteration,
which is both common and safe. The cost is a real performance regression for no
correctness benefit.

### Reason only from purity, as LICM did

The status quo. Rejected: it is unsound, as the reproduced miscompile shows.
Purity constrains *results*, not *whether a raise occurs*.

### Track value ranges so more expressions can be proven non-raising

For example, proving a shift amount non-negative would make
`integer-bit-shift-left` total. This is a strictly stronger analysis and is not
foreclosed, but it is a separate capability. The boolean classification is
correct on its own and is the precondition for any such refinement.

## Consequences

### Positive

- Optimisation passes that move expressions have a single, checkable soundness
  condition, rather than each reasoning informally about purity.
- The classification is builtin metadata, declared once next to each builtin, so
  a newly added builtin cannot silently be misclassified as non-raising.

### Negative

- Passes that move expressions must consult the classification, so a builtin
  whose raising behaviour changes must have its registry entry updated.
- The condition is conservative: an expression that can raise only on argument
  values the compiler could prove impossible is still treated as raising. This
  is a missed optimisation, not a miscompile.

## Related

- [ADR-0007](0007-dead-code-elimination-always-safe.md) records that purity
  makes *elimination* safe. This ADR records that purity does not make *motion*
  safe, which is the distinction the LICM bug turned on.
- [ADR-0026](0026-single-builtin-table.md) establishes the builtin registry as
  the single source of truth for builtin metadata; the raising classification
  extends that table.
