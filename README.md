# Menai

Menai is a pure functional programming language with Lisp-like S-expression syntax.
It is homoiconic, strictly typed, and side-effect free.  It has no I/O, no mutation,
no access to the filesystem, network, or any other external state.

While Menai doesn't support I/O operations, it is designed to be embedded into other
software that does.  It started as part of the [Humbug](https://github.com/m6r-ai/humbug)
AI project which uses Menai to perform file and data processing, sorting operations, apply
mathematical reasoning, etc., but does so within its AI tool framework that does the I/O
operations and handles permissioning.

The separation of concerns means we never have to worry about non-deterministic or stateful
behaviour within Menai itself, leaving the stateful activities residing elsewhere.

This repo contains a number of tools and examples that demonstrate this approach, including
a simple [pipeline runner](src/menai_pipeline_runner/README.md) that can chain I/O operations
implemented in Python with deterministic operations implemented in Menai.

As a pure functional language, Menai lends itself to being highly optimized.  The current
implementation compiles to a virtual machine bytecode but future versions will target highly
optimized native code.  The language design has also lent itself to very fast compilation, with
Menai code compiled on demand.

## Designed with AI

One of the more unusual features of Menai is that it was designed with AI, and with an assumption
that AI would be heavily used in both implementing the language and in using it.

As such a key question has always been "what would you, as an AI, want in a language, as opposed
to what would a human want?"  This means Menai prefers precision over convenience, and explicit
clarity over brevity that might make anything unclear.

An example of where this has had an impact are that there is no implicit type coercion.  If you have
an integer and want to use it in a floating point operation you must explicitly convert it.
Similarly, there are no overloaded operators, so where other languages might have an `+` operator,
Menai has explcit `integer+`, `float+`, and `complex+` operators.  It turns out AIs can generate
very robust code this way.

## Key characteristics

- **Pure functional** — no side effects, no mutation, immutable data
- **Homoiconic** — code and data share the same representation (S-expressions)
- **Strictly typed** — no implicit coercion between numeric types; each type has
  its own operators (e.g. `integer+`, `float*`, `complex/`)
- **Proper lists only** — no improper lists
- **Tail call optimised** — recursive functions don't overflow the stack
- **Pattern matching** — declarative branching with destructuring
- **Module system** — write and import `.menai` files
- **Atoms** - atoms for integers (arbitrary precision), floating point numbers, complex
  floating point numbers, strings, booleans
- **Bytes type** — with multi-byte integer read/write (little/big-endian, LEB128)
- **Structs** — nominal typed records with functional updates
- **Containers** - lists, dictionaries, sets
- **Compiled** - code is compiled with an optimizing compiler to a virtual machine bytecode

## An example

Here's an example that finds the occurrence of words in a string and then returns the 3 most
frequent words, sorted by frequency!

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

This returns `(("the" 3) ("fox" 2) ("quick" 1))`.  This is a dictionary output with the word as a key and the
number of occurences as the value.

## Language manual

The full language manual is in [`docs/`](docs/). Start with
[`docs/index.md`](docs/index.md) for a table of contents and introduction.

The manual is written for both human and AI readers.

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
