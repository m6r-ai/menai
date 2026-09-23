# AGENTS.md - Menai

## Purpose

This document exists to convey design intent, non-obvious invariants, and architectural
decisions that cannot be read directly from the code. It is a guide for AI agents
working on this codebase.

### What this document is NOT

This document does NOT describe what the code currently does in detail. It does not
reproduce pipeline diagrams, file-by-file role tables, pass-order lists, or any other
information that is already expressed clearly in the source. That kind of content becomes
a maintenance liability: it drifts out of date as the code evolves and then actively
misleads the next reader.

If you update the code, DO NOT add derived technical descriptions here. If you feel
the urge to document how something works, put that documentation in the source file itself
(module docstring, class docstring, inline comment) where it will be read alongside
the code it describes and is more likely to be kept correct.

This document should only grow when there is a genuine design decision, constraint, or
non-obvious invariant to record that cannot be expressed in the code itself.

## Where to start

- Pipeline: read `src/menai/menai_compiler.py` — it is the authoritative, always-current
  description of the compilation pipeline and pass order.
- Language semantics: use the AI tool description (available via the `help` tool).
  Do not rely on README.md for semantics. Do NOT assume that because it looks a bit like
  Scheme or Lisp that it's actually the same.
- AI-facing language reference: `src/menai/menai_help.py` is the canonical source of the
  text shown to AI agents via the `help` tool (exposed as `menai.get_help()`). The Menai
  AI tool (e.g. Humbug's `menai_ai_tool.py`) fetches this text rather than maintaining its
  own copy, so the help can never drift out of sync with the language. When you add a
  language feature, update `menai_help.py` so the AI-facing reference stays accurate.
- Human-readable language manual: `docs/` is the manual for human readers. It is
  AI-maintained and kept consistent with `menai_help.py`.
- Individual passes: each source file has a module-level docstring that describes
  what that pass does, its invariants, and its position in the pipeline.

## APIs

Many of the internal APIs are non-obvious. DO NOT attempt to guess what they might be.
If you need to use an API read the source code to understand it first.

## Tool use

- If you want to use the terminal you will require user authorization every time you send keystrokes.  If you load files into
  an editor tab, however, you don't.  If you just want to do a simple search of a file then consider using the editor
  tabs.  They can get pretty cluttered though so if you don't need the tab again then close it.
- If you open a terminal it will automatically be in the root of the mindspace directory.  Don't change directory unless
  you want to be somewhere else.
- Terminals will not open with a python virtual environment by default.  The venv is at `venv/` in the mindspace root.
- Do not pipe pytest output through `grep` or other filtering tools.  pytest interleaves progress dots on stderr with
  summary lines on stdout, so filtering mangles the output and hides the pass/fail counts.  Run pytest with no flags
  and pipe through `tail` only if the output is too long to read in full:
  ```bash
  python -m pytest tests/src/menai/ 2>&1 | tail -10
  ```

## Code quality

- Before considering any code change complete, run the full suite of static analysis tools:
  ```bash
  source venv/bin/activate && python -m tools.code_checker
  ```
  All checks must pass cleanly before the work is done.

## Code generation

- Do not write lengthy file-level docstrings.  These go stale very fast as the code evolves.
- Do not add comments marking blocks of functionality within files.  Functions, classes, etc., have docstrings so we have
  everything we need anyway and these sorts of delimeter comments simply add clutter to the code.
- If you are writing tests, the tests must reflect the correct and desired behaviour.  NEVER write or patch a test to
  mask broken implementation logic.  If the logic is wrong then a test must fail.
- Test docstrings must describe the expected correct behaviour only.  They must not reference previously broken
  behaviour, historical bugs, or implementation details of past fixes.  A test is a specification, not a changelog.
- Do not write block comments using lines of dashes.  E.g. never do this:
  ```python
  # -----------------------------------------------------------
  # This is a block level comment because I like wasting tokens
  # -----------------------------------------------------------
  ```
  Functions/methods have doc strings and we don't need comments about grouping of things because they go stale.
- We use modern Python, so never use `Optional`, always use `type | None`.
- Never use `Union[X, Y]`; always use the modern `X | Y` syntax.
- Never import legacy typing aliases.  Use builtins (`dict`, `list`, `set`, `tuple`, `type`,
  `frozenset`) or `collections.abc` (`Callable`, `Awaitable`, `AsyncGenerator`, `Generator`,
  `Iterator`, `Sequence`, `Coroutine`) instead of `typing.Dict`, `typing.List`, etc.
- Do not use `@property`.  Simple getter methods (e.g. `def foo(self) -> T:`) are used instead.
- Do not pad-align `=` signs in consecutive assignment statements.  Each assignment should have a single space before
  the `=`, regardless of surrounding assignments.
- Put a blank line after any code block.  If the code dedents then there should be a blank line before it.
- Multi-line docstrings must have the opening `"""` and closing `"""` on their own lines, with no other text.
- These and other style rules are enforced by the style checker pylint plugin (`tools/style_checker/`), which runs
  automatically as part of `python -m tools.code_checker`.

## YAGNI (You Aren't Gonna Need It)

This project strongly follows the YAGNI principle.  If there is no clear reason for a feature, method, or helper to exist,
it should not be added.

- Every method, function, class, and module must be used somewhere in the codebase.  If code cannot be reached at
  runtime in this project or its supporting tools, remove it.
- Do not add speculative helper functions, convenience methods, or abstraction layers that are not called by real
  code.  "It might be useful someday" is not a valid reason.
- When restructuring or refactoring, remove any code that is no longer called rather than leaving it in place.

## Top-level structure

```text
menai/
├── docs/                       # language manual (human + AI readable)
├── menai_modules/              # standard library (.menai files)
├── pyproject.toml              # Python package configuration (setuptools backend)
├── setup.py                    # C VM extension build (platform-specific compile flags)
├── .github/workflows/          # CI (build+test on push) and release (cibuildwheel on tag)
├── src/
│   ├── menai/                  # compiler core (lexer, parser, IR, CFG, bytecode, VM)
│   ├── menai_benchmark/        # performance benchmarking tool
│   ├── menai_check/            # parenthesis balance checker
│   ├── menai_disassemble/      # bytecode disassembler
│   ├── menai_eval/             # evaluator: compile, run, and profile a .menai file
│   ├── menai_pipeline/         # JSON-defined pipeline runner (tool + Menai steps)
│   ├── menai_pretty_print/     # code formatter
│   └── menai_test/             # test runner for *_test.menai files
└── tests/
    ├── src/
    │   └── menai/              # compiler core tests
    └── tools/
        └── style_checker/      # style checker tests
```

## Architectural invariants

These are constraints that must hold across the whole compiler. They are recorded here
because they span multiple files and are easy to violate accidentally.

### The IR tree is immutable — passes return new trees

No IR optimisation pass may mutate its input tree in place. Each pass receives an IR tree
and returns a new one, along with a boolean indicating whether anything changed. The
pass manager uses that flag to drive the fixed-point loop.

See [ADR-0005](docs/adr/0005-ir-tree-immutability.md).

The reason: immutability makes passes composable and makes bugs easier to isolate. A pass
that mutates its input can corrupt the tree in ways that only manifest later in an
unrelated pass.

### CFG passes declare their scope via their base class

CFG optimisation passes operate at module scope: a pass is handed the root
`MenaiCFGFunction` and returns the (possibly new) root plus a changed flag. There is
no separate module object — the module is the root function plus every function
reachable through `MenaiCFGMakeClosureInstr` instructions, enumerated by
`collect_functions`.

A pass declares whether it is intraprocedural or whole-program by its base class:

- `MenaiCFGPerFunctionPass` — implement `_optimize_function`; the base class handles
  traversal of the function tree and write-back of replaced nested functions.
- `MenaiCFGWholeProgramPass` — implement `_optimize_module`; the pass owns its own
  traversal and write-back.

Do not write a whole-program pass against the per-function contract (or vice versa).
The base class is how a pass's scope is made visible at its declaration site.

See [ADR-0020](docs/adr/0020-cfg-pass-scope-contracts.md).

### Type facts distinguish "no information" from "conflicting information"

The interprocedural type analysis (`menai_cfg_interproc_type_analysis.py`) uses a
three-level fact lattice: BOTTOM (no information), a known kind, and ANY (conflicting
kinds). These must stay distinct. If both "no information" and "conflicting
information" mapped to a single unknown element, joining two different known kinds
would move down the lattice and the parameter-fact fixed point would oscillate instead
of converging.

BOTTOM is the fixed-point identity, so it is not the same as "unknown". A call site
whose argument's type is unknown must degrade the parameter it feeds, not be dropped
by the join. Conflating the two lets a value of one type be reported as proven for a
parameter that an unknown value can also reach, which is a miscompile, not a missed
optimisation.

The analysis is a pure optimisation: where a type cannot be proven, the existing
runtime path is used. It never changes observable behaviour.

See [ADR-0021](docs/adr/0021-interprocedural-type-analysis.md) and
[ADR-0027](docs/adr/0027-recursion-cycle-parameter-grounding.md).

### Menai is pure — dead code elimination is always safe

Because Menai has no side effects, any expression whose result is never used can be
discarded unconditionally. Optimisation passes may rely on this without checking for
side effects.

See [ADR-0007](docs/adr/0007-dead-code-elimination-always-safe.md).

### `letrec` reaching the IR builder is always a genuine mutually-recursive group

The desugarer guarantees that by the time `letrec` reaches the IR builder, every
`letrec` is a single strongly-connected component of mutually-recursive bindings.
Non-recursive bindings are hoisted to `let` forms.

However, not every binding in a `letrec` group is necessarily a lambda. A
non-lambda binding (e.g. `(letrec ((x (list (lambda () x)))) x)`) can appear in a
`letrec` group when its RHS contains a nested lambda that closes over the binding
name — the dependency analyzer sees a cycle and correctly keeps it in `letrec`.
The IR builder and both codegens handle this. The CFG builder handles it via a
dedicated Phase 2b / Phase 3b in `_build_letrec`: non-lambda binding values are
evaluated after all sibling lambda closures exist (so nested lambdas can capture
them), and any nested lambdas with sibling captures are patched afterward.

IR passes downstream of the IR builder may not assume all `letrec` bindings are lambdas.

See [ADR-0008](docs/adr/0008-letrec-is-genuine-mutual-recursion.md).

### The prelude and the builtin registry must stay consistent

There are two categories of builtin that must not be confused:

- Opcode-backed builtins have an entry in `BUILTINS` in `menai_builtin_registry.py`.
  Each entry records the opcode, which is the single source of truth for the
  builtin's existence and its opcode arity. A builtin's surface arity is recorded
  alongside it.
- Prelude-only functions (e.g. `map-list`, `filter-list`, `fold-list`) are implemented
  as Menai lambdas in `prelude.menai`. They MUST NOT be added to `BUILTINS`.

See [ADR-0026](docs/adr/0026-single-builtin-table.md).

### Name-based lowering must respect lexical shadowing

The desugarer lowers a call by name: `(integer+ a b)` becomes `($integer+ a b)`,
`(list-length x)` becomes `($list-length x)`, and so on. The semantic analyser
likewise validates call arity against the builtin registry by name. Both
must first check whether the name is bound by an enclosing lexical binder
(`let`/`let*`/`letrec`/`lambda`/`match`); if it is, the call refers to the user's
binding and neither the rewrite nor the arity check may fire. Both passes track
this with a scope stack.

The prelude is desugared as an ordinary program, so its own top-level `letrec`
bindings shadow the builtins. The prelude must therefore write every call to an
opcode-backed builtin in explicit `$`-prefixed form; it must not rely on the
desugarer's name-based rewrites. Prelude-only functions (which have no primitive
form) are called unprefixed, as normal.

