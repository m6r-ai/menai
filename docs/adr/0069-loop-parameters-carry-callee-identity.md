# ADR-0069: A loop parameter carries callee identity when it cannot be rebound

Date: 2026-11-25  
Status: Accepted

## Context

The IR inliner resolves a call's target by looking a name up in a scope stack
built as the tree is walked. It resolves binding forms and direct lambda
applications, and refuses a name entered with no target, which is how a lambda
parameter shadows an outer binding without resolving to it.

A loop parameter had neither treatment. `MenaiIRLetrecToLoop` runs before the
inliner, so a helper loop's parameters are `MenaiIRLoop` params by the time the
call sites inside the loop body are walked, and `_opt_loop` walked the body with
the scope stack unchanged. The consequences were both a missed optimisation and a
wrong-code bug.

**Missed optimisation.** Every higher-order prelude function is written as a
binding for a self-recursive helper, and the helper takes the function argument as
one of its own parameters:

```menai
(fold-list
 (lambda (f init lst)
   (let ((helper
          (lambda (f acc lst)
            (if ($list-null? lst)
                acc
                (helper f (f acc ($list-first lst)) ($list-rest lst))))))
     (helper f init lst))))
```

`(fold-list integer+ 0 lst)` therefore offers `integer+` only as the *initial
value* of the helper's `f` parameter. The call `(f acc ...)` inside the loop body
resolves through the loop parameter, which resolved to nothing, so the call
survived. The prelude's variadic wrappers were already inlineable; this, not
arity, was what kept them out.

**Wrong code.** Because a loop parameter was not entered into the scope at all, it
did not shadow anything. A call whose function position was a loop parameter found
an *outer* binding of the same name further down the stack and was inlined as that
binding's body. The parameter's own value was ignored:

```menai
(let ((f (lambda (a b) (integer+ a b))))
  (let ((loop (lambda (f n)
                (if (integer=? n 0)
                    (f 1 2)
                    (loop (lambda (a b) (integer* a b)) (integer- n 1))))))
    (loop f 3)))
```

The call `(f 1 2)` runs after `f` has been rebound to the multiplier, so the
program's value is 2. It was compiled to 3, with the addition folded in from the
outer binding. This is the same class of fault ADR-0062 describes: a name-based
lookup that ignores a binding the source puts in front of it.

## Decision

A loop parameter is entered into the scope stack for the loop body, so that it
always shadows an outer binding of the same name. Whether it resolves to a known
lambda is decided by a separate question: whether the parameter can change.

A parameter is **invariant** when every back-edge of the loop passes the
parameter's own name in the parameter's position. The value the init plan produces
is then the value the parameter holds on every iteration, so a call through it
reaches the function the init plan names, and that lambda may be inlined at the
call site. A parameter that any back-edge rebinds is entered with no target.

Two further restrictions apply to the resolved lambda, both of which are
properties of the call site the substitution will create:

- A name the lambda captures from outside the loop must not collide with a loop
  parameter, because the inlined body would resolve it to the loop's value
  instead of the caller's. Nothing in the substitution can repair that, so such a
  lambda is not resolved.
- The existing admission rules are unchanged: the recursion guard, the arity
  rules, the size budget, and the sibling-capture rule are all evaluated against
  the resolved lambda exactly as they are for any other target.

**The dead parameter.** Inlining every call through a higher-order function's loop
parameter makes the parameter itself dead: its only remaining mention is the
back-edge passing it to itself. The init plan that seeded it — the callback's
closure — is then never read, but it was still built, and the lambda still became
a code object. The inliner now drops such a parameter, its init plan, and the
argument in that position from every back-edge, so the loop's arity shrinks
consistently.

Dropping the init plan is an elimination, not motion, which purity makes safe
(ADR-0007) — with one exception. A raise is observable and there are no handlers,
so an init plan that can raise must be evaluated even when nothing reads the
parameter it seeds (ADR-0049). The elimination is therefore restricted to init
plans that carry no computation.

## Alternatives considered

### Add the invariance flow to the CFG instead

`MenaiCFGInterprocTypeAnalysis` already computes a may-set of callees for each
parameter, iterated to a fixed point with the value-level flag (ADR-0058). It runs
after IR inlining, so its facts arrive too late to act on them, and inlining
belongs in the IR because that is the only layer where scope is still lexical.
Moving inlining to the CFG was rejected: the CFG has already dissolved scope into
SSA values, so a substitution there would have to reconstruct it.

Rejected. The IR-level question is narrower than the CFG analysis: it is not which
callees may arrive, but whether this one cannot change, and that is decidable from
the loop alone.

### Resolve a loop parameter without requiring invariance

Rejected as unsound. A parameter that a back-edge rebinds reaches a different
function after the back-edge, so inlining the init plan's lambda would be
inlining a call that can go somewhere else.

### Restrict resolution to parameters whose init plan is a literal lambda

This would cover the shape where a lambda argument is written at the call site and
leave the `(fold-list integer+ ...)` shape alone, since that one's init plan is a
variable. Rejected: the variable case is the more common one, and it resolves
through the same scope stack the rest of the pass already uses.

### Leave the dead parameter in place

Rejected. It is not merely untidy: the closure and its code object are emitted, so
inlining a callback *adds* a dead function to the module. That is a code-size
regression that the standard library's function-coverage guard detects as a
function nothing calls, and it was detected.

## Consequences

### Positive

- A callback reaching a higher-order function's helper loop is inlined whether it
  is written as a literal argument or bound first; the two shapes now compile
  identically. A literal lambda argument previously left an indirect call, a
  frame, and an argument move per element.
- A call through a loop parameter can no longer be resolved to an unrelated outer
  binding of the same name.
- Inlining a callback no longer leaks the closure it makes unreachable.

### Negative

- `_opt_loop` now contributes scope entries, so a loop body is walked in a deeper
  scope than before. Any future pass or check that assumes the loop body is walked
  in the enclosing scope is wrong.
- Invariance is decided per parameter from the loop's own back-edges, so a nested
  loop's back-edges must not be attributed to the enclosing one. A nested
  `MenaiIRLoop`'s body and a nested lambda's body are both excluded from the walk
  for that reason, and the exclusion is a correctness requirement, not a
  convenience.
- The capture restriction is conservative in one direction: a lambda whose free
  name happens to coincide with a loop parameter of the same name is not
  resolved, even where the outer binding is the one it should read. The call is
  left alone rather than miscompiled.
- Dropping a dead parameter removes it from the loop's arity. A pass that caches
  a loop's parameter count across the optimisation pipeline would see it change.
- The use count that decides liveness must be an upper bound that resolves no
  shadowing. A shadow-aware count misses a use made through a nested lambda's
  capture list, which drops a parameter that is still read.

## Related

- [ADR-0032](0032-single-binding-tail-recursive-letrecs-become-loops.md) is why
  the parameter exists by the time the inliner runs: the loop conversion happens
  first, so the inliner sees parameters rather than parameters of a lambda it
  could resolve by name.
- [ADR-0059](0059-inliner-guards-are-intrinsic-to-the-lambda.md) governs which
  resolved lambdas may be inlined; this decision only extends what can be
  resolved, not what is admissible.
- [ADR-0062](0062-name-resolution-respects-lexical-shadowing.md) is the same
  principle for the desugarer and the semantic analyser: a name-based rule must
  first respect the bindings the source puts in front of it.
- [ADR-0049](0049-hoisting-must-not-introduce-a-raise.md) is why the dead
  parameter's init plan may only be dropped when it cannot raise.
- [ADR-0007](0007-dead-code-elimination-always-safe.md) is why dropping it is
  otherwise unconditional.
