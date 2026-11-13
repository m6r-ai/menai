# AGENTS.md - Menai compiler core

Guardrails for `src/menai/`: AST, desugaring, IR, CFG, VCode, bytecode and the type
analyses. Each pass's module docstring describes what it does and where it sits; the rules
below span files and are easy to violate by accident.

The root [`AGENTS.md`](../../AGENTS.md) covers how to work in the repository, and
[`vm/AGENTS.md`](vm/AGENTS.md) covers the C VM.

## Architectural invariants

### The IR tree is immutable — passes return new trees

No IR pass may mutate its input. Each returns a new tree plus a changed flag, which the pass
manager uses to drive its fixed-point loop. A mutating pass corrupts the tree in ways that
only surface later in an unrelated pass.
See [ADR-0005](../../docs/adr/0005-ir-tree-immutability.md).

### The inliner does not inline recursive or mutually-recursive calls

It refuses a lambda whose `binding_name` appears in its own `sibling_free_vars`, because
inlining that body reintroduces the call and the fixed-point loop cannot terminate.

The guard must be intrinsic to the lambda, never keyed on the walk position. A call site is
re-walked with the call site's `$letrec` group names after a body is inlined, so a
position-keyed guard misses a self-call in a freshly inlined body and inlines without bound.
Mutual recursion is covered by the sibling-capture rule; a direct lambda application
(`((lambda ...) arg)`) is exempt.
See [ADR-0059](../../docs/adr/0059-inliner-guards-are-intrinsic-to-the-lambda.md), which
supersedes [ADR-0031](../../docs/adr/0031-inliner-does-not-inline-recursive-calls.md).

### The inliner recomputes capture lists from the rewritten body

Closure conversion binds a lambda's names *only* from its capture lists, so the inliner
rebuilds `sibling_free_vars` and `outer_free_vars` from the optimised body rather than
copying them; a name the body reads but the lists omit is unresolvable. The sibling/outer
split follows the `$letrec` group, so the walk threads group names down to binding values,
and a lambda body resets them to empty because it is not inside its own group.

`_free_vars` is load-bearing for this and must stay exact: a name wrongly reported becomes a
spurious capture, a name missed becomes an unresolvable one. A builtin call's function
position names an opcode, not a binding, and must not be reported. Do not revisit an
identity-based capture rule without first addressing closure conversion.
See [ADR-0059](../../docs/adr/0059-inliner-guards-are-intrinsic-to-the-lambda.md).

### Single-binding tail-recursive groups become loops before inlining

`MenaiIRLetrecToLoop` converts a `$letrec` with a single self-referencing binding whose body
is a single tail call into a `MenaiIRLoop`, replacing the self-calls with `MenaiIRRecur`
back-edges. It runs before the inliner so calls inside the loop body can be inlined. Only
single-binding groups are converted: a self-recursive function sharing a group with other
bindings is not, and moving it into its own group makes it eligible.
See [ADR-0032](../../docs/adr/0032-single-binding-tail-recursive-letrecs-become-loops.md).

### CFG passes declare their scope via their base class

A pass is handed the root `MenaiCFGFunction`; there is no module object. The module is the
root plus every function reachable through `MenaiCFGMakeClosureInstr`, enumerated by
`collect_functions`. Scope is declared by base class — `MenaiCFGPerFunctionPass` (implement
`_optimize_function`; traversal and write-back are inherited) or `MenaiCFGWholeProgramPass`
(implement `_optimize_module`; the pass owns its traversal). Do not write one against the
other's contract.
See [ADR-0020](../../docs/adr/0020-cfg-pass-scope-contracts.md).

### Type facts distinguish "no information" from "conflicting information"

The interprocedural analysis' lattice is BOTTOM (no information), a known kind, and ANY
(conflicting kinds), and the three must stay distinct. BOTTOM is the fixed-point identity, so
it is not "unknown": a call site with an unknown argument type must degrade the parameter it
feeds, not be dropped by the join. Conflating them lets a value of one type be reported as
proven for a parameter an unknown value can also reach — a miscompile, not a missed
optimisation. The analysis is a pure optimisation; where a type cannot be proven the existing
runtime path is used.
See [ADR-0027](../../docs/adr/0027-recursion-cycle-parameter-grounding.md).

