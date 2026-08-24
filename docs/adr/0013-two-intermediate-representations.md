# ADR-0013: Two intermediate representations — IR tree and SSA CFG

| Metadata |
|---|
| Date | 2026-08-24 |
| Status | Accepted |

## Context

Menai's compiler pipeline lowers source code through two distinct intermediate
representations before reaching bytecode: an IR tree (nested expressions, close
to the source language) and an SSA CFG (flat blocks, phi nodes, virtual
registers).

The IR tree came first. As the compiler matured, control flow optimisations
were needed that the tree representation could not express effectively — branch
constant propagation, phi chain collapsing, type propagation across control
flow paths. This required a lower-level representation with explicit basic
blocks and control flow edges.

The question was whether to replace the IR tree with the CFG or keep both.

## Decision

Keep both intermediate representations. The IR tree handles optimisations that
benefit from the nested, expression-oriented structure of the source language.
The SSA CFG handles optimisations that require explicit control flow.

Each lowering loses contextual information. The IR tree preserves the
structure of the source language — nested `let` bindings, `lambda` bodies,
`if` expressions — which makes some optimisations more effective at that level.
Lowering to CFG flattens this structure into basic blocks, which is necessary
for control flow analysis but loses the nesting context.

The CFG was added because the IR tree was not sufficient to handle control flow
optimisations. Rather than discarding the IR tree, both were kept so that each
representation serves the optimisations that work best at its level of
abstraction.

## Alternatives considered

### Replace the IR tree with the CFG

This would eliminate one representation and the lowering step between them.
However, the IR tree's nested structure carries contextual information that the
flattened CFG loses. Optimisations that rely on that structure — such as the IR
inliner, which walks nested lambda bodies and let bindings — would be less
effective or more complex to implement on a flat CFG. The two representations
serve different optimisation needs, and discarding either one would lose
optimisation opportunities.

### Go directly from AST to CFG

This would skip the IR tree entirely, lowering the desugared AST straight to
SSA CFG. But the AST is still close to the source syntax and carries source
location metadata, making it unsuitable for the kind of structural
optimisations the IR tree performs. The IR tree is a deliberately simplified
core language (literals, variables, `if`, `let`, `lambda`, calls) that is a
better fit for optimisation than the full AST. Introducing the CFG directly
from the AST would conflate two different levels of abstraction.

### Go directly from AST to bytecode (no intermediate representations)

This was never seriously considered. A multi-stage optimising compiler needs
intermediate representations that are amenable to analysis and transformation.
Lowering directly to bytecode would leave no room for optimisation passes.

## Consequences

### Positive

- Each representation serves the optimisations that work best at its level:
  the IR tree for structure-aware optimisations (inlining, dead binding
  elimination), the CFG for control flow optimisations (branch constant
  propagation, phi chain collapsing, type propagation).
- The lowering from IR to CFG is a one-way transformation: the CFG does not
  need to preserve all the IR tree's contextual information, only what is
  needed for control flow analysis.
- Optimisation passes at each level can be developed and tested independently.

### Negative

- There are two intermediate representations to maintain, each with their own
  data structures, visitors, and optimisation pass infrastructure.
- The lowering from IR tree to CFG is an additional pipeline stage with its
  own complexity.
- The boundary between what belongs in IR optimisation vs CFG optimisation
  requires judgement and could shift over time.
