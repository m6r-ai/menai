# Change log for Menai

## v0.8.0 (2026-10-05)

New features:

- Added a new "trivial phi elimination" operation inside the CFG collapse phi pass.
- Added a register dump capability to capture state when an error is raised inside
  the VM.
- Added a `csv-decode` module to the standard library.  It decodes RFC 4180 CSV
  text (as a string) to a vector of rows, each row a vector of field strings, so
  both rows and fields are reachable in O(1) by position.
- Added a `csv-encode` module to the standard library.  It encodes a vector of
  rows (each a vector of field strings) as RFC 4180 CSV text; it is the inverse
  of `csv-decode`.

Bug fixes:

- Updated the LICM pass to handle the nested loops introduced by the letrec inliner
  optimization.
- Fixed a hash reinitialization bug in the VM.
- Parenthesis errors now report the line and column to change rather than the line
  where the offending form opened.  A missing `)` is reported at its insertion
  point, and an extra `)` at the `)` that cannot be matched, naming any earlier `)`
  that closed a form before its body as the likely culprit.  Errors also include a
  per-line parenthesis depth table.
- A parse error no longer leaves the AST builder's unclosed-form stack populated,
  which previously inflated the depth reported for the next parse.
- Two top-level expressions are no longer misreported as a premature closing
  parenthesis.
- Fixed a miscompile in the CFG branch constant propagation pass.  It dropped a
  phi whose result fed a branch directly and was also used downstream, leaving
  the downstream use undefined.
- Fixed a miscompile in the interprocedural type analysis.  A call whose callee
  could not be resolved contributed no return-type information instead of an
  unknown type, so a function returning such a call was reported as returning
  only the types of its other return paths.
- Fixed a miscompile in the slot allocator.  A closure result coalesced into a
  slot it captured overwrote the captured value before it was patched, so the
  closure captured itself.
- Fixed a scope bug when desugaring a `let*` that binds an imported module.  A
  binding value that referenced a module member failed to compile because the
  module's bindings were not in scope for it.
- Fixed an IR dead binding elimination quirk.  An unreachable group of mutually-
  recursive bindings can now be eliminated.
- Fixed a VCode issue affecting tail recursive variadic functions.

Internal structure changes:

- Any opcode that has a register source operand has now been updated to take an
  optional constant operand too.  A new peephole pass merges constant loads into
  opcodes, giving benchmark gains typically around 4-5%.
- Test `tests` directory now accurately reflects the source tree.
- Removed the `menai-check` tool.  The compiler reports parenthesis errors more
  accurately, and editors highlight matching parentheses visually, so the tool no
  longer earned its place.

## v0.7.0 (2026-10-02)

New features:

- The standard library is now packaged with Menai and available in a wheel install.
  It lives in `src/menai/stdlib/` and can be read by an agent with
  `Menai.stdlib_source`.
- The module search path is now composed from explicit `--module-path` directories,
  the `MENAI_PATH` environment variable, and the source file's directory.
- Added `extract-entry` and `extract-matches` operations to `zip-extract`.
- Added `gzip-compress` and `gzip-decompress` modules to the standard library.
  `gzip-decompress` parses and skips the optional FEXTRA, FNAME, and FCOMMENT
  header fields, and verifies FHCRC when present.
- Added `tar-create`, `tar-entries`, and `tar-extract` modules to the standard library.
  The readers accept both the POSIX ustar and GNU tar formats, including GNU long
  names and PAX extended headers.

## v0.6.0 (2026-10-01)

New features:

- Added `menai-eval`, a tool that compiles and evaluates a `.menai` file (or an
  expression from stdin) and prints the result.  It can optionally profile the
  compiler with `--cprofile` and/or VM execution opcodes with `--opcodes`, and
  both may be combined.
- Removed `menai-profile` as `menai-eval` does everything it did and more.
- Added an approach for implementing negative tests in `menai-test`.
- Added `bmp-decode` and `bmp-encode` modules to read and write BMP image files.
- Added `deflate-compress` and `deflate-decompress` modules.  These compress and
  decompress using DEFLATE (RFC 1951) compression.
- Added `zip-create`, `zip-entries` and `zip-extract` modules.  These write/read a ZIP
  archive's central directory and can process stored and deflated entries.
- Added `zlib-compress` and `zlib-decompress` modules.  These compress and decompress
  a zlib stream (RFC 1950).
- Added `png-decode` and `png-encode` modules.  These read/write non-interlaced 8-bit
  PNG image files.
- Added a `json-encode` module to serialize JSON and renamed `json_parser` to
  `json-decode`.
- Added a `bytes-crc32` primitive.  This computes the CRC-32/ISO-HDLC checksum of
  a bytes value as an integer.
