# Menai

Menai is a pure functional programming language with Lisp-like S-expression syntax.
It is homoiconic, strictly typed, and has no side effects — no I/O, no mutation,
no access to the filesystem, network, or any external state.

## A language designed for AI

Menai was born inside [Humbug](https://github.com/m6r/humbug), an operating system
for human-AI collaboration. The problem was straightforward: AI agents needed to
perform computation — sorting, filtering, transforming data, parsing, mathematical
reasoning — but giving an AI the ability to execute arbitrary Python or shell commands
is dangerous. An AI that can run arbitrary code can delete files, exfiltrate data,
or cause other harm, which means every execution needs human approval.

Menai takes a different approach. By being pure and side-effect free, it requires no
sandboxing and no user approval to execute. There are no dangerous parts to escape
from because the language cannot touch anything outside itself. This lets AI agents
build and run complex algorithmic tools freely and safely, without interrupting a
human collaborator for approval on every execution.

Menai has since been extracted from Humbug into its own repository. It has zero
dependencies on Humbug and is consumed as an external dependency.

## Key characteristics

- **Pure functional** — no side effects, no mutation, immutable data
- **Homoiconic** — code and data share the same representation (S-expressions)
- **Strictly typed** — no implicit coercion between numeric types; each type has
  its own operators (e.g. `integer+`, `float*`, `complex/`)
- **Proper lists only** — no cons cells or improper lists
- **Tail call optimised** — recursive functions don't overflow the stack
- **Pattern matching** — declarative branching with destructuring
- **Module system** — write and import `.menai` files
- **Bytes type** — with multi-byte integer read/write (little/big-endian, LEB128)
- **Structs** — nominal typed records with functional updates

## Language manual

The full language manual is in [`docs/`](docs/). Start with
[`docs/index.md`](docs/index.md) for a table of contents and introduction.

The manual is written for both human and AI readers. It is precise, concrete, and
self-contained — no prior knowledge of Scheme or Lisp is assumed.

## Getting started

### Installation

```bash
pip install menai
```

The C VM is compiled into the wheels, so `pip install menai` includes it
automatically — no separate build or download step is needed. Wheels are
published for Linux, macOS, and Windows on x86_64 and ARM64, across Python
3.10–3.14.

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

### A first program

```menai
(let ((greet (lambda (name)
               (string-concat "Hello, " name))))
  (greet "World"))
```

Evaluates to `"Hello, World"`.

## Implementation

Menai is compiled through an optimizing pipeline (lexing → AST → semantic analysis →
module resolution → desugaring → constant folding → IR → IR optimisation → CFG →
CFG optimisation → VCode → bytecode) and executed by a register-based C VM.

The pipeline is authoritative in `src/menai/menai_compiler.py` — it is always current
and should be read directly rather than reproduced in documentation.

The reference implementation is in Python, but the language specification is
independent of Python. Future bindings (C, Rust, etc.) will implement the same
language against the same spec.

## Repository structure

```text
menai/
├── docs/                       # language manual
├── menai_modules/              # standard library (.menai files)
├── pyproject.toml              # Python package configuration
├── setup.py                    # C VM extension build (platform-specific flags)
├── src/
│   ├── menai/                  # compiler core (lexer, parser, IR, CFG, bytecode, VM)
│   ├── menai_benchmark/        # performance benchmarking tool
│   ├── menai_checker/          # parenthesis balance checker
│   ├── menai_disassembler/     # bytecode disassembler
│   ├── menai_pretty_print/     # code formatter
│   ├── menai_profiler/         # profiling tool
│   └── menai_test_runner/      # test runner for *_test.menai files
└── tests/                      # compiler core tests
```

## Design intent

See [`blueprint.md`](blueprint.md) for the design philosophy, core principles, and
architectural decisions behind Menai.

## License

Apache License, Version 2.0. See [LICENSE.txt](LICENSE.txt).
