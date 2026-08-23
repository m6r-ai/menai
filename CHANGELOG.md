# Change log for Menai

## v0.3.0 (2026-08-xx)

New features:

- Added more floating point operations.
- Added `string->float` and `string->complex` operations.
- Improved performance of `string->integer`.

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
