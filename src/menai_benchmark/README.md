# Menai Benchmark

Times Menai implementations across a set of algorithmic benchmarks.

Each suite records per-case mean and minimum execution times.  The minimum is
the more stable statistic and is the one to trust when comparing runs.  Because
the inputs are deterministic, the numbers are reproducible: run the suite now,
run it again later (on another machine, another OS, or another compiler/VM
version) and compare the two reports.

## Usage

Run from this directory with the virtual environment active:

```
python run.py                        # run all suites
python run.py --suite sort           # run only the sort suite
python run.py --suite sort --case n=1000  # run one case in a suite
python run.py --iterations 3         # override iteration count on every case
python run.py --profile              # opcode profiling (Menai only)
python run.py --profile --profile-top 20  # limit opcode output
python run.py --trace                # per-function tracing (Menai only)
python run.py --trace --trace-top 10 # limit trace output
python run.py --annotate             # annotated disassembly (Menai only)
```

`--suite` selects a single suite by exact name (case-insensitive); omit it to
run every suite.  `--case` selects a single case within that suite by exact
name (case-insensitive) and requires `--suite`.  A name that matches nothing
is an error listing the available names.

## Structure

```
benchmark/
├── benchmark.py          # Framework: BenchmarkSuite, BenchmarkRunner, BenchmarkReporter
├── run.py                # CLI entry point — discovers and runs suites
├── README.md
└── suites/
    ├── bmp-decode/
    │   ├── suite.py          # BMP decoder benchmark suite
    │   ├── generate_fixtures.py
    │   └── fixtures/         # committed .bmp inputs
    ├── calendar/
    │   ├── suite.py          # Calendar arithmetic benchmark suite
    │   └── calendar.menai
    ├── deflate-compress/
    │   ├── suite.py          # DEFLATE compressor benchmark suite
    │   ├── generate_fixtures.py
    │   └── fixtures/         # committed raw inputs
    ├── deflate-decompress/
    │   ├── suite.py          # DEFLATE decompressor benchmark suite
    │   ├── generate_fixtures.py
    │   └── fixtures/         # committed raw DEFLATE inputs
    ├── json-decode/
    │   └── suite.py          # JSON decoder benchmark suite
    ├── png-decode/
    │   ├── suite.py          # PNG decoder benchmark suite
    │   ├── generate_fixtures.py
    │   └── fixtures/         # committed .png inputs
    ├── rubiks_cube/
    │   ├── suite.py          # Rubik's cube IDA* benchmark suite
    │   └── rubiks_cube.menai
    ├── rubiks_vector/
    │   ├── suite.py          # Rubik's cube IDA* benchmark suite (vector faces)
    │   └── rubiks-cube-vector.menai
    ├── sort/
    │   ├── suite.py          # Sort benchmark suite
    │   └── list-sort.menai
    ├── sudoku/
    │   ├── suite.py          # Sudoku solver benchmark suite
    │   └── sudoku-solver.menai
    ├── sudoku_vector/
    │   ├── suite.py          # Sudoku solver benchmark suite (vector board)
    │   └── sudoku-vector-solver.menai
    ├── zip/
    │   ├── suite.py          # ZIP archive benchmark suite
    │   ├── generate_fixtures.py
    │   └── fixtures/         # committed .zip inputs
    └── zlib-decompress/
        ├── suite.py          # zlib stream benchmark suite
        ├── generate_fixtures.py
        └── fixtures/         # committed .zlib inputs
```

## What is benchmarked

Each suite benchmarks a single Menai implementation.  Correctness is covered by
the `*.test.menai` files (run via `menai-test`), so the benchmark suites are
timing-only.

## Fixtures

Suites whose input is binary (image and archive formats) read committed fixture
files from a `fixtures/` directory inside the suite.  The fixtures are produced
by a `generate_fixtures.py` script in the same directory and checked in, so every
machine and every run reads byte-identical inputs.  The generator is
deterministic — re-running it reproduces the committed files exactly.

Fixture bytes are passed to Menai as a bound `bytes` value rather than embedded
in the expression string, which keeps large binary blobs out of the compiled
source.

## Opcode profiling

The `--profile` flag enables VM-level opcode frequency profiling during the
timed runs.  After the timing report, an opcode frequency table is printed
for each Menai implementation and case, showing which bytecode opcodes
dominate execution by count and as a percentage of total instructions.

Profiling overhead is included in the measured times, so the timing numbers
reflect the real cost of running with profiling enabled.  This lets you
assess the profiling overhead by comparing a profiled run against an
unprofiled run.

Opcode profile data is produced by the Menai VM.

`--profile-top N` controls how many opcodes are shown per case (default: 40).