### Struct type recognition is lexically scoped

Struct recognition in the desugarer (constructor calls `(Point 1 2)` and
destructuring patterns `(Point x y)`) resolves the type name against the struct
types declared by the enclosing lexical binders, exactly like any other name.
A struct type is not visible outside the binder that declares it, and two
struct types with the same name in different scopes are distinct.

Struct declarations are the one exception to `let` being parallel: a struct
binding is hoisted so that sibling binding values and the body can use it as a
constructor or pattern head. Ordinary `let` bindings are not hoisted.

Consequence: a struct type exported from a module and rebound by the importer
(e.g. bound from a namespace member) *is* recognised as a struct by name, because
binding it to a local name puts the declaration in the importer's lexical scope.
An imported struct is used as a constructor and pattern head exactly like a locally
declared one; the importer binds the member to a local name first (see ADR-0023).

### Modules export named bindings; namespaces are second-class

A module's body ends with an `(export name ...)` form naming the bindings it
exports. `(import "name")` loads a module as a namespace, and `(:: namespace member)`
resolves at compile time to the declaration that produced the member.

The export form is consumed by the module resolver, and there are two paths. When a
module is imported, the resolver consumes the export form and builds the namespace.
When a module file is compiled directly as a program (e.g. by `menai-eval` or
`menai-disassemble`) there is no importer, so the resolver lowers the export form to a
dict mapping each export name to its value. Both paths go through the resolver, so the
desugarer only ever sees a genuinely stray `export` (one that is not a module body) and
rejects it.

