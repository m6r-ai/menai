# ADR-0024: Hash primitives are native VM operations

Date: 2026-06-15  
Status: Accepted

## Context

Menai needs cryptographic hashing of `bytes` values: SHA2-256, SHA2-512,
SHA2-512/256 (FIPS Pub 180-4) and SHA3-256 (FIPS Pub 202). Each is exposed as a
single-argument operation taking a `bytes` value and returning the raw digest as
`bytes`.

Menai has two categories of builtin: opcode-backed operations implemented
natively in the VM, and prelude-only functions implemented as Menai lambdas
(ADR-0009). The question is which category hashing belongs to.

This is an architectural question, not merely a question of where code is
placed. It determines whether hashing is a primitive of the language — present
in every binding by construction — or a library routine written in the language
itself.

## Decision

Hashing is a primitive. The four operations are opcode-backed VM operations,
implemented natively in the VM core alongside the other primitive type
operations.

The criterion is that hashing is a performance-critical, fixed-spec,
integer-heavy kernel. It is a word-at-a-time bit-manipulation algorithm whose
cost is dominated by the inner loop, and its behaviour is pinned by a published
standard rather than by anything specific to Menai. Such kernels belong in the
VM core.

## Alternatives considered

### Implement hashing as pure-Menai functions in the prelude

Implement the four algorithms as Menai lambdas in `prelude.menai`.

Rejected because hashing is a hot, integer-heavy kernel. Running the inner loop
through the language's own arithmetic and recursion would be dramatically slower
than native code, and it would place a large performance-critical routine in the
prelude where the language's optimiser must carry it. Hashing is a primitive
operation, and primitives are implemented natively.

## Consequences

### Positive

- Hashing runs at native speed, appropriate for a primitive operation.
- The operations are available to every program as ordinary primitives, with no
  prelude cost.

### Negative

- Hashing is native code in the VM core, so each binding must implement it
  against the published standard rather than inheriting it from the language.
  The standard test vectors (FIPS 180-4 / FIPS 202) are the specification that
  each binding's implementation must satisfy.