- Added floating-point bytes primitives: `bytes-read-f32-le`/`-be`,
  `bytes-read-f64-le`/`-be`, `bytes-append-f32-le`/`-be`, `bytes-append-f64-le`/`-be`,
  `bytes-write-f32-le`/`-be`, and `bytes-write-f64-le`/`-be`.  These mirror the
  multi-byte integer operations, encoding and decoding IEEE-754 values.
- Reworked the type propagation optimizations.  Removed the old implementation and added
  a new one based on interprocedural analysis.  This allows return types to be back
  propagated to callers and to remove type guards that are provably not necessary.
- Replaced the concept of the "prelude" functions being special global symbols and
  instead made them a letrec around the user's program.  This removes a number of
  idiosyncracies in the internal design.
- Improved the slot allocator so it eliminates more redundant `MOVE` opcodes in
  self-recursive loops.
- Added a loop rotation CFG pass.  A self-recursive loop with its test at the top
  is rotated so the test is evaluated at the bottom.
- Improved the constant type annotations in the disassembler.
- Added cryptographic hashing of `bytes` values: `bytes-hash-sha2-256`,
  `bytes-hash-sha2-512`, `bytes-hash-sha2-512-256` (FIPS Pub 180-4) and
  `bytes-hash-sha3-256` (FIPS Pub 202).  Each takes a `bytes` value and returns
  the raw digest as `bytes`.
- Added binary floating point read, append, and write operations for bytes.
- Added a peephole optimization that inlines an unconditional jump targeting a
  label immediately followed by a `RETURN`.
- Added the `RETURN_IF_FALSE` and `RETURN_IF_TRUE` opcodes and a peephole
  optimization that fuses a conditional jump leading directly to a `RETURN`
  into a single conditional-return instruction.  A `RETURN` block that becomes
  unreachable after the fusion is removed.
- Added a struct instance folding optimization.  A `struct-is-instance?` test
  whose receiver is proven to be a struct of exactly the tested type is folded
  to `#t` and its branch re-wired.
- Added a tracing/annotating profiler.  `menai-eval --annotate` renders every
  function's disassembly with per-instruction execution counts and shares.
- Added support for disassembling a module with `menai-disassemble`.
- Made constant folding able to create vectors.
- Added missing prelude functions.
- Improved compiler diagnostics and parsing error messages.
- Reworked the module system and introduced the `::` special form.
- Removed `struct-ref` and `struct-set-ref` from the language.
- Renamed the element access operations for consistency and to remove the mutation
  connotation of `set`, `add`, and `remove`.  Positional reads are now `-nth`
  (`string-nth`, `list-nth`, `bytes-nth`, `vector-nth`), keyed reads are `-get`
  (`dict-get`, `struct-get`), additions and replacements are `-with` (`vector-with`,
  `dict-with`, `struct-with`, `set-with`), and removals are `-without`
  (`set-without`, `dict-without`, `list-without`).  This replaces `string-ref`,
  `list-ref`, `bytes-ref`, `vector-ref`, `vector-set`, `dict-set`, `struct-set`,
  `set-add`, `set-remove`, `dict-remove`, and `list-remove`.
- Improved the inliner so it can inline `letrec`-containing bodies.
- Improved performance of integer bitwise VM operations.
- Improved the algorithmic performance of deflate, inflate and the Sudoku and
  Rubik's cube benchmarks.

Bug fixes:

- Fixed a slot allocation bug that could emit a branch on a register in the
  outgoing argument zone.
- Fixed a VM crash when `apply` is used with a large argument list.
  reserved slots corrupted memory.
- Fixed a VM stack overflow when freeing a long list.  The list finalizer
  released the tail recursively, using one C stack frame per element, so freeing
  a list of a few hundred thousand elements overflowed the C stack.  Long lists
  are now freed iteratively.
- Fixed a CFG bug where branch constant propagation could remove a phi node whose
  result was still used by a branch target.
- Fixed a CFG bug where dead capture elimination failed to remove orphaned
  `PATCH_CLOSURE` instructions.
- Dictionaries created with duplicate keys retained the first value, but should have
  retained the last one.
- Sets created with dynamic duplicate elements must not contain duplicates!
- Fixed a crash in the closure cycle collector when a dead closure was destroyed
  twice in one sweep.
- Fixed a problem where desugaring did not correctly honour shadowing of operation names.
- Struct type recognition is now lexically scoped.  Two struct types with the same
  name in different scopes are distinct, and a struct type is not visible outside the
  binder that declares it.
- Fixed a compiler register usage bug.
- Fixed several soundness bugs in the type analysis, including a phi type-fact
  bug and a struct field-access rewrite that could omit a required guard.
