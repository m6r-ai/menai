# AGENTS.md - Menai

## Purpose

This document conveys design intent, non-obvious invariants and decisions that cannot be
read directly from the code. It is a guide for AI agents working on this codebase.

### What this document is NOT

Do not add derived technical descriptions here: no pipeline diagrams, file-by-file role
tables, pass-order lists, or anything else the source already states clearly. That content
drifts out of date and then actively misleads. If you want to document how something works,
put it in the source file itself (module, class or function docstring) where it is read
alongside the code it describes.

The guardrails are split by area so that only the relevant part loads when you work in a
directory:

| Area | File |
|------|------|
| compiler core: AST, desugaring, IR, CFG, type analysis, builtins, modules | `src/menai/AGENTS.md` |
| C VM: values, allocation, GC, errors, debug builds | `src/menai/vm/AGENTS.md` |
| standard library `.menai` modules | `src/menai/stdlib/AGENTS.md` |
| full reasoning behind each decision | `docs/adr/` |

Add a decision here only when it is a genuine design constraint or non-obvious invariant
that cannot be expressed in the code. Put it in the nearest directory that contains
everything it constrains; use this file only for something that holds across packages.

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
- Architecture decisions: `docs/adr/README.md` defines the format and the principles. An
  ADR is a permanent record, so it must be self-contained — it may cite another ADR, the
  source, or an external specification, but never an `AGENTS.md` file or any other
  document that is expected to change. The guidance quotes the ADR, never the reverse.

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
  Activate it (`source venv/bin/activate`) before running any of the project's console scripts.  Without it, the
  scripts resolve to a stale globally-installed copy rather than the working tree, which produces misleading results
  (for example `menai-test` reporting that no test files were found).
- Do not pipe pytest output through `grep` or other filtering tools.  pytest interleaves progress dots on stderr with
  summary lines on stdout, so filtering mangles the output and hides the pass/fail counts.  Run pytest with no flags
  and pipe through `tail` only if the output is too long to read in full:
  ```bash
  python -m pytest tests/src/menai/ 2>&1 | tail -10
  ```
- Do not filter or truncate the output of any tool.  Do not pipe a command through `grep`, `head`, `tail`, `awk`, or
  any other filter, and do not add `| tail -N` to a command to "save tokens".  Filtering hides the very lines that
  explain a failure, and it silently discards output you will need.  Run the command with no filtering and read the
  full output.  If output is genuinely too long to read, read it in full anyway, or ask the user how to narrow the
  command — do not filter it yourself.
- The standard library module tests (`tests/src/menai/stdlib/`) are slow: each parametrised
  case compiles and runs a full Menai suite.  Use `make test-fast` to run everything except
  them when iterating.  `make test` runs the full suite, and CI runs the full suite, so the
  standard library is still exercised.

## Project tools

The project installs a set of console scripts (declared in `pyproject.toml`). They are
available on the PATH once the venv is active. Do not guess their names or invoke the
underlying modules with `python -m`; use the console scripts, and use `--help` for the
full flag list rather than assuming what a flag is called.

- `menai-test <path>...` — discovers and runs `*.test.menai` suites from files or
  directories. This is the tool for running the standard library module tests:
  ```bash
  menai-test src/menai/stdlib/                          # all module tests
  menai-test src/menai/stdlib/json-decode.test.menai    # a single suite
  ```
- `menai-eval <file>` — compiles and evaluates a `.menai` file (or an expression from
  stdin) and prints the result.
- `menai-format <file>...` — formats Menai source. `--check` exits non-zero if a file does
  not match the canonical style.
- `menai-disassemble <file>` — compiles and prints annotated bytecode disassembly.
- `menai-benchmark` — runs the performance benchmark suites.
- `menai-pipeline` — runs a JSON-defined pipeline of tool and Menai steps.

`menai-eval`, `menai-benchmark` and `menai-pipeline` share profiling and coverage flags:
`--profile`, `--opcodes` and `--annotate` profile VM execution per function, per opcode and
as annotated disassembly, and `--coverage` reports bytecode instruction coverage. Most
report-producing tools accept `--width N` to clip lines to N columns.

## Code quality

