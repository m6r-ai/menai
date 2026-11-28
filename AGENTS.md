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
  It is also the only way to measure a module through the suite that specifies it:
  `--function-coverage` and `--coverage` report what those tests do not reach, and
  `--annotate [FUNCTION]` prints the disassembly of that very run with each
  instruction's execution count.
- `menai-eval <file>` — compiles and evaluates a `.menai` file (or an expression from
  stdin) and prints the result.
- `menai-format <file>...` — formats Menai source in place. It takes several files and
  rewrites each one that differs from the canonical style; `--check` instead reports which
  files need formatting and exits non-zero if any do. Use `-o FILE` for a single file (`-o -`
  for stdout) and `-` to format stdin to stdout.
- `menai-disassemble <file>` — compiles and prints annotated bytecode disassembly.
- `menai-benchmark` — runs the performance benchmark suites.
- `menai-pipeline` — runs a JSON-defined pipeline of tool and Menai steps.

`menai-eval`, `menai-benchmark`, `menai-pipeline` and `menai-test` share profiling and
coverage flags:
`--profile`, `--opcodes` and `--annotate` profile VM execution per function, per opcode and
as annotated disassembly, and `--coverage` reports bytecode instruction coverage. Most
report-producing tools accept `--width N` to clip lines to N columns.

To answer "why is this instruction not covered", do not reach for `menai-disassemble` plus
`menai-eval` on a program of your own: the suite and a driver compile the tree differently,
so their instruction indices do not agree and the comparison silently misleads.
`menai-test --annotate <function>` reports the trace of the suite's own compilation, and
`--isolate` cannot be combined with any of these reports.

## Measuring an optimisation change

Wall-clock timings cannot resolve a compiler change. Benchmarks are millisecond-scale and
noisy: an A/A control — the same build benchmarked twice — gives per-case ratios of ±5–14%,
which is the same magnitude a real change produces. Run the control before trusting any
timing, and do not use timings to accept or reject an optimisation.

Executed instruction counts are deterministic and are the measurement to use. Compare the
summed `TOTAL` rows of the opcode profile, taken on the same corpus with one iteration:

```bash
menai-benchmark --opcodes --iterations 1 --no-color > /tmp/op-after.log
```

The counts contain thousands separators, so sum them rather than comparing totals by eye.
Compare against a log taken from the previous commit (or from `git stash`), and report the
per-case deltas, not only the corpus total: a change that improves the total while some cases
regress is usually trading one hot path for another, and the regressions are the evidence.

Three rules, each learned from a change that looked good and was not:

- **Measure a green tree.** A build with failing tests can post a better number precisely
  because it is wrong: a miscompile that reads a stale register may execute fewer
  instructions. Run the full suite before measuring, and never quote a number taken before it
  passed.
- **Count cost in both currencies.** An optimisation can reduce executed instructions while
  making the compiler much slower, or the reverse. If a change adds work to a pass, measure
  compile time as well — a few modules compiled to a CFG is enough to see an order-of-magnitude
  regression.
- **A pass's benefit is not the work it deletes locally.** Folding or merging removes
  instructions, but it also removes the shape that later passes were exploiting, and work can
  reappear multiplied inside a loop. Deleting an instruction executed once per call is worth
  almost nothing; starving a shared invariant that executes once per element is expensive.

## An in-place update changes the cost model

Collection updates land in place when the accumulator is a loop-carried variable of the loop that
performs the write, so building a collection an element at a time is amortised O(1) per element
rather than O(n). The conditions are stated at the update opcodes and in
[`src/menai/vm/AGENTS.md`](src/menai/vm/AGENTS.md); the reasoning is in
[ADR-0063](docs/adr/0063-in-place-update-of-uniquely-owned-collections.md) and
[ADR-0064](docs/adr/0064-bytes-update-in-place.md), with
[ADR-0065](docs/adr/0065-string-append-in-place.md) for strings. What follows from it is a
maintenance obligation, not a mechanism.

**An idiom that exists only to minimise copying is now a suspect.** A sharded dictionary, an
extra pass whose purpose was to avoid a copy, a loop split into helpers to keep a collection out
of the argument list, a list used where a vector would now be cheaper: each of these was the
right answer under copying and may not be now. Re-deriving such a site is expected work, not the
speculation that [YAGNI](#yagni-you-arent-gonna-need-it) forbids — the DEFLATE compressor's
sixteen-way sharded LZ77 table existed only for this reason, and removing it was worth 30–50% on
the compression benchmarks.

**It is not a licence to flatten loops mechanically.** The copy being removed is often a small
memcpy that costs less than the loop state needed to keep the accumulator local: flattening
`bmp-encode`'s per-row loop measured slower than the shape it replaced. Re-derive, then measure.

**The opcode profile cannot see this class of change.** The copying happens inside an opcode
body, so executed instruction counts are identical before and after and the method above does not
apply. Use wall-clock at the largest input the suite has, and expect the gain to be asymptotic:
it appears as the input grows, often beyond the sizes the benchmark cases use.

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
│   │   ├── stdlib/             # standard library (.menai files)
│   │   └── testing/            # test support module (.menai package data)
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
  access dominates; destructured with a `:` head like any other fixed-arity structural type
  (`(: vector a b c)`), not hashable, and no coercion.
  See [ADR-0018](docs/adr/0018-vector-type.md), amended by
  [ADR-0057](docs/adr/0057-typed-destructuring-patterns.md).
- **Element access verbs are `-nth`, `-get`, `-with`, `-without`** — position, key, add or
  replace, remove. `-set`, `-add` and `-remove` connote mutation, which Menai does not have; do
  not reintroduce them, and the same applies to `-write` and `-read`, which no builtin uses.
  bytes reads a field with `bytes-nth-{width}-{endian}` and replaces one with
  `bytes-with-{width}-{endian}`; there is no `bytes-read-*` or `bytes-write-*`.
  See [ADR-0037](docs/adr/0037-collection-access-verbs.md), superseded for bytes by
  [ADR-0072](docs/adr/0072-bytes-element-access-verbs.md).
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
- **Character-set search is an opcode** (`string-index-any`), not a library helper: the
  per-character interpretation it replaces dominates its callers, and it is defined by a
  fixed specification rather than by a dialect.
  See [ADR-0067](docs/adr/0067-string-index-any-character-set-search.md).
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