- Fixed desugaring of `vector-slice`.
- Fixed inliner shadowing and recursion-checker bugs.
- Fixed a VM crash from deep recursion.
- Fixed non-determinism in the compiler.
- Fixed a type guard problem and regressions in loop-invariant code motion,
  `MOVE` removal, loop rotation and type propagation.

Internal structure changes:

- Made the compiler purely functional.  Every phase is now a pure function over
  immutable values, and the AST, IR, CFG, VCode and bytecode models are
  immutable.  Each layer has an immutability test.
- Made the CFG an immutable value.  Terminators reference blocks by id,
  predecessors are derived rather than stored, and passes receive a context
  carrying cross-pass state.
- Reworked the CFG pass manager to run each pass to its own fixed point.
- Renamed the standard library modules to a `format-operation` convention.
- Removed the last elements of compiler global state.
- Added ADRs recording the inliner recursion rule, letrec-to-loop conversion,
  CFG immutability, compiler purity, function provenance through containers, and
  predicate folding over interprocedural facts.

## v0.5.0 (2026-09-14)

New features:

- Added a `vector` type to Menai.
- Added a loop-invariant-code-motion optimizer.
- Added a constant coalescing optimizer.
- Added a jump threading optimizer.
- Implemented performance improvements for some prelude functions.
- The `error` operation can now take any arbitrary Menai value, allowing for structured error returns.
- Runtime errors now generate a backtrace to make it easier to debug them.

Bug fixes:

- Unified `bytes-slice` so it matches the semantics of the other slice operations.

Internal structure changes:

- Reimplemented the bytecode validator in C rather than Python.  This always runs meaning we can remove runtime
  checks that are now covered by the validator.

## v0.4.0 (2026-09-08)

New features:

- Switched from a vector-like list representation to a cons-cell like representation inside the VM.  This wins up to 6x on
  the sort benchmark while being slightly positive on the JSON parser and slightly negative on rubiks and sudoku.  This does
  not change any visible aspect of the language surface, just performance.
- Added a new peephole opimization to reorder independent operations where this will reduce register pressure and allow
  `MOVE` opcodes to be eliminated.
- Improved closure creation behaviour to allow a greater set of registers and remove more unnecessary `MOVE` opcodes.
- Added fast-paths for many prelude operations where there are only 2 operands.
- Added some syntax detection rules into `menai-check` to improve pinpointing mismatched paren issues.
- Improved syntax checking in the compiler to pinpoint errors and provide better feedback to AI models.

Bug fixes:

- Fixed various help text and documentation issues related to an earlier tool renaming.
- Fixed problems with the VM not returning "no memory" errors.
- Fixed the `string-prefix` and `string-suffix` operations.

Internal structure changes:

- VM opcodes are now a dense set and the C defines are now auto-generated from the Python list, so the two can't get out of sync.

## v0.3.1 (2026-08-30)

Bug fixes:

- Fixed path problems in the `menai-pipeline` tool examples.

## v0.3.0 (2026-08-30)

New features:

- Added more floating point operations.
- Added `string->float` and `string->complex` operations.
- Improved performance of `string->integer`.
- Added a closure garbage collector so Menai can reclaim memory.
- Added a compile-time leak detector (`MENAI_DEBUG_LEAKS`) that tracks all MenaiValue allocations and
  reports any not freed at VM teardown.
- Added a `number->string` operation.
- Removed overly-conservative closure restriction for back-propagating move instructions.
- Added a new CFG dead capture elimination pass that removes captures that are eliminated by other CFG passes.

Bug fixes:

- Menai no longer leaks memory!
- Coallesced type guards that are the same (after propagation).

Internal structure changes:

- Added ADRs into the docs so design choices are visible.

## v0.2.0 (2026-08-10)

New features:

- Added VM opcode profiling support.
- Improved performance of a number of prelude functions by using a `list-append` operation rather
  than `list-prepend` followed by `list-reverse`.
- Improved type assertion removal pass.
- Added a simple function inliner.
- Added support for getting accurate VM timings when benchmarking, so we only measure execution time and not
  execution setup time.
- Improved the `menai-check` tool annotations so they show all closed parens.
- Improved register lifetime analysis to improve code generation.
- Added "names" output to disassembler output.

Bug fixes:

- Fixed a problem with dictionary comparisons.  The order of elements must not matter.
- Fixed several out-of-memory error handling issues.
- Fixed thread-safety issues.
- Fixed a type propagation error that was removing necessary type check opcodes.

Internal structure changes:

- Reworked a huge amount of the VM internals to regularize the implementations.

## v0.1.1 (2026-07-29)

Patch info:

- Updated the pyproject.toml information to provide more metadata on PyPI.

## v0.1.0 (2026-07-29)

This is the initial release as a stand-alone repo.  For earlier history please see the
humbug repo (https://github.com/m6r-ai/humbug).
