# ADR-0029: Type-predicate folding is restricted to locally-proven arguments

Date: 2026-09-24  
Status: Accepted

## Context

A type-predicate builtin (`none?`, `integer?`, `string?`, ...) returns `#t`
exactly when its argument's type is the predicate's type. When the argument's
type is proven, the predicate's result is known at compile time and the branch
it feeds can be re-wired to the taken edge, eliminating a runtime check and a
branch. This is the `MenaiCFGPredicateFold` pass, which consumes the per-value
type facts computed by the interprocedural type analysis (ADR-0021, ADR-0027).

The interprocedural analysis is documented as a pure optimisation that "never
changes observable behaviour": a fact it reports must be a sound upper bound on
the value's runtime type, because the guard-insertion pass skips runtime type
checks on the strength of it. A fact that is more precise than reality is a
miscompile, not a missed optimisation.

The analysis does not resolve calls through function-valued parameters. A
function value that is passed as an argument to a call the analysis cannot
resolve may be called with values of any type, but the analysis does not see
those call sites, so it does not degrade the function's parameter facts. A
parameter can therefore be reported with a concrete type even though an
unresolved call site can pass a value of another type. This gap is latent for
guard insertion on the current codebase, but predicate folding consumes the
facts directly: folding a predicate on such a parameter deletes a branch that
must be taken at runtime, which changes behaviour (and can produce a program
that no longer terminates).

Fixing the gap properly requires escape analysis: any function value that is
passed, stored, or returned must have its parameters degraded, because it may be
called with anything. That is a substantially larger change to the core
interprocedural logic, with regression risk for the struct-threading workload
ADR-0021 was written for.

## Decision

The predicate-fold pass folds a predicate only when its argument's type fact is
*locally proven*: derived solely from constants, value constructors, and builtins
whose signature fixes the result type, combined through phi nodes, all within the
function being compiled. A fact derived from a parameter, a free variable, or a
call result is never used, regardless of how precise the interprocedural analysis
reports it to be.

Locally-proven facts do not depend on the interprocedural analysis's precision,
so the fold is sound without escape analysis. The restriction is a deliberate
precision/scope trade-off: predicates on parameters are left unfolded until
escape analysis makes their facts trustworthy.

## Alternatives considered

### Fold on any fact the interprocedural analysis reports

Rejected: unsound. A parameter fact can be too precise when a function value
escapes into an unresolved call, and folding on it deletes a required branch. A
program that recurses through a higher-order prelude function and dispatches on
the element type was miscompiled to a non-terminating program by this approach.

### Add escape analysis first, then fold on any fact

This is the correct long-term shape and was not rejected on the merits, only
deferred: it is a much larger change to the interprocedural analysis, and the
predicate-fold optimisation is useful without it. The locally-proven restriction
is compatible with relaxing to full facts later, because it only narrows which
facts the pass will consume.

### Fold only on constant arguments

Rejected as too narrow: it would not fold the motivating case, where the argument
is the result of a builtin whose signature fixes its type (a bytes read returning
an integer), and it would forfeit the phi-of-locals case.

## Consequences

### Positive

- Predicate folding is sound without depending on the precision of the
  interprocedural analysis, so it cannot miscompile when a function value escapes
  into an unresolved call.
- The motivating case — a `none?` check on the result of a bytes read that is
  known to be an integer — folds.
- The restriction is a pure narrowing of which facts are consumed; it can be
  relaxed to full interprocedural facts once escape analysis exists, without
  changing the pass's structure.

### Negative

- Predicates on parameters are not folded, even when the interprocedural analysis
  proves their type, so the optimisation misses cases that would be sound if the
  analysis were complete.
- The pass carries its own provenance computation (which values are locally
  proven), independent of the fact lattice.
