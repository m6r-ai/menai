# Change log for Menai

## v0.6.0 (2026-09-xx)

New features:

- Added `menai-eval`, a tool that compiles and evaluates a `.menai` file (or an
  expression from stdin) and prints the result.  It can optionally profile the
  compiler with `--cprofile` and/or VM execution opcodes with `--profile`, and
  both may be combined.
- Removed `menai-profile` as `menai-eval` does everything it did and more.
- Added an approach for implementing negative tests in `menai-test`.
- Added a preliminary BMP parser.  This reads BMP image files.
- Added an `inflate` module.  This is a raw DEFLATE (RFC 1951) decompressor supporting
  stored, fixed Huffman, and dynamic Huffman blocks.
- Added a `deflate` module.  This is a raw DEFLATE (RFC 1951) compressor supporting
  stored, fixed Huffman, and dynamic Huffman blocks, with an optional mode argument
  selecting the encoding (defaulting to the smallest of the three).  It is the
  counterpart to the `inflate` module.
- Added a `zip_parser` module.  This reads a ZIP archive's central directory and can
  extract stored and deflate entries.
- Added a `zlib_parser` module.  This decompresses a zlib stream (RFC 1950),
  verifying the header check and the Adler-32 trailer.
- Added a `png_parser` module.  This reads non-interlaced 8-bit PNG image files,
  reversing the per-scanline filters.  All colour types are supported
  (greyscale, truecolour, palette, and the alpha variants), normalised to RGB
  or RGBA pixels.
- Reworked the type propagation optimizations.  Removed the old implementation and added
  a new one based on interprocedural analysis.  This allows return types to be back
  propagated to callers and to remove type guards that are provably not necessary.
- Replaced the concept of the "prelude" functions being special global symbols and
  instead made them a letrec around the user's program.  This removes a number of
  idiosyncracies in the internal design.
- Improved the slot allocator so it eliminates more redundant `MOVE` opcodes in
  self-recursive loops.  A loop-carried parameter update is now coalesced into the
  parameter slot even when a call sits between the value's definition and the
  back-edge move, and a single-use value that feeds a phi arm is written directly
  into the phi result's slot.  This removes the residual `MOVE` in loops such as
  `filter-vector`'s, where a predicate call and an `if` join previously forced the
  value through a scratch register.
- Improved the constant type annotations in the disassembler.
- Added cryptographic hashing of `bytes` values: `bytes-hash-sha2-256`,
  `bytes-hash-sha2-512`, `bytes-hash-sha2-512-256` (FIPS Pub 180-4) and
  `bytes-hash-sha3-256` (FIPS Pub 202).  Each takes a `bytes` value and returns
  the raw digest as `bytes`.

Bug fixes:

- Fixed a VM crash when `apply` is used with a large argument list.  The
  register file was sized from the callee's static local count rather than the
  runtime argument count, so applying a function to a list larger than the
  reserved slots corrupted memory.
- Fixed a VM stack overflow when freeing a long list.  The list finalizer
  released the tail recursively, using one C stack frame per element, so freeing
  a list of a few hundred thousand elements overflowed the C stack.  Long lists
  are now freed iteratively.
- Fixed a CFG bug where branch constant propagation could remove a phi node whose
  result was still used by a branch target.  When a phi feeding a type-predicate
  branch had one constant arm re-wired away and the sole remaining arm was
  non-constant, the phi was deleted even though a branch target still referenced
  it, leaving a use with no definition.
- Fixed a CFG bug where dead capture elimination failed to remove orphaned
  `PATCH_CLOSURE` instructions.  The set of dead captures was tracked per block,
  but a closure's `MAKE_CLOSURE` and its `PATCH_CLOSURE` can live in different
  blocks, so patches in later blocks were left behind.  When all sibling captures
  were dead the closure became a shared constant, and the leftover patch then
  mutated that constant.
- Dictionaries created with duplicate keys retained the first value, but should have
  retained the last one.
- Sets created with dynamic duplicate elements must not contain duplicates!
- Fixed a crash in the closure cycle collector when a dead closure was destroyed
  twice in one sweep.  Destroying one dead closure can destroy another, because a
  code object's constant pool can hold a closure and destroying that code object
  releases it.  The sweep then reached the already-destroyed closure's own entry and
  destroyed it again, releasing a freed code object.  A closure is now tagged as
  freed at its single destruction point, and the sweep skips entries already tagged.
- Fixed a problem where desugaring did not correctly honour shadowing of operation names.
- Struct type recognition is now lexically scoped.  Two struct types with the same
  name in different scopes are distinct, and a struct type is not visible outside the
  binder that declares it.  A struct type exported from a module and rebound by the
  importer is no longer usable as a destructuring pattern head; read its fields with
  `struct-get` or `struct-ref` instead.

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
