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
- If you send a command to a terminal, don't forget the newline or carriage return required (Unix, or Windows specific).
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
│   ├── menai_pipeline/         # JSON-defined pipeline runner (tool + Menai steps)
│   ├── menai_pretty_print/     # code formatter
│   ├── menai_profile/          # profiling tool
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

- Opcode-backed builtins have an entry in `BUILTIN_OPCODE_ARITIES` in
  `menai_builtin_registry.py`. The registry asserts that every entry in this table has a
  corresponding opcode in `BUILTIN_OPCODE_MAP`. Adding a name here without an opcode
  will cause an assertion failure at startup.
- Prelude-only functions (e.g. `map-list`, `filter-list`, `fold-list`) are implemented
  as Menai lambdas in `prelude.menai`. They MUST NOT be added to `BUILTIN_OPCODE_ARITIES`.

See [ADR-0009](docs/adr/0009-prelude-and-builtin-registry-consistency.md).

### The C VM has no process-global mutable state

All mutable VM state (pool allocator free-lists, singletons, prelude globals) is
owned by `MenaiVMState`, a per-instance struct allocated by the Python `MenaiVM`
wrapper. A `MenaiVMState *` pointer is passed explicitly as the first argument
to every C function that allocates, frees, or touches singletons. There are no
file-level mutable statics in the C VM outside the bridge layer (which has only
read-only Python type references fetched at module init).

`menai_value_retain` is the one exception: it only increments `ob_refcnt` and
does not need `MenaiVMState *`. Every other refcount or allocation function
(`menai_value_release`, `menai_value_free`, `menai_alloc`,
`menai_free`, all `alloc_menai_*` constructors, `menai_none`, `menai_boolean_true`,
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

There are no cons cells and no improper lists. List construction uses `list`,
`list-prepend`, and `list-append`, all of which produce proper lists. The internal
representation of lists is an implementation detail of each binding. This is
intentional: improper lists add complexity for minimal benefit in a language
without pattern-matched list destructuring at the cons-cell level.
See [ADR-0003](docs/adr/0003-proper-lists-only.md).

### Strict numeric typing

There is no implicit coercion between `integer`, `float`, and `complex`. All arithmetic
operators are type-specific (e.g. `integer+`, `float*`). This is intentional.
See [ADR-0004](docs/adr/0004-strict-numeric-typing.md).

## VM implementation

The C VM (`menai_vm_c`) is the execution engine, compiled from C source and
loaded at runtime. `vm/menai_vm.py` is a thin Python wrapper that exposes the C
VM's `execute` and `cancel` functions to the rest of the codebase. The C extension
is compiled into platform-specific wheels via cibuildwheel and published to PyPI
on version tags (see `.github/workflows/release.yml`).

The C VM currently makes use of some Python runtime library functionality, but with the
exception of the bridge layer between C and Python, the C code should be systematically
updated so Python functions and data structures are removed.

### Debug build flags

The C VM supports compile-time debug features via preprocessor defines:

- `MENAI_DEBUG_LEAKS` — Tracks every `MenaiValue` allocation in a per-instance
  hash set.  At VM teardown, any value still tracked (excluding known
  singletons) is reported as a leak to stderr.  Build with `make build-leaks`.
  Used to verify that the reference counting scheme (see ADR-0016) correctly
  reclaims all values, including cyclic closures that require cycle collection.

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
