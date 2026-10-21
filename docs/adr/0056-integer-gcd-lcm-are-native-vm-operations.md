# ADR-0056: Integer gcd and lcm are native VM operations

Date: 2026-10-21  
Status: Accepted

## Context

Menai has no exact rational type. Adding one is a large commitment: ADR-0004
requires every numeric type to carry its own complete set of type-specific
operators, so a `rational` type would arrive with `rational+`, `rational*`,
`rational->string`, and so on — a new value representation in the C VM and a new
opcode family.

Whatever form rational support eventually takes, one operation is unavoidable.
A rational is kept in lowest terms with a positive denominator, and that
normalisation has to happen on every construction and every arithmetic result.
Without a greatest common divisor the invariant cannot be maintained, and
without the invariant structural equality is wrong (`1/2` and `2/4` compare
unequal), `->string` emits unreduced output, and numerator and denominator grow
without bound on every addition.

So `integer-gcd` is not a convenience beside rational support; it is a
prerequisite for it. `integer-lcm` is the weaker case: addition needs only
`a/b + c/d = (ad + cb)/(bd)`, and `lcm` merely keeps the intermediate smaller.
It is included because the pair is what a caller reaches for, and because the
two together form a testable identity, `gcd(a, b) * lcm(a, b) == |a * b|`.

The question this ADR settles is which category of builtin these belong to.
ADR-0026 defines two: opcode-backed operations implemented natively in the VM,
and prelude-only functions implemented as Menai lambdas.

## Decision

`integer-gcd` and `integer-lcm` are opcode-backed VM operations.

Both are fixed 2-arity and both return a non-negative `integer` regardless of
the signs of their arguments. `(integer-gcd 0 0)` is `0`, and `(integer-lcm 0 n)`
is `0` for every `n`.

The non-negative result is part of the contract, not an implementation detail.
A gcd that took the sign of its arguments would break the invariant it exists to
serve: a divisor is a positive quantity, and a caller reducing a fraction must
not have to correct the sign afterwards.

They are not variadic, unlike `integer-min` and `integer-max`. A variadic gcd
raises the question of what `(integer-gcd)` means — `0` or `1` — and forces every
caller to reason about a case that has no use. A fold over three or more values
is written by the caller.

## Alternatives considered

### Implement both entirely in the prelude

`gcd` is six lines of Menai:

```menai
(integer-gcd
 (lambda (a b)
   (let ((loop (lambda (x y)
                 (if (integer=? y 0)
                     (integer-abs x)
                     (loop y (integer% x y))))))
     (loop (integer-abs a) (integer-abs b)))))
```

Rejected on the ADR-0024 criterion. `gcd` is a fixed-specification,
integer-heavy kernel: the loop is dominated by `integer%`, whose cost for
arbitrary-precision operands is proportional to the operand size, and a Menai
loop pays interpreter dispatch on every iteration on top of that. It is the
operation most likely to be called in a tight loop once rationals exist, since
every arithmetic result reduces through it.

This is the same reasoning that made CRC-32 and the hash functions primitives
rather than prelude lambdas.

### Add `integer-gcd` only, and leave `lcm` to the caller

Defensible on YAGNI grounds, since `lcm` is derivable in one line from `gcd`.
Rejected because the pair is what makes the relationship testable: with both
present, `gcd(a, b) * lcm(a, b) == |a * b|` is a property the test suite can
assert over arbitrary inputs, and that property is what catches a sign-handling
error in either operation. An `lcm` written by each caller would be written
slightly differently by each caller, and the identity would not be checked
anywhere.

### Name them `integer-gcd` / `integer-lcm` versus a `math` capability module

A `math` capability module (ADR-0041) could hold `gcd`, `lcm`, and later
statistics and linear algebra. Rejected for now because `gcd` and `lcm` are
operations on a single builtin type, not a capability composed of parts that
only make sense together. They belong with the other `integer-` operations for
the same reason `integer%` does. A `math` module may still be the right home for
higher-level work that is not a primitive.

## Consequences

### Positive

- Rational support has the operation it depends on, at native speed, whenever it
  is built.
- The `gcd`/`lcm` identity is available as a testable contract.
- Both operations are available to every program with no prelude cost.

### Negative

- Two more opcodes in the VM core, so each future binding must implement them
  against the published semantics rather than inheriting them from the language.
  The semantics are the standard ones, and the sign convention above is the part
  a binding could get wrong.
- The small-integer fast path must handle `LONG_MIN`, whose magnitude is not
  representable in a `long`. The implementation routes that operand to the
  arbitrary-precision path rather than negating it; a binding that negates it in
  place would produce a wrong result for exactly one input in 2^63.
- `lcm` is added before any caller exists in the repository. It is justified by
  the identity above and by the expected rational work, not by a present use.
