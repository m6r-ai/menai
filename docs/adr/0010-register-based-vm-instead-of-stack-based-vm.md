# ADR-0010: Register-based VM instead of stack-based VM

Date: 2026-08-24  
Status: Accepted

## Context

Menai's VM executes bytecode produced by the compiler pipeline. The original
implementation used a stack-based VM, where operands are pushed onto a stack and
operations consume from and produce to the stack.

Stack-based VMs are simple to implement and have compact instruction encodings.
However, as the compiler pipeline matured and optimisation passes became more
sophisticated, the stack-based model became a bottleneck. Complex control flow
operations were difficult to implement on a stack machine — the stack discipline
constrained how values could flow through branches, loops, and phi nodes.
Optimisation passes that wanted to reorder, eliminate, or fuse operations were
fighting the stack's strict ordering requirements.

## Decision

The VM uses a register-based model. The compiler pipeline works in terms of
virtual registers, which are then mapped to the actual VM registers in the C VM.

## Alternatives considered

### Stack-based VM (the original implementation)

The stack-based VM was the first implementation. It was simpler to get working
initially, but complex control flow operations were difficult to implement and
optimise. The strict stack ordering made it hard to reorder or eliminate
operations — every value had to flow through the stack in push/consume order,
which constrained the optimisation passes.

### Hybrid stack/register model

This would keep a stack for simple operand flow but introduce registers for
complex control flow. This would add complexity in both the VM implementation
and the compiler, as code generation would need to decide when to use the stack
and when to use registers. The pure register model is simpler to reason about.

## Consequences

### Positive

- The compiler pipeline operates on virtual registers throughout, which are
  mapped to actual VM registers. This gives optimisation passes freedom to
  reorder, eliminate, and fuse operations without stack discipline constraints.
- Complex control flow (branches, loops, phi nodes) is straightforward to
  represent because values live in named registers rather than being implicitly
  positioned on a stack.

### Negative

- The instruction encoding is larger than a stack-based format (instructions
  carry register operands), though this is offset by fewer instructions being
  needed for the same computation.
- The VM implementation is more complex than a stack machine (register
  management, instruction decoding), though this complexity is concentrated in
  the VM rather than spread across every optimisation pass.
