# ADR-0034: The compiler is expressible in Menai

Date: 2026-09-28  
Status: Accepted

## Context

Menai's compiler is currently written in Python. The long-term goal is that
every part of it — the front end, the analyses, the optimisations, the code
generation — could be written in Menai itself.

This is not a porting exercise for its own sake. Menai is pure: it has no
mutation, no I/O, and no side effects. A compiler written in Menai would be a
compiler that can be reasoned about, tested, and transformed by the same tools
that consume the language. It would also mean an AI agent could inspect and
modify the compiler using Menai's own machinery.

The goal imposes a standard on the Python implementation, because a component
that cannot be expressed as a pure function over values can never be ported.
The standard has two parts:

- **Models are values.** A data structure passed between phases must be
  immutable, so that a phase cannot alter its input.
- **Transformations are pure functions.** A phase takes a value and returns a
  new value. It must not mutate its input, and it must not depend on hidden
  state that survives between calls.

ADR-0033 applied this standard to the CFG. This ADR states it for the whole
compiler and records where each layer currently stands, so the remaining work
is visible rather than rediscovered.

## Decision

Every compiler phase is a pure function over immutable values. This is the
standard the whole compiler holds itself to, not a CFG-specific rule.

Conformance is not uniform today. The current state, verified by inspection and
by running each phase against a structural snapshot of its input:

| Layer | Model | Transformations |
|-------|-------|-----------------|
| AST | frozen | pure |
| IR | mutable | pure |
| CFG | frozen | pure |
| VCode | mostly mutable (only the register type is frozen) | pure |
| Bytecode | mutable | pure |
| Builders (IR, CFG, VCode, bytecode) | n/a | pure |
| Slot allocator | n/a | pure |
| Lexer | n/a | **stateful** |
| Parser | n/a | **stateful** |

Two things follow from this table.

First, the property that actually matters for Menai-expressibility — that
transformations are pure functions of their input — already holds everywhere
except the lexer and the parser. Every pass and every builder takes a value and
returns a result without mutating it.

Second, immutability is enforced by the model in only two of the five
representations. The IR, VCode, and bytecode models are immutable by convention
rather than by construction: nothing prevents a future pass from mutating them.
The AST and CFG show the intended pattern.

The remaining work is therefore:

- Freeze the IR, VCode, and bytecode models, following the AST and CFG pattern.
- Make the lexer and parser pure. Both are classic stateful designs: the lexer
  holds a cursor and a token accumulator as instance state, and the parser
  holds a position cursor. These are redesigns, not mechanical changes.

## Relationship to other ADRs

ADR-0005 requires that no IR pass mutate its input tree, and ADR-0033 makes the
CFG an immutable value. Both are instances of this standard, adopted before the
general principle was articulated. This ADR does not supersede them; it is the
general statement they are instances of.

ADR-0005's rule is a rule for passes, and it holds: the IR passes return new
trees. It is not enforced by the IR model, which remains mutable. Freezing the
IR model would make the rule structural, as it already is for the AST and CFG.

## Alternatives considered

### Treat immutability as a CFG-specific fix

The CFG refactor (ADR-0033) could be regarded as a one-off, with no general
standard. This would leave the IR, VCode, and bytecode models mutable by
convention, and would leave the asymmetry between layers unexplained: a reader
would have no way to tell whether the AST being frozen and the IR not was a
decision or an accident. Rejected.

### Require purity only of the optimisation passes

The passes are where mutation caused visible bugs (ADR-0033). Restricting the
standard to them would leave the builders and the front end unconstrained, and
those are the components that must be ported for the compiler to be expressible
in Menai. Rejected.

### Enforce the standard with a type system rather than convention

Python cannot express "this function does not mutate its argument". The
practical enforcement is frozen dataclasses, which make mutation a runtime
error, plus tests that snapshot a phase's input and assert it is unchanged.
This is what the AST and CFG layers do, and what the remaining layers should
adopt. A stricter guarantee would require a different implementation language,
which is the very thing this ADR is working towards.

## Consequences

### Positive

- The standard is explicit, so a new phase's author knows what is required and
  a reviewer knows what to check.
- The existing asymmetries between layers become legible: the table states
  which layers conform and which do not.
- The remaining work is enumerated, so it can be planned and tracked.
- The property that matters most — pure transformations — is already met, so
  the remaining work is narrower than the goal might suggest.

### Negative

- Freezing the IR, VCode, and bytecode models touches every construction site
  in those layers, as the CFG change did.
- Making the lexer and parser pure is a genuine redesign of stateful algorithms,
  and is the largest remaining piece of work.
- The standard is enforced by convention and tests, not by the type system, so
  a new phase can violate it without the compiler complaining.