A namespace is second-class: it may only be bound directly by a `let`/`let*`/`letrec`
binding and used as the first argument of `::`. It cannot be passed, stored, returned,
or called as a function. This restriction is what guarantees every member access is
statically resolvable.

Member access is a disjoint form: the shape of the form decides its meaning, so a
namespace name used as a call head is an error, not member access. `::` is a reserved
form head, like `import`/`export`/`struct`.

The module resolver alpha-renames a module's bindings with a per-import prefix so
that importing two modules that share a private name does not collide. The renamer
must not rename a namespace member name (it is a key, not a variable reference) nor
a struct pattern head's member name — see `_ModuleRenamer`.

See [ADR-0023](docs/adr/0023-second-class-module-namespaces.md).

### The C VM has no process-global mutable state

All mutable VM state (pool allocator free-lists, singletons, the closure registry) is
owned by `MenaiVMState`, a per-instance struct allocated by the Python `MenaiVM`
wrapper. A `MenaiVMState *` pointer is passed explicitly as the first argument
to every C function that allocates, frees, or touches singletons. There are no
file-level mutable statics in the C VM outside the bridge layer (which has only
read-only Python type references fetched at module init).

`menai_value_retain` is the one exception: it only increments `ob_refcnt` and
does not need `MenaiVMState *`. Every other refcount or allocation function
(`menai_value_release`, `menai_value_free`, `menai_alloc`,
`menai_free`, `menai_pool_alloc`, `menai_pool_free`, all `alloc_menai_*` constructors, `menai_none`, `menai_boolean_true`,
`menai_boolean_false`) takes `MenaiVMState *vs` as its first parameter.

