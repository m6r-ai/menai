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
python run.py --suite sort_list      # run only the sort_list suite
python run.py --suite sort_list --case n=1000  # run one case in a suite
python run.py --iterations 3         # override iteration count on every case
python run.py --opcodes              # opcode profiling (Menai only)
python run.py --opcodes --opcodes-top 20  # limit opcode output
python run.py --profile              # per-function profiling (Menai only)
python run.py --profile --profile-top 10  # limit profile output
python run.py --annotate             # annotated disassembly (Menai only)
python run.py --annotate --no-color  # disable colour in the reports
python run.py --annotate --color     # force colour when piping
python run.py --coverage             # bytecode coverage (Menai only)
python run.py --coverage --coverage-top 10  # limit coverage table output
python run.py --width 120            # clip report lines to 120 columns
```

`--suite` selects a single suite by exact name (case-insensitive); omit it to
run every suite.  `--case` selects a single case within that suite by exact
name (case-insensitive) and requires `--suite`.  A name that matches nothing
is an error listing the available names.

The per-function, annotated, and coverage reports are coloured when stdout is a
terminal.
Pass `--no-color` to disable it (for example when redirecting to a file), or
`--color` / `-c` to force it on even when stdout is not a terminal (for example
when piping into `less -R`).

Report lines are not clipped by default, so long case names, function labels,
and constants are shown in full.  `--width N` clips every report line to N
columns.

## Structure

```
benchmark/
├── benchmark.py          # Framework: BenchmarkSuite, BenchmarkRunner, BenchmarkReporter
├── run.py                # CLI entry point — discovers and runs suites
├── README.md
└── suites/
    ├── bmp_decode/
    │   └── suite.py          # BMP decoder benchmark suite
    ├── bmp_encode/
    │   └── suite.py          # BMP encoder benchmark suite
    ├── calendar/
    │   ├── suite.py          # Calendar arithmetic benchmark suite
    │   └── calendar.menai
    ├── deflate_compress/
    │   ├── suite.py          # DEFLATE compressor benchmark suite
    │   └── reference.py      # pure-Python reference compressor
    ├── deflate_decompress/
    │   ├── suite.py          # DEFLATE decompressor benchmark suite
    │   └── reference.py      # pure-Python reference decompressor
    ├── json_decode/
    │   └── suite.py          # JSON decoder benchmark suite
    ├── json_encode/
    │   └── suite.py          # JSON encoder benchmark suite
    ├── png_decode/
    │   └── suite.py          # PNG decoder benchmark suite
    ├── png_encode/
    │   └── suite.py          # PNG encoder benchmark suite
    ├── regexp/
    │   └── suite.py          # Regular-expression benchmark suite
    ├── rubiks_list/
    │   ├── suite.py          # Rubik's cube IDA* benchmark suite
    │   └── rubiks_cube.menai
    ├── rubiks_vector/
    │   ├── suite.py          # Rubik's cube IDA* benchmark suite (vector faces)
    │   └── rubiks-cube-vector.menai
    ├── sort_list/
    │   ├── suite.py          # List sort benchmark suite
    │   └── list-sort.menai
    ├── sort_vector/
    │   ├── suite.py          # Vector sort benchmark suite
    │   └── vector-sort.menai
    ├── sudoku_list/
    │   ├── suite.py          # Sudoku solver benchmark suite
    │   └── sudoku-solver.menai
    ├── sudoku_mrv/
    │   ├── suite.py          # Sudoku solver benchmark suite (most constrained cell first)
    │   ├── reference_solver.py  # Python reference solver for the sudoku_mrv suite
    │   └── sudoku-mrv.menai
    ├── sudoku_vector/
    │   ├── suite.py          # Sudoku solver benchmark suite (vector board)
    │   └── sudoku-vector-solver.menai
    ├── zip_create/
    │   └── suite.py          # ZIP archive writer benchmark suite
    ├── zip_entries/
    │   └── suite.py          # ZIP central-directory reader benchmark suite
    ├── zip_extract/
    │   └── suite.py          # ZIP archive extractor benchmark suite
    ├── zlib_compress/
    │   └── suite.py          # zlib stream compressor benchmark suite
    └── zlib_decompress/
        └── suite.py          # zlib stream benchmark suite
```

## What is benchmarked

Each suite benchmarks a single Menai implementation.  Correctness is covered by
the `*.test.menai` files (run via `menai-test`), so the benchmark suites are
timing-only.

## Reference implementations

A suite may additionally report a non-Menai implementation of the same work, in
adjacent columns and with a final `ref/menai` ratio.

The reference must perform the same work — the same algorithm and, where it can be
counted, the same count — but it must be the **fastest** implementation we know how
to write in the host language, not a transcription of Menai's data structures.  A
reference that is slower than it needs to be understates the host language and
flatters Menai.  Which representation wins is measured, never assumed: an in-place
Rubik's cube solver was written and measured 1.7x slower than the immutable one, so
it was discarded, while an in-place sudoku solver measured 2x faster and was kept.
Each suite records the variant it chose and the measurement that chose it.

Because the representation is free, the ratio compares *implementations*, not
virtual machines.  A gap can mean the VM is slower, or that the Menai module is
written in a shape that forfeits something the VM can do — and the number alone does
not say which.

Purity does not make the second impossible.  Menai has no mutation a program can
express, but the VM updates a collection in place whenever the update cannot be
observed (ADR-0063, extended by ADR-0064 and ADR-0065), so a module's data shape
decides whether it gets that path.  When a Python reference wins by mutating, check
the Menai module's shape before attributing the gap to the language.

Three suites carry one, all in pure Python:

| Suite | Reference | Equivalence check |
|-------|-----------|-------------------|
| `sudoku_mrv` | `reference_solver.py` in the suite directory | asserts its search node count equals the Menai figure from the opcode profile |
| `deflate-compress` | `suite directory/reference.py` | emits byte-identical output to the Menai module for every fixture and block mode |
| `deflate-decompress` | `suite directory/reference.py` | decodes every fixture to the same bytes as the Menai module |

A reference lives in the suite directory that uses it, beside that suite's
`suite.py`, since it describes the same benchmark and nothing else consumes it.

The deflate references were checked against the Menai modules when written; they
are not asserted on every run, because recompressing every fixture with Menai
outside the timed section would cost more than the benchmark.

A reference must not hand the work to a compiled library behind the host language's
builtin — that would measure the library, not the language.

Two asymmetries remain and neither is corrected for:

- The Menai figure is the VM's internal execution time, while a reference is timed
  with `time.perf_counter` and so includes host-language call overhead.  Do not
  read differences below a percent or so as meaningful.
- A host-language module builds its tables at import, outside the timed section.  A
  Menai module has no import-time state, so its equivalent is rebuilt on every
  execution and lands inside the timed section.  Where a suite's workload has such
  setup, the Menai time carries a fixed per-execution cost that the reference does
  not; `sudoku_mrv`'s docstring quotes it.

## Fixtures

Suites whose input is binary (image and archive formats) generate their fixture
bytes in memory from deterministic code in the suite module.  There are no
committed binary files: each fixture is a pure function of fixed constants, so
every machine and every run produces byte-identical inputs.  Generation is lazy
and memoised — a fixture is built the first time a case needs it, so running one
suite does not pay for another suite's fixtures.

Fixture bytes are passed to Menai as a bound `bytes` value rather than embedded
in the expression string, which keeps large binary blobs out of the compiled
source.

## Opcode profiling

The `--opcodes` flag enables VM-level opcode frequency profiling during the
timed runs.  After the timing report, an opcode frequency table is printed
for each Menai implementation and case, showing which bytecode opcodes
dominate execution by count and as a percentage of total instructions.

Profiling overhead is included in the measured times, so the timing numbers
reflect the real cost of running with profiling enabled.  This lets you
assess the profiling overhead by comparing a profiled run against an
unprofiled run.

Opcode profile data is produced by the Menai VM.

`--opcodes-top N` controls how many opcodes are shown per case (default: 40).

## Per-function profiling

The `--profile` flag enables VM-level per-function profiling during the timed
runs.  After the timing report, a per-function summary is printed for each Menai
case, ranking functions by the number of instructions they executed and showing
each function's share of the total instructions executed and its call count.
Where `--opcodes` shows which opcode *kinds* dominate, `--profile` shows which
functions dominate.

Profiling overhead is included in the measured times.  `--profile-top N` controls
how many functions are shown per case (default: 20).

## Annotated disassembly

The `--annotate` flag enables instruction tracing and prints the annotated
disassembly of every function for each Menai case, in disassembly order, with
each instruction's execution count and its percentage of the total instructions
executed.  This is the `perf annotate` view: the whole function body is visible
so a hot region can be read in context.  Each function header shows that
function's share of the total.  This is the instruction-level counterpart to
`--profile`.

Percentages are of instruction count, not time.

## Coverage

The `--coverage` flag enables instruction tracing and reports one cumulative
bytecode coverage report for the whole run, merged across every selected suite
and every case.  Counts are summed across a case's timed iterations and merged
across cases and suites, so a function reached by any scenario is covered.

The report shows the total distinct instructions across the merged functions,
how many executed at least once, a per-function table ordered with the least
covered functions first, and the instruction indices that never executed.
Functions are merged by source identity (file, line, and short name), so a
library function compiled into several benchmark programs is counted once rather
than once per program.  Module-root code objects have no stable source identity
across programs and are excluded.

`--coverage-top N` limits the per-function table; the unexecuted instruction list
always covers every function.

Coverage is measured over the bytecode that was compiled.  Code removed by an
optimisation pass before bytecode emission has no instructions in the tree and
is not counted.  Profiling overhead is included in the measured times, as with
the other VM instrumentation modes.

## Suites

### BMP Decode
Decodes uncompressed 24-bit and 32-bit BMP files.  Five cases cover bottom-up and
top-down row order, 24-bit and 32-bit pixels, and a width that requires row
padding.  Inputs are generated fixtures (see [Fixtures](#fixtures)).

### BMP Encode
Encodes decoded BMP descriptions.  Five of the eight cases use the same
geometries as the BMP Decode suite (bottom-up and top-down row order, 24-bit and
32-bit pixels, and a width that requires row padding), so the write and read
directions can be compared.  `1024x16` and `16x1024` hold the same number of
pixels as `128x128` and differ only in how they divide it between rows, which
makes them a canary for an encoder whose cost depends on the image's shape
rather than its pixel count.  `256x256` is there for the direction in which the
cost that grows is the image.  The containers are constructed as Menai
expressions rather than parsed from bytes; the literals fold to constants, so
the timing measures encoding alone.

### Calendar
Working-day date arithmetic over a 5-day calendar with holidays.  The Menai
inner loops dispatch through dense integer matches (day-name: 7 arms,
days-in-month: 12 arms).  Five cases advance a project start date by both a
working-day count (5–400) and a calendar-day span (5–600), so the days-in-month
table is executed on every day step across month and year boundaries.

### DEFLATE Compress
Compresses raw byte inputs using the default block encoding.  Four cases span
text that compresses well, incompressible data, and long runs.  Inputs are
generated fixtures (see [Fixtures](#fixtures)).

### DEFLATE Decompress
Decompresses raw DEFLATE streams.  Five cases cover text that compresses well,
incompressible data, long runs, and a stored-block stream.  Inputs are
generated fixtures (see [Fixtures](#fixtures)).

### DOCX Decode
Decodes DOCX packages: a ZIP of XML parts, read with `zip-extract` and parsed
with `xml-decode`.  Eight cases exercise the document part at 1 to 2000
paragraphs, a package with 100 styles, one with a nine-level numbering
definition, one with 16 media parts, and a full package combining all of them.
The timings are dominated by the XML parser, so they track the size of
`word/document.xml` rather than the package's compressed size.  Inputs are
generated fixtures (see [Fixtures](#fixtures)).

### DOCX Encode
Encodes DOCX value trees: serialising each part with `xml-encode` and building
the package with `zip-create`.  Seven cases mirror the DOCX Decode cases
(document size, styles, numbering, media, and a full package).  The value trees
are constructed as Menai expressions rather than parsed from bytes, so the
timing measures encoding alone.

### HTML Decode
Decodes HTML documents: a string parsed into the same element tree `xml-decode`
produces.  Nine cases cover the shapes that distinguish HTML from XML — a wide
sibling list, 200 levels of nesting, four attributes per element, mixed text and
child elements, 2000 paragraphs of named/decimal/hex character references, 1000
paragraphs dense with void elements (`br`, `img`, `input`), 1000 list items
relying on omitted end tags, 500 script and style elements whose content is raw
text, and a realistic page combining a doctype, head, comments, a table, and a
script.  These exercise the recovery paths (void elements, omitted end tags, raw
text) that the XML decoder never takes, so the timings are not comparable with
the XML Decode suite.  Inputs are generated fixtures (see [Fixtures](#fixtures)).

### HTML Encode
Encodes HTML value trees, writing each element with an explicit end tag and
escaping text and attribute values.  Seven cases mirror the HTML Decode shapes
(wide, deep, attributes, mixed, void elements, escaping, and declarations) so the
write and read directions can be compared.  The trees are constructed as Menai
expressions rather than parsed from bytes, so the timing measures encoding alone.

### JSON Decode
Decodes JSON strings of varying structure and size using a hand-written decoder
in Menai. Nine cases cover primitives (integer, float, booleans, null), strings
with escapes, empty collections, nested arrays, a long string (~2000 chars),
and a deeply nested array (500 levels).

### JSON Encode
Encodes Menai values of varying structure and size using a hand-written encoder
in Menai. Eight cases mirror the JSON Decode cases: an object, a flat array of
100 integers, a flat object of 50 pairs, a mixed nested structure, a
string-heavy object, a number-variety array, unicode strings, and a long string
(~2000 chars). The values are constructed directly rather than parsed from
JSON, so the timing measures encoding alone.

### PNG Decode
Decodes non-interlaced 8-bit PNG files.  Six cases cover the greyscale,
greyscale+alpha, palette, truecolour, and truecolour+alpha colour types at
sizes from 64×64 to 192×192, exercising zlib decompression, scanline filter
reversal, and per-pixel normalisation to RGB/RGBA.  Inputs are generated
fixtures (see [Fixtures](#fixtures)).

### PNG Encode
Encodes decoded PNG descriptions.  Three cases cover truecolour and
truecolour+alpha at 48x48 and 96x96.  The containers are constructed as Menai
expressions rather than parsed from bytes, so the timing measures encoding
alone.  Note that at these sizes most of the time is spent in the DEFLATE
compressor, which the encoder uses for the IDAT chunk.

### Regexp
Exercises the `regexp` standard library module.  Twelve cases span compiling a
complex pattern per line, a literal-prefix search over a log, a character-class
search over numbers, an alternation search over prose, every-match scans
(`search-all`) for a class and a literal prefix, capturing-group extraction
(`search-groups` and `search-all-groups`) from a log and from numbers, splitting
on runs of whitespace, replacing runs of whitespace, replacing a date with its
captured groups reordered, and a chained per-line workload that splits a log into
lines and searches each.  Inputs are generated fixtures (see
[Fixtures](#fixtures)).

### Rubik's Cube (list)
Solves scrambled Rubik's cubes using IDA* with a misplaced-stickers heuristic.
Seven scramble depths from 1 to 7 moves.
The `rubiks_vector` suite solves the same scrambles with the cube faces stored
as vectors instead of lists.

### Sort (list)
Sorts a list of random integers using `sort-list` in Menai.
Sizes: 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000 elements.

### Sort (vector)
Sorts the same sizes of random integers using `sort-vector` in Menai, so the
timings are directly comparable across the two suites.

### Sudoku (list)
Solves sudoku puzzles using a backtracking solver. Four difficulty levels:
easy (36 givens), medium (30), hard (25), expert (23).

### Sudoku (vector)
Solves the same four sudoku puzzles as the sudoku_list suite, but the board is a
vector of 9 row-vectors instead of a list of lists, so cell access is
`vector-nth` and a cell update is a `vector-with` copy. The puzzles and iteration
counts are shared with the sudoku_list suite, which makes the Menai timings directly
comparable across the two suites.

### ZIP Entries
Reads ZIP central-directory metadata.  Five fixtures (stored and deflate
entries, 16 mixed entries, 128 small entries, and a 256 KB entry) are each
decoded without decompressing any entry.  Inputs are generated fixtures
(see [Fixtures](#fixtures)).

### ZIP Extract
Extracts ZIP archives, decompressing every entry.  The five cases use the same
entry sets as the ZIP Entries suite, so the metadata-only and
decompression-inclusive directions can be compared.  Inputs are generated
fixtures (see [Fixtures](#fixtures)).

### ZIP Create
Builds ZIP archives from generated containers.  The five cases use the same
entry sets as the ZIP Entries and ZIP Extract suites (stored and deflate
entries, 16 mixed entries, 128 small entries, and a 256 KB entry), so the write
and read directions can be compared.  The containers are constructed as Menai
expressions rather than parsed from bytes, so the timing measures archive
construction alone.

### zlib Compress
Compresses byte inputs to zlib streams.  Four cases span text that compresses
well, incompressible data, and long runs.  Inputs are generated fixtures
(see [Fixtures](#fixtures)).

### zlib Decompress
Decompresses zlib streams.  Four cases span text that compresses well,
incompressible data, and long runs.  Inputs are generated fixtures
(see [Fixtures](#fixtures)).

## Adding a new suite

1. Create `suites/<name>/suite.py` containing a class named `Suite` that
   subclasses `BenchmarkSuite` from `benchmark`.
2. Implement `cases()` and `menai_program()`.
3. `menai_program()` returns a `MenaiProgram` describing the expression to
   benchmark.  Its `expression` is either a constant string or a callable that
   maps a case input to an expression string.  When the program reads binary
   input, set `fixture` to a callable that maps a case input to the bytes bound
   as the `inputs` dict's `"input-data"` member, and have the expression read it
   via `(dict-get inputs "input-data")`.  The framework compiles the expression
   (with any fixture bound) outside the timed loop and executes the pre-compiled
   bytecode inside it.
4. Non-standard `.menai` modules go in the suite directory.  Standard-library
   modules are resolved from the installed standard library and must not be copied into the
   suite, so that the benchmark always exercises the reference implementation.
5. For binary inputs, generate the fixture bytes in the suite module from
   deterministic code and return them from the `fixture` callable (see
   [Fixtures](#fixtures)).

The runner discovers suites automatically via `suites/*/suite.py`.