## Per-function tracing

The `--trace` flag enables VM-level tracing during the timed runs.  After the
timing report, a per-function summary is printed for each Menai case, ranking
functions by the number of instructions they executed and showing each
function's share of the total instructions executed and its call count.  Where
`--profile` shows which opcode *kinds* dominate, `--trace` shows which
functions dominate.

Tracing overhead is included in the measured times.  `--trace-top N` controls
how many functions are shown per case (default: 20).

## Annotated disassembly

The `--annotate` flag enables instruction tracing and prints the annotated
disassembly of every function for each Menai case, in disassembly order, with
each instruction's execution count and its percentage of the total instructions
executed.  This is the `perf annotate` view: the whole function body is visible
so a hot region can be read in context.  Each function header shows that
function's share of the total.  This is the instruction-level counterpart to
`--trace`.

Percentages are of instruction count, not time.

## Suites

### BMP Decode
Decodes uncompressed 24-bit and 32-bit BMP files.  Five cases cover bottom-up and
top-down row order, 24-bit and 32-bit pixels, and a width that requires row
padding.  Inputs are committed fixtures (see [Fixtures](#fixtures)).

### Calendar
Working-day date arithmetic over a 5-day calendar with holidays.  The Menai
inner loops dispatch through dense integer matches (day-name: 7 arms,
days-in-month: 12 arms).  Five cases advance a project start date by both a
working-day count (5–400) and a calendar-day span (5–600), so the days-in-month
table is executed on every day step across month and year boundaries.

### DEFLATE Compress
Compresses raw byte inputs using the default block encoding.  Four cases span
text that compresses well, incompressible data, and long runs.  Inputs are
committed fixtures (see [Fixtures](#fixtures)).

### DEFLATE Decompress
Decompresses raw DEFLATE streams.  Five cases cover text that compresses well,
incompressible data, long runs, and a stored-block stream.  Inputs are
committed fixtures (see [Fixtures](#fixtures)).

### JSON Decode
Decodes JSON strings of varying structure and size using a hand-written decoder
in Menai. Nine cases cover primitives (integer, float, booleans, null), strings
with escapes, empty collections, nested arrays, a long string (~2000 chars),
and a deeply nested array (500 levels).

### PNG Decode
Decodes non-interlaced 8-bit PNG files.  Six cases cover the greyscale,
greyscale+alpha, palette, truecolour, and truecolour+alpha colour types at
sizes from 64×64 to 192×192, exercising zlib decompression, scanline filter
reversal, and per-pixel normalisation to RGB/RGBA.  Inputs are committed
fixtures (see [Fixtures](#fixtures)).

### Rubik's Cube
Solves scrambled Rubik's cubes using IDA* with a misplaced-stickers heuristic.
Seven scramble depths from 1 to 7 moves.
The `rubiks_vector` suite solves the same scrambles with the cube faces stored
as vectors instead of lists.

### Sort
Sorts a list of random integers using `sort-list` in Menai.
Sizes: 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000 elements.

### Sudoku
Solves sudoku puzzles using a backtracking solver. Four difficulty levels:
easy (36 givens), medium (30), hard (25), expert (23).

### Sudoku (vector)
Solves the same four sudoku puzzles as the sudoku suite, but the board is a
vector of 9 row-vectors instead of a list of lists, so cell access is
`vector-ref` and a cell update is a `vector-set` copy. The puzzles and iteration
counts are shared with the sudoku suite, which makes the Menai timings directly
comparable across the two suites.

### ZIP
Reads and extracts ZIP archives.  Five fixtures (stored and deflate entries,
16 mixed entries, 128 small entries, and a 256 KB entry) are each run through
both `entries` (central-directory metadata only) and `extract` (which additionally
decompresses every entry), giving ten cases.  Inputs are committed fixtures
(see [Fixtures](#fixtures)).

### zlib Decompress
Decompresses zlib streams.  Four cases span text that compresses well,
incompressible data, and long runs.  Inputs are committed fixtures
(see [Fixtures](#fixtures)).

## Adding a new suite

1. Create `suites/<name>/suite.py` containing a class named `Suite` that
   subclasses `BenchmarkSuite` from `benchmark`.
2. Implement `cases()` and `implementation()`.
3. Non-standard `.menai` modules go in the suite directory.  Standard-library
   modules are resolved from `menai_modules/` and must not be copied into the
   suite, so that the benchmark always exercises the reference implementation.
4. For binary inputs, add a `generate_fixtures.py` script and a `fixtures/`
   directory, and commit the generated files (see [Fixtures](#fixtures)).

The runner discovers suites automatically via `suites/*/suite.py`.