### Function provenance must follow containers

A function value that reaches a call by a route the resolution does not follow contributes no
call site, so the callee's parameter is never degraded and the reported type can be more
precise than reality — again a miscompile, because the struct field rewrite and guard
insertion both consume the fact.

Every builtin that moves a value into or out of a container must be in `_CONTAINER_FLOW_OPS`;
a missing entry lets a function that does escape appear not to. A function that can reach an
unresolvable call site has its parameters left unconstrained, including one returned to the
host. Callee resolution tracks a parameter as the union of the functions passed into it, and
that union is a *may*-set carrying an "unknown" flag: a phi of a known closure and an unnamed
value denotes the closure *and possibly something else*. The flag propagates through phis and
containers exactly as the known set does, and a call through an unknown value returns ANY.
See [ADR-0035](../../docs/adr/0035-function-provenance-must-follow-containers.md) and
[ADR-0058](../../docs/adr/0058-parameter-callee-sets.md).

### Predicate folding may consume any fact the analysis reports

`MenaiCFGPredicateFold` folds a type predicate when the analysis proves its argument's type,
including facts from parameters, free variables and call results. That is sound because the
parameters of every escaping function are left unconstrained.
See [ADR-0036](../../docs/adr/0036-predicate-fold-may-consume-interprocedural-facts.md).

### The type-signature table is the builtins' operand and result contract

`BUILTIN_TYPE_SIGNATURES` is the only place the compiler learns a builtin's argument and
result types, and both are consumed as proven facts. Guard insertion emits a guard only for an
argument whose declared type is specific, so an operand an opcode reads as a given type must
be declared as that type — the guard the declaration buys *is* the check. The result type
becomes the call's proven fact, which predicate folding and guard insertion both trust.

Declaring `any` is safe and forfeits optimisation; declaring a specific type that is wrong is a
miscompile. Every declared result type is checked against the type the builtin actually
produces, so drift fails a test.
See [ADR-0061](../../docs/adr/0061-type-signature-table-is-the-builtin-contract.md).

### Menai is pure — dead code elimination is always safe

Any expression whose result is unused can be discarded unconditionally, and passes may rely
on that without checking for side effects.
See [ADR-0007](../../docs/adr/0007-dead-code-elimination-always-safe.md).

### Hoisting must not introduce a raise on a path that did not have one

Purity makes *elimination* safe, not *motion*: Menai expressions can raise, a raise is
observable (there are no handlers, so it terminates the program), and moving an expression to
a point that executes on more paths can make a program raise where it previously completed.

A potentially-raising expression may move only to a point guaranteed to execute on every path
that reached any of its original execution points; one that cannot raise may move freely.
Which builtins can raise is recorded in the registry beside the opcode and arity so it cannot
drift; a call, `apply` and `error` raise unconditionally. For loop-invariant code motion the
condition is that the instruction's block dominates every back-edge block of the loop.
See [ADR-0049](../../docs/adr/0049-hoisting-must-not-introduce-a-raise.md).

### Reordering a block must respect guards and closure patches

