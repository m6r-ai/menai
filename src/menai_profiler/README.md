# Menai Profiler

Compiles and profiles Menai source files.  Two profiling modes are available:

- **cprofile** (default) — wraps execution in Python's `cProfile`.  Shows
  Python-level function call overhead (compiler, bridge, VM entry).  Useful
  for identifying hot Python-side code paths.

- **opcode** — counts per-opcode execution frequency inside the C VM and
  measures total wall-clock time.  Shows which bytecode opcodes dominate
  execution by count, overall instruction throughput, and average time per
  instruction.

## Usage

Run from the repository root with the virtual environment active:

```
python -m menai_profiler.profile <file.menai>                          # cProfile mode
python -m menai_profiler.profile <file.menai> --mode opcode            # opcode frequency mode
python -m menai_profiler.profile <file.menai> --output stats.prof      # save cProfile data
python -m menai_profiler.profile <file.menai> --top 50                 # show top 50 entries
python -m menai_profiler.profile <file.menai> --sort time              # sort cProfile by time
```

Profiling is always available — no special build flags are needed.  The
dispatch loop checks a runtime flag per instruction; when profiling is off
(the default), the cost is a single well-predicted branch.

## What opcode mode measures

Opcode mode counts how many times each bytecode opcode is executed.  This
reveals which opcodes are hot by frequency.  It also measures total
wall-clock time for the run (after a warm-up pass), giving instruction
throughput and average time per instruction.

Opcode mode does **not** attribute time to individual opcodes.  Per-opcode
time measurement via per-instruction timer reads is not feasible on current
hardware: the VM executes most opcodes in under 5 ns, while the cheapest
high-resolution timer read costs ~40–100 ns, making per-instruction timing
dominated by measurement overhead rather than actual work.

## Structure

```
menai_profiler/
├── __init__.py
├── profile.py              # CLI tool — compile, run, and profile
└── examples/
    ├── list-sort.menai          # simple sort-list lambda
    ├── sudoku-solver.menai      # sudoku solver
    ├── test-sudoku-solver.menai # test harness that runs the solver
    ├── rubiks_cube.menai        # Rubik's cube solver
    ├── test-rubiks-cube.menai   # test harness for the Rubik's solver
    ├── profile_sudoku_solver.py # standalone sudoku profiling script
    ├── profile_rubiks_cube.py   # standalone Rubik's profiling script
    └── profile_list_sort.py     # standalone list-sort profiling script
```

## Module path resolution

Module imports in the profiled file are resolved the same way as the
disassembler: the file's own directory first, then the current working
directory.