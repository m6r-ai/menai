# Menai

Menai is a pure functional programming language with Lisp-like S-expression syntax.
It is homoiconic, strictly typed, and side-effect free.  It has no I/O, no mutation,
and no access to the filesystem, network, or any other external state.

That last part is the whole point.  Menai is a computational language - you hand it
values, it returns a value, and nothing else changes.  There is no filesystem to
touch, no network to reach, and no state to corrupt.  Thsi means there's nothing to
sandbox and nothing to escape.  An AI can be given Menai and left to run it unsupervised,
which is a much stronger guarantee than "we tried to block the dangerous parts".

Menai doesn't do I/O itself, but it is designed to be embedded in software that does.
The host reads the file, queries the clock, or writes the result; Menai does the
computation in between.  This keeps the non-deterministic and stateful parts of a
system outside the language, where they can be permissioned and reviewed.

Menai started life inside [Humbug](https://github.com/m6r-ai/humbug), where it is used
for file and data processing, sorting, parsing, and mathematical reasoning.  It was
extracted into its own repository because it solves a fundamentally different problem
and has zero dependencies on Humbug.

## What you can do with it

Menai is not a toy.  The standard library in [`menai_modules/`](menai_modules/) is
written entirely in Menai, and it implements real binary and text formats:

| Module | What it does |
|--------|--------------|
| `json-decode` / `json-encode` | Read and write JSON |
| `bmp-decode` / `bmp-encode` | Read and write uncompressed 24-bit and 32-bit BMP images |
| `png-decode` / `png-encode` | Read and write non-interlaced 8-bit PNG images |
| `zip-entries` / `zip-extract` / `zip-create` | Read ZIP central directories, extract and decompress entries, and build archives |
| `zlib-compress` / `zlib-decompress` | Compress and decompress zlib streams (RFC 1950) |
| `deflate-compress` / `deflate-decompress` | Compress and decompress raw DEFLATE streams (RFC 1951) |

These are not bindings to libraries written in another language.  The whole idea is
to focus on making Menai fast enough to do these things natively.

## The language

Menai has a rich but strict type system.  Integers are arbitrary precision, and there
are floats, complex numbers, strings, booleans, symbols, bytes, and structs.  Menai has
a useful set of containers including lists, dictionaries, and sets.  It also has vectors
for index-heavy code where a list's O(n) random access is a bottleneck.

Everything is dynamically typed, but every low-level operation is strictly typed.
There is no implicit coercion: `integer+` adds integers and raises an error if you
hand it a float.  There are no overloaded operators.  This is deliberate and emphasizes
precision over convenience, with explicit clarity over brevity.  It turns out AIs generate
very robust code this way.

The language has lexical scoping, pattern matching with destructuring, a compile-time module
system, and tail-call optimisation.  It's very much like a pure functional scheme but without
any global state and with a more opinionated approach to operation naming.

Here is a program that counts word occurrences and returns the three most frequent
words, sorted by frequency:

```menai
(letrec
  ((count-words
    (lambda (words)
      (fold-list
        (lambda (acc word)
          (dict-set acc word
            (integer+ 1 (dict-get acc word 0))))
        (dict) words)))

   (top-words
    (lambda (text n)
      (let* ((words (string->list (string-downcase text) " "))
             (counts (count-words words))
             (pairs (sort-list
                       (lambda (a b) (integer>? (list-ref a 1) (list-ref b 1)))
                       (map-list (lambda (key)
                                   (list key (dict-get counts key)))
                                 (dict-keys counts)))))
        (list-slice pairs 0 n)))))

  (top-words "the quick brown fox jumps over the lazy dog the fox runs" 3))
```

This returns `(("the" 3) ("fox" 2) ("quick" 1))`.

The full language manual is in [`docs/`](docs/).  Start with
[`docs/index.md`](docs/index.md) for a table of contents and introduction.  The manual
is written for both human and AI readers.

## Compiler and runtime

Menai is compiled, not interpreted.  The pipeline goes through five internal
representations on the way to bytecode: abstract syntax tree, intermediate
representation, control-flow graph, virtual code, and finally bytecode.

Each representation makes a different class of optimisation natural.  Inlining and dead
binding elimination want a tree, branch constant propagation and phi collapsing want a
control-flow graph, slot allocation and peephole optimisation want a flat instruction
list, etc.  Each layer exists because trying to do its passes anywhere else would be
awkward, fragile, or fidelity-losing.

The bytecode runs on a register-based C VM with a pool allocator, reference counting,
and a closure cycle collector.  The VM has no process-global mutable state, so
multiple instances can run independently and safely.

Compilation is fast enough to do on demand.  Every optimisation has to justify its
cost: if a pass costs 10ms it needs to offer close to that as a saving in a single
runtime use, and if it costs 100ms it is probably not worth having.

The pipeline is authoritative in
[`src/menai/menai_compiler.py`](src/menai/menai_compiler.py) — it is always current
and should be read directly rather than reproduced in documentation.

## Tooling

Menai ships with a full set of command-line tools:

| Tool | Purpose |
|------|---------|
| `menai-eval` | Compile and run a `.menai` file, with optional compiler and VM profiling |
| `menai-test` | Discover and run `*.test.menai` suites |
| `menai-check` | Validate parenthesis balance and pinpoint mismatched parens |
| `menai-pretty-print` | Format Menai source |
| `menai-disassemble` | Print annotated bytecode disassembly |
| `menai-benchmark` | Run the performance benchmark suites |
| `menai-pipeline` | Run a JSON-defined pipeline of tool and Menai steps |

The profiling is worth noting.  `menai-eval`, `menai-benchmark`, and
`menai-pipeline` can all profile VM execution three ways: `--profile` ranks functions
by instructions executed, `--opcodes` counts opcode frequency, and `--annotate` shows
the annotated disassembly with each instruction's execution share.  That last one is
a `perf annotate` view for Menai.

`menai-check` exists because AIs have a peculiar weakness.  Much as they struggle to
count the "R"s in "strawberry", they struggle with long runs of parentheses and will
often write throwaway scripts to check their own work.  It is easier to give them a
reusable tool that does this.  Humans can use it too, but most find it easier to let
an editor highlight matching parentheses visually.

## Embedding Menai

Menai is designed to be embedded.  The interface is deliberately small —
`Menai`, `MenaiError`, `MenaiString`, `MenaiList`, and `MenaiValue` — and the language
has no idea who is hosting it.

The [`menai-pipeline`](src/menai_pipeline/README.md) tool demonstrates the pattern:
a JSON file describes a sequence of steps, some of which are I/O operations
(filesystem, clock, console) and some of which are Menai computations.  Values flow
between them, and because Menai is pure, adjacent Menai steps are automatically
collapsed and optimised together before execution.  The
[`examples/`](src/menai_pipeline/examples/) directory has working pipelines that read
BMP files, parse JSON, and combine clock readings with file contents.

## Designed with AI

Menai was designed with AI, and with the assumption that AI would be heavily used both
in implementing the language and in using it.  That means it does a few things
differently to a human-focused language.

The guiding question is always "what would you, as an AI, want in a language, as
opposed to what would a human want?"  The answer, repeatedly, was precision over
convenience.  Explicit `integer+`, `float+`, and `complex+` operators instead of one
overloaded `+`, no implicit coercion, proper lists only, etc.

The AI-facing language reference lives in
[`src/menai/menai_help.py`](src/menai/menai_help.py).  It is the single source of truth
for the help text shown to AI agents, fetched by the host rather than duplicated, so it
can never drift out of sync with the language.

## Getting started

### Installation

```bash
pip install menai
```

The C VM is compiled into the wheels, so `pip install menai` includes it
automatically — no separate build or download step is needed.  Wheels are
published for Linux, macOS, and Windows on x86_64 and ARM64, across Python
3.10–3.14.

### A first program

```menai
(let ((greet (lambda (name)
               (string-concat "Hello, " name))))
  (greet "World"))
```

Evaluates to `"Hello, World"`.

### Development

For local development, install in editable mode and build the C VM from source:

```bash
pip install -e ".[dev]"
make build
```

Requires a C compiler (gcc, clang, or MSVC).

### Running tests

```bash
make test
```

## Repository structure

```text
menai/
├── docs/                       # language manual and design records
├── menai_modules/              # standard library (.menai files)
├── pyproject.toml              # Python package configuration
├── setup.py                    # C VM extension build (platform-specific flags)
├── src/
│   ├── menai/                  # compiler core (lexer, parser, IR, CFG, bytecode, VM)
│   ├── menai_benchmark/        # performance benchmarking tool
│   ├── menai_check/            # parenthesis balance checker
│   ├── menai_disassemble/      # bytecode disassembler
│   ├── menai_eval/             # evaluator: compile, run, and profile a .menai file
│   ├── menai_pipeline/         # JSON-defined pipeline runner (tool + Menai steps)
│   ├── menai_pretty_print/     # code formatter
│   ├── menai_render/           # shared bytecode rendering and code-object walk
│   ├── menai_trace/            # VM instruction and call trace rendering
│   └── menai_test/             # test runner for *.test.menai files
└── tests/                      # compiler core tests
```

## Where to go next

- [`docs/index.md`](docs/index.md) — the language manual
- [`blueprint.md`](blueprint.md) — design philosophy and core principles
- [`docs/adr/`](docs/adr/) — the architecture decision records
- [`src/menai_pipeline/README.md`](src/menai_pipeline/README.md) — the pipeline runner
- [`src/menai_benchmark/README.md`](src/menai_benchmark/README.md) — the benchmark suites

## License

Apache License, Version 2.0. See [LICENSE.txt](LICENSE.txt).