`MenaiCodeObject` retain/release does NOT take `MenaiVMState *` — C code objects
are ephemeral (built and destroyed within a single `execute()` call) and never
shared across VM instances or threads.

See [ADR-0006](docs/adr/0006-no-process-global-mutable-state-in-c-vm.md).

### The closure cycle collector only frees closures with refcnt == 0

The GC runs at the end of every `menai_vm_execute_native` call and at VM
teardown.  It marks all closures reachable from globals and the execute
result, then sweeps unreachable closures.  Phase 3 breaks internal edges
(dead-to-dead capture references) with bare refcount decrements.  Phase 4
must only call `menai_value_free` on closures whose `ob_refcnt` is exactly
0 after Phase 3.  Closures with `ob_refcnt > 0` have an external reference
(e.g. from a code object's constant pool, which is released after the GC
runs) and must be returned to the registry, not freed.

The GC does not trace code object constants as roots.  This is safe because
closures in constants are non-cyclic (the bridge strips captures during
round-tripping).  The Phase 4 refcnt guard prevents use-after-free when a
non-cyclic closure in constants is unreachable from roots but still held
by the code object.  The `MENAI_DEBUG_LEAKS` build monitors this.

## Design decisions

These are decisions that might otherwise look like oversights or invite "improvement".

### No `cond` form

Deliberate omission. `match` covers all multi-branch conditional use cases and is more
expressive. Do not add `cond`. See [ADR-0001](docs/adr/0001-no-cond-form.md).

### Symbols are not strings

`symbol` values are produced only by `quote` and exist solely to support homoiconicity
(code-as-data). See [ADR-0002](docs/adr/0002-symbols-not-strings.md).

### Proper lists only

There are no improper lists and no `cons` operation in the surface language.
List construction uses `list`, `list-prepend`, and `list-append`, all of which
produce proper lists. Internally, lists are represented as cons cells (linked
pairs where the cdr is always a list or nil) — this gives O(1) head/tail
decomposition and O(1) prepend, which aligns with the recursive traversal
patterns that dominate Menai list processing. Random access is O(n). The
dotted pattern `(head . tail)` in `match` is a pattern-matching convenience,
not a cons-cell type.
See [ADR-0017](docs/adr/0017-cons-cell-internal-representation.md) (supersedes
[ADR-0003](docs/adr/0003-proper-lists-only.md)).

