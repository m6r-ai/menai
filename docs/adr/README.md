# Architecture Decision Records (ADRs)

This directory contains ADRs for the Menai project. Each ADR records a significant
architectural or design decision: the context, the decision, the alternatives that
were considered, and the consequences.

## Relationship to other documents

- **AGENTS.md** — contains short summaries of each decision as guardrails for
  contributors. Each summary links to the full ADR here.
- **blueprint.md** — captures the project's purpose and core principles. ADRs
  record specific decisions that follow from those principles.
- **CONTRIBUTING.md** — points contributors here for design context before
  proposing changes.

## How to write an ADR

Use the next available four-digit number. Copy the structure of an existing ADR:

```markdown
# ADR-NNNN: Title

Date: YYYY-MM-DD  
Status: Accepted

## Context

What problem were we solving? What forces were at play?

## Decision

What did we decide?

## Alternatives considered

What else was on the table, and why did we reject it?

## Consequences

### Positive

What benefits does this decision bring?

### Negative

What costs, risks, or constraints does this decision impose?
```

### Status values

- **Proposed** — decision is under discussion, not yet final.
- **Accepted** — decision is final and implemented (or being implemented).
- **Superseded by ADR-XXXX** — replaced by a later decision.
- **Deprecated** — no longer relevant, but not superseded by a specific ADR.

### Principles

- An ADR records **context, alternatives, and reasoning** — the things that are
  not in the code. Do not reproduce implementation details that can be read from
  the source.
- An ADR is written when the decision is **settled**, not while it is still under
  discussion. Use a working document for proposals, then create the ADR once the
  approach is confirmed.
- Once accepted, an ADR is not updated to reflect implementation details. If the
  decision itself changes, write a new ADR that supersedes the old one.

## Index

| Number | Title | Status |
|--------|-------|--------|
| [0001](0001-no-cond-form.md) | No `cond` form | Accepted |
| [0002](0002-symbols-not-strings.md) | Symbols are not strings | Accepted |
| [0003](0003-proper-lists-only.md) | Proper lists only | Accepted |
| [0004](0004-strict-numeric-typing.md) | Strict numeric typing | Accepted |
| [0005](0005-ir-tree-immutability.md) | IR tree immutability — passes return new trees | Accepted |
| [0006](0006-no-process-global-mutable-state-in-c-vm.md) | No process-global mutable state in the C VM | Accepted |
| [0007](0007-dead-code-elimination-always-safe.md) | Dead code elimination is always safe | Accepted |
| [0008](0008-letrec-is-genuine-mutual-recursion.md) | `letrec` reaching the IR builder is always genuine mutual recursion | Accepted |
| [0009](0009-prelude-and-builtin-registry-consistency.md) | Prelude and builtin registry must stay consistent | Accepted |
| [0010](0010-register-based-vm-instead-of-stack-based-vm.md) | Register-based VM instead of stack-based VM | Accepted |
| [0011](0011-s-expression-syntax.md) | S-expression syntax | Accepted |
| [0012](0012-distinct-none-type-for-absence.md) | Distinct `#none` type for absence | Accepted |
| [0013](0013-two-intermediate-representations.md) | Two intermediate representations — IR tree and SSA CFG | Accepted |
| [0014](0014-pool-allocator-in-c-vm.md) | Pool allocator in the C VM | Accepted |
| [0015](0015-desugar-to-small-core.md) | Desugar to a small core before IR | Accepted |
| [0016](0016-reference-counting-with-closure-cycle-collection.md) | Reference counting with closure cycle collection in the C VM | Accepted |
