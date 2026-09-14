# Menai Evaluator

Compiles and evaluates a Menai source file, printing the result.  Optionally
profiles either the compilation pipeline, VM execution, or both.

A `.menai` file is a single expression, so evaluating it means compiling that
expression and printing the value it evaluates to.

## Usage

Run from the repository root with the virtual environment active:

```
python -m menai_eval.eval <file.menai>                       # evaluate and print the result
python -m menai_eval.eval -                                  # read the expression from stdin
python -m menai_eval.eval <file.menai> --cprofile            # profile the compiler
python -m menai_eval.eval <file.menai> --profile             # profile VM opcodes
python -m menai_eval.eval <file.menai> --cprofile --profile  # both
python -m menai_eval.eval <file.menai> --profile --top 50    # show top 50 entries
python -m menai_eval.eval <file.menai> --cprofile --sort time
python -m menai_eval.eval <file.menai> --cprofile --output stats.prof
python -m menai_eval.eval <file.menai> --raw                   # render string results literally
```

## Profiling modes

The two profiling modes are complementary and may be combined.

### `--cprofile` — compiler profile

Wraps the compilation pipeline in Python's `cProfile`.  Because the compiler
is pure Python, this gives full per-pass attribution across lexing, parsing,
semantic analysis, module resolution, desugaring, AST optimisation, IR
building, IR optimisation, CFG building, CFG optimisation, VCode building,
and bytecode emission.

Only the compilation pipeline is profiled.  Execution is performed separately
so that the single opaque C `execute` frame does not dominate the
cumulative-time table and bury the compiler passes beneath it.

`--output FILE` saves the raw profile data for later inspection with
`python -m pstats FILE` or `snakeviz FILE`.

### `--profile` — VM opcode profile

Counts how many times each bytecode opcode is executed inside the C VM and
measures total wall-clock time (after a warm-up pass).  Reports which opcodes
are hottest by frequency, overall instruction throughput, and average time
per instruction.

Opcode profiling does **not** attribute time to individual opcodes.  Per-opcode
time measurement via per-instruction timer reads is not feasible on current
hardware: the VM executes most opcodes in under 5 ns, while the cheapest
high-resolution timer read costs ~40–100 ns, making per-instruction timing
dominated by measurement overhead rather than actual work.

## Rendering string results

By default the result is rendered in its canonical Menai form via `describe()`,
which quotes and escapes string values.  A program that returns a formatted text
block therefore prints with literal `\n` escapes:

```
Result: "+-------+-------+-------+\n| 5 3 4 | 6 7 8 | 9 1 2 |\n..."
```

Pass `--raw` to render a string result as its literal contents instead, so that
embedded newlines and tabs appear as real characters:

```
+-------+-------+-------+
| 5 3 4 | 6 7 8 | 9 1 2 |
| 6 7 2 | 1 9 5 | 3 4 8 |
...
```

`--raw` only affects string results.  Non-string results (integers, lists,
dicts, and so on) are always rendered with `describe()`, since there is no
unquoted form to render.  The `Result: ` prefix is omitted in raw mode, and no
extra trailing newline is added when the string already ends in one.

## Output structure

Each section is delimited by a full-width separator.  A combined run produces:

```
──────────────────────────────────────────────────────────────────────
COMPILER PROFILE  (top 40 by cumulative)
──────────────────────────────────────────────────────────────────────
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
      ...  compiler pass frames ...

──────────────────────────────────────────────────────────────────────
VM OPCODE PROFILE  (top 40 by count)
──────────────────────────────────────────────────────────────────────
Opcode                                      Count   % of total
---------------------------------------- -------- ------------
LOAD_CONST                                 123,456         18.4%
...
---------------------------------------- -------- ------------
TOTAL                                      671,234

Wall-clock time:      12.345 ms
Instructions/sec:     54,372,109
Avg time/instruction: 18.4 ns

──────────────────────────────────────────────────────────────────────
EVALUATION
──────────────────────────────────────────────────────────────────────
Result: ((8 5 1 7 2 6 3 4 9) ...)
```

## Module path resolution

Module imports in the evaluated file are resolved the same way as the
disassembler: the file's own directory first, then the current working
directory.  When reading from stdin there is no file directory, so only the
current working directory is used.

## Structure

```
menai_eval/
├── __init__.py
├── eval.py                 # CLI tool — compile, evaluate, and profile
└── examples/
    ├── list-sort.menai          # simple sort-list lambda
    ├── sudoku-solver.menai      # sudoku solver
    ├── test-sudoku-solver.menai # test harness that runs the solver
    ├── rubiks_cube.menai        # Rubik's cube solver
    └── test-rubiks-cube.menai   # test harness for the Rubik's solver
```