- Before considering any code change complete, run the full suite of static analysis tools:
  ```bash
  source venv/bin/activate && python -m tools.code_checker
  ```
  All checks must pass cleanly before the work is done. It runs mypy, pylint, and the style
  checker described under Code generation. Contributors get the same rules from
  `CONTRIBUTING.md`; keep the two consistent when a rule changes.

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
├── docs/                       # language manual (human + AI readable) and docs/adr/
├── src/
│   ├── menai/                  # compiler core (lexer, parser, IR, CFG, bytecode, VM)
│   │   └── stdlib/             # standard library (.menai files)
│   ├── menai_benchmark/        # performance benchmarking tool
│   ├── menai_disassemble/      # bytecode disassembler
│   ├── menai_eval/             # evaluator: compile, run, and profile a .menai file
│   ├── menai_format/           # code formatter
│   ├── menai_pipeline/         # JSON-defined pipeline runner (tool + Menai steps)
│   ├── menai_render/           # shared bytecode rendering and code-object walk
│   ├── menai_trace/            # VM instruction and call trace resolution and rendering
│   └── menai_test/             # test runner for *.test.menai files
├── tests/                      # mirrors src/
└── tools/                      # style_checker/
```

The `tests/` tree mirrors the source tree: a test for code under `src/<name>/` lives under
`tests/src/<name>/`, and a test for code under `tools/<name>/` lives under
`tests/tools/<name>/`. The standard library is package data of the `menai` package, so its
tests live under `tests/src/menai/stdlib/`.

A test may keep a private copy of a `.menai` module next to itself rather than importing one
from `src/`. `test_rubiks_cube_functions.py` does this: its `rubiks_cube.menai` is frozen in
the form that exposed the compiler bug the suite guards against, while the benchmark suite's
copy has since moved on. The copy is not a duplicate to be deduplicated — pointing the test
at the benchmark copy would destroy the regression it exists to catch. A test that pins a
representation for a reason must say so in its own header, as that copy does.

## Language design decisions

These might otherwise look like oversights or invite "improvement". The ADR behind each one
carries the alternatives that were rejected.

- **No `cond`.** `match` covers every multi-branch conditional and is more expressive. Do not
  add `cond`.
  See [ADR-0001](docs/adr/0001-no-cond-form.md).
- **Symbols are not strings.** Symbols come only from `quote` and exist for homoiconicity.
  See [ADR-0002](docs/adr/0002-symbols-not-strings.md).
- **Proper lists only.** No `cons` and no improper lists in the surface language; `(head . tail)`
  in a match is a pattern convenience, not a cons-cell type. Lists are cons cells internally:
  O(1) head, tail and prepend, O(n) random access.
  See [ADR-0017](docs/adr/0017-cons-cell-internal-representation.md), which supersedes
  [ADR-0003](docs/adr/0003-proper-lists-only.md).
- **Vectors are a distinct type** with O(1) access, for index-heavy code where a list's O(n)
  access dominates; not pattern-matchable, not hashable, and no coercion.
  See [ADR-0018](docs/adr/0018-vector-type.md).
- **Element access verbs are `-nth`, `-get`, `-with`, `-without`** — position, key, add or
  replace, remove. `-set`, `-add` and `-remove` connote mutation, which Menai does not have; do
  not reintroduce them.
  See [ADR-0037](docs/adr/0037-collection-access-verbs.md).
- **Strict numeric typing.** No implicit coercion between `integer`, `float` and `complex`;
  every arithmetic operator is type-specific.
  See [ADR-0004](docs/adr/0004-strict-numeric-typing.md).
- **Slices raise.** All four slice operations raise on a negative, out-of-range or
  start-after-end index rather than clamping.
  See [ADR-0019](docs/adr/0019-slice-out-of-bounds-raises-error.md).
- **Bytes hashing and CRC-32 are opcodes**, not prelude functions: they are fixed-spec,
  integer-heavy, performance-critical kernels.
  See [ADR-0024](docs/adr/0024-hash-primitives-in-c-vm.md) and
  [ADR-0025](docs/adr/0025-crc32-primitive-in-c-vm.md).
- **`regexp` is a standard library capability module**, not an opcode: the pattern dialect is a
  Menai design decision rather than a fixed specification, and its operations share one
  compiled-regexp type and dialect.
  See [ADR-0041](docs/adr/0041-standard-library-module-taxonomy.md) and
  [ADR-0052](docs/adr/0052-regular-expression-captures.md).
- **Enums are bare-tag**, and the form must not foreclose payloads. A match is fused into a
  `SWITCH_ENUM` jump table, so the group's `enum-is-instance?` test is load-bearing: the switch
  reads the variant index without reading the type. Exhaustiveness checking is not implemented;
  when added it must be an error, computed as a variant set so that it extends to nested
  patterns.
  See [ADR-0043](docs/adr/0043-enum-type.md).
- **A type pattern head carries `:`** — `(: Point x y)`, `(: state 'idle)`. A bare
  `(Name p1 ...)` is always a list pattern, so a pattern's meaning never depends on what types
  happen to be in scope. Declaration and construction are unchanged.
  See [ADR-0044](docs/adr/0044-type-pattern-heads-are-syntactically-distinct.md).
- **A match's desugared form is bounded** at `MAX_DESUGARED_MATCH_NODES` (4096) nodes. A
  list pattern expands super-linearly, and every later pass is super-linear in its input, so
  the bound turns a hang into a reported error. The largest standard library match is 290
  nodes.
  See [ADR-0045](docs/adr/0045-desugared-match-size-is-bounded.md).
- **`menai-format` implements one fixed style**, 120 columns wide. It specifies rather than
  describes the corpus: do not adjust the formatter to match a file; change the ADR if the
  style is wrong.
  See [ADR-0051](docs/adr/0051-menai-source-formatting-style.md).
- **Paren diagnostics name the line to change**, not where the symptom appears. A missing and
  an extra `)` need different evidence, so three techniques are required and must not be
  merged: the open-form stack, the parser's cursor and shape reconstruction.
  See [ADR-0053](docs/adr/0053-paren-diagnostics-locate-the-line-to-change.md).

## Cross-package contracts

These hold across package boundaries, so they live here rather than in a directory file.

### The code-object walk order is a contract between Python and C

`menai_render.menai_render_walk.walk_code_objects` defines the canonical ordering of code
objects and instructions within a compiled program: depth-first, root first, children in
list order, with a global instruction ordinal assigned contiguously in visit order. The C
VM's instruction tracer assigns the same ordinals while converting the Python `CodeObject`
tree, and the two implementations must agree exactly. If they drift, execution counts
collected in C are attributed to the wrong instructions, silently; a change to the
traversal order on one side must be mirrored on the other, and a test covers the agreement.

The ordering must remain a pure function of the tree structure. It is what makes a trace
buffer meaningful across repeated `execute()` calls: the native code objects are ephemeral,
so ordinals — not pointers — are the only stable key.

See [ADR-0028](docs/adr/0028-instruction-tracing-by-ordinal.md).

### The module search path is composed by the library, not by each tool

The standard library ships as package data inside the `menai` package, under
`src/menai/stdlib/`, and is located through `importlib.resources` — the same mechanism the
prelude uses. It is therefore present in a wheel install, not just a source checkout, and a
caller can read a module's source (`Menai.stdlib_source`) to understand it.

The search path is composed in a fixed precedence order by `Menai.build_module_path`:
explicit directories first, then application library directories (`MENAI_PATH`, then the
source file's directory), then the standard library. Resolution is first-match-wins, so an
application library may shadow a standard library module of the same name.

Tools must not build their own search path. Each tool composes the default through
`Menai.build_module_path` and prepends its own `--module-path` directories; a tool that
builds its own list breaks the precedence guarantee and can omit the standard library
entirely.

See [ADR-0038](docs/adr/0038-standard-library-packaging-and-location.md) and
[ADR-0040](docs/adr/0040-module-search-path-and-application-libraries.md).

### Renaming a module requires a repo-wide text sweep, not just `.menai` files

Module names appear in more places than the module files themselves. In particular they
appear as string literals inside Python source — benchmark suites and pipeline examples
build Menai expressions as `'(let ((x (import "module-name"))) ...)'` strings.

A rename is not complete until every occurrence of the old name has been found and updated.
Search the whole repository (excluding `.git`, `venv`, and build artefacts), not just
`src/menai/stdlib/` and `docs/`. Both the old module name and the old export name must be
swept, and the test-file naming convention (`<module>.test.menai`) must be applied to any
renamed test file.