### Vector type

Vectors are immutable, contiguous-array-backed sequences with O(1) random
access — a distinct type from lists, with no coercion. They complement lists
for index-heavy code where lists' O(n) random access is a bottleneck (e.g.
Sudoku, Rubik's Cube). Vectors are not pattern-matchable and not hashable.
There is no literal syntax; vectors are created via `(vector ...)`.
See [ADR-0018](docs/adr/0018-vector-type.md).

### Strict numeric typing

There is no implicit coercion between `integer`, `float`, and `complex`. All arithmetic
operators are type-specific (e.g. `integer+`, `float*`). This is intentional.
See [ADR-0004](docs/adr/0004-strict-numeric-typing.md).

### Slice operations raise errors on out-of-bounds indices
All four slice operations (`string-slice`, `list-slice`, `vector-slice`,
`bytes-slice`) raise a runtime error on negative, out-of-range, or
start-after-end indices. They do not silently clamp.
See [ADR-0019](docs/adr/0019-slice-out-of-bounds-raises-error.md).

### Hash operations are native VM primitives
The `bytes` hash operations (`bytes-hash-sha2-256`, `bytes-hash-sha2-512`,
`bytes-hash-sha2-512-256`, `bytes-hash-sha3-256`) are opcode-backed primitives
implemented natively in the VM core, not prelude functions. Hashing is a
performance-critical, fixed-spec, integer-heavy kernel and therefore a language
primitive.
See [ADR-0024](docs/adr/0024-hash-primitives-in-c-vm.md).

### CRC-32 is a native VM primitive
The `bytes-crc32` operation is an opcode-backed primitive implemented natively
in the VM core, not a prelude function, and returns the checksum as an
`integer`. It is a performance-critical, fixed-spec, integer-heavy kernel and
therefore a language primitive.
See [ADR-0025](docs/adr/0025-crc32-primitive-in-c-vm.md).

## VM implementation

The C VM (`menai_vm_c`) is the execution engine, compiled from C source and
loaded at runtime. `vm/menai_vm.py` is a thin Python wrapper that exposes the C
VM's `execute` and `cancel` functions to the rest of the codebase. The C extension
is compiled into platform-specific wheels via cibuildwheel and published to PyPI
on version tags (see `.github/workflows/release.yml`).

The C VM only uses Python runtime library functionality within the bridge layer
between C and Python. All other C code is free of Python dependencies.

### Debug build flags

The C VM supports compile-time debug features via preprocessor defines:

- `MENAI_DEBUG_LEAKS` — Tracks every `MenaiValue` allocation in a per-instance
  hash set.  At VM teardown, any value still tracked (excluding known
  singletons) is reported as a leak to stderr.  Build with `make build-leaks`.
  Used to verify that the reference counting scheme (see ADR-0016) correctly
  reclaims all values, including cyclic closures that require cycle collection.

- `MENAI_DEBUG_MAGIC` — Every `MenaiValue` and `MenaiCodeObject` carries a
  `ob_magic` field set at allocation and cleared before the block is freed or
  returned to the pool.  `MENAI_CHECK_MAGIC` aborts if the field does not
  match, catching use-after-free and double-free at the point of access.  Build
  with `make build-magic`, or `make build-debug` to combine both debug flags.

### C formatting

Do NOT use lines of characters in comments. E.g. never use something like:

```c
/* --------------------------------
 * This is a bad comment - don't do this!
 * -------------------------------- */
```

For single line comments put the open and close of the comment on the same line:

```c
/* This is a good single line comment */
```

For multi-line comments the open and close go on their own lines:

```c
/*
 * This is a great multiline comment.
 * Where we have more than one line of text.
 */
```

Do NOT use excess whitespace to line up things on adjacent lines. E.g. never do:

```c
int x_with_long_name = 0;
iny y                = 1;
```

Instead do:

```c
int x_with_long_name = 0;
int y = 1;
```

Do NOT put code on the same line after an opening brace. E.g. never do:

```c
if (foo) { something(); }
```

Instead do:

```c
if (foo) {
    something();
}
```