Two constraints are not expressible as data edges: a guard must precede every other reader of
the value it guards (the unchecked opcode would interpret the payload as the guarded type and
corrupt memory), and every patch-closure instruction for a `$letrec` group must execute before
any closure in that group is called (an unset capture slot fails as "cannot call non-function
value").

`MenaiCFGSchedule` implements both and leaves a block containing patch-closure instructions
unscheduled; its module docstring is the reference. The general rule for any future reordering
pass: an instruction that defines no result is not automatically position-free.

### `$letrec` reaching the IR builder is always a genuine mutually-recursive group

The desugarer guarantees each `$letrec` is one strongly connected component; non-recursive
bindings are hoisted to `let`. Not every binding in the group is necessarily a lambda: a
non-lambda binding whose RHS contains a nested lambda closing over the group belongs there,
and the CFG builder evaluates such values after the sibling closures exist and patches nested
lambdas afterwards. IR passes may not assume all `$letrec` bindings are lambdas.
See [ADR-0008](../../docs/adr/0008-letrec-is-genuine-mutual-recursion.md).

### The prelude and the builtin registry must stay consistent

Opcode-backed builtins have an entry in `BUILTINS` in `menai_builtin_registry.py`, the single
source of truth for existence, opcode and surface arity. Prelude-only functions (e.g.
`map-list`, `filter-list`, `fold-list`) are Menai lambdas in `prelude.menai` and MUST NOT be
added to `BUILTINS`.
See [ADR-0026](../../docs/adr/0026-single-builtin-table.md).

### Name-based lowering must respect lexical shadowing

The desugarer rewrites `(integer+ a b)` to `($integer+ a b)` and the semantic analyser
validates arity, both by name. Both must first check whether an enclosing `let`/`lambda`/
`match` binds the name; if so, the call is the user's binding and neither the rewrite nor the
arity check may fire. The same gate applies to the builtin sequence pattern head lookup
(`(: list ...)`, `(: vector ...)`), because `list` and `vector` are also prelude binding names:
a head resolving to a non-type binding is a compile-time error, never a fallback to the
builtin.

The prelude is desugared as an ordinary program, so its own `let` bindings shadow the
builtins; it must write every opcode-backed call in explicit `$` form. Struct and enum type
recognition resolves against separate lexical type scopes, so a type is not visible outside the
binder that declares it.

A missed gate is silent: the form still compiles and runs, with the wrong meaning.
See [ADR-0062](../../docs/adr/0062-name-resolution-respects-lexical-shadowing.md).

### `let` bindings are mutually visible; evaluation is dependency-ordered

Every binding name is visible to every binding value and to the body, regardless of source
order, so `(let ((y (integer* x 2)) (x 5)) ...)` evaluates `y` to 10. `MenaiASTDependencyAnalyzer`
groups bindings into strongly-connected components and the desugarer emits them in topological
order as nested `$let` and `$letrec` forms, so a chain of dependent bindings is written as one
flat `let`. Do not infer nested source `let`s from nested `$let` nodes, and do not "simplify" a
flat `let` into nested ones — the desugarer re-splits it.

A non-lambda binding may not depend on another non-lambda binding in the same group: neither
has a value when the other is evaluated, so there is no base case. A reference shielded by a
lambda is fine, which is what makes self- and mutual recursion work.
See [ADR-0054](../../docs/adr/0054-let-is-the-only-binding-form.md).

### Modules export named bindings; namespaces are second-class

A module body ends with `(export name ...)`; `(import "name")` loads a namespace and
`(:: namespace member)` resolves at compile time. The resolver consumes the export form when
the module is imported, and lowers it to a dict of name to value when the module is compiled
directly as a program, so the desugarer only ever sees a stray `export` and rejects it.

A namespace may only be bound by a `let` binding and used as `::`'s first argument — it cannot
be passed, stored, returned or called. That is what makes every member access statically
resolvable. Member access is a disjoint form: the shape of the form decides its meaning. A
namespace name used as a call head is therefore an error, not member access.

The resolver alpha-renames a module's bindings with a per-import prefix. It must not rename a
namespace member name (a key, not a reference) nor a struct pattern head's member name. A
pattern head that names a module's *type* must be renamed, because it is a reference to the
module binding; an enum pattern's variant is a quoted symbol and is left alone, and it binds
nothing. If a type pattern head is not renamed, the desugarer can no longer resolve it to the
type binding and silently falls back to treating the pattern as a list destructuring pattern.
That is not a local failure: a list pattern compiles to a `list?`/`list-length`/`list-nth`
test chain, so a `match` over an enum degrades from a jump table into a long comparison chain
and can explode the CFG of any function it is inlined into.
See [ADR-0023](../../docs/adr/0023-second-class-module-namespaces.md).

### Exception blocks are laid out after the normal path

Blocks ending in a raise come after all normal-path blocks, the ordering is stable and the
entry block stays first. Emitted layout is derived from the CFG, not its block-list order, so
the partition must be applied to the layout order explicitly.
See [ADR-0030](../../docs/adr/0030-exception-blocks-emitted-last.md).
