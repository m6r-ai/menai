# Blueprint: Menai

## What is Menai?

Menai is a pure functional programming language designed specifically for use by AI
agents. It is Lisp-inspired, homoiconic, strictly typed, and has no side effects.

## Why does it exist?

Programming languages were designed for human developers. While AIs are proficient
with most of these languages, they can be unsafe because they support potentially
dangerous I/O operations. An AI that can execute arbitrary Python or shell commands
can delete files, exfiltrate data, or cause other harm.

Menai takes a different approach: by being pure and side-effect free, it requires no
sandboxing and no user approval to execute. This lets AIs build and run complex
algorithmic tools freely and safely, without interrupting a human collaborator for
approval on every execution.

## Who is it for?

- **AI agents** that need to perform computation safely — sorting, filtering,
  transforming data, parsing, mathematical reasoning — without side effects.
- **Platforms that embed AI agents** (such as Humbug) that want to give their AIs
  a computational tool that doesn't require human-in-the-loop approval for every
  execution.
- **Language enthusiasts** interested in a modern, pure functional language with an
  optimizing compiler pipeline.

## Core principles

### Purity — no side effects

Menai has no I/O, no mutation, no state. Every expression evaluates to a value and
nothing else changes. This is the foundational design decision: it is what makes the
language safe for unsupervised AI execution. It also means dead code elimination is
always safe — any expression whose result is unused can be discarded unconditionally.

### Homoiconicity — code is data

Menai uses S-expression syntax where code and data share the same representation.
This makes it natural for AIs to generate, transform, and reason about Menai programs
programmatically.

### Strict numeric typing

There is no implicit coercion between `integer`, `float`, and `complex`. All
arithmetic operators are type-specific (e.g. `integer+`, `float*`). This prevents
a class of bugs that arise from silent coercion and makes the type system more
predictable for both AIs and humans.

### Safety by design, not by sandboxing

The safety model is not "we sandboxed the dangerous parts." There are no dangerous
parts. The language cannot touch the filesystem, the network, or any external state.
This is a stronger guarantee than sandboxing because there is no sandbox to escape.

### Performance matters

Menai is compiled through an optimizing pipeline (AST → IR → CFG → VCode → bytecode)
and executed by a register-based C VM. The Python implementation serves as a fallback
and reference, but the C VM is the primary execution engine. Optimisation passes
(constant folding, dead binding elimination, branch constant propagation, phi chain
collapsing, peephole optimisation) are an integral part of the design, not an
afterthought.

## Architecture

Menai uses an optimizing compiler pipeline feeding into a bytecode VM. The pipeline
is authoritative in `src/menai/menai_compiler.py` — it is always current and should
be read directly rather than reproduced in documentation.

The C VM (`menai_vm_c`) is compiled from C source and loaded at runtime. Pre-built
binaries for all supported platforms are published via GitHub Releases. A pure Python
fallback exists but the C VM is recommended for performance.

The C VM currently makes use of some Python runtime library functionality. The
long-term direction is to remove Python dependencies from the C code entirely,
leaving only the bridge layer between C and Python.

## Multi-binding future

The Python implementation is the reference implementation. The language specification
and design are independent of Python. Future bindings (C, Rust, etc.) will implement
the same language against the same spec. The repository structure is designed to
accommodate this without reorganisation.

## Relationship to Humbug

Menai was originally developed as part of Humbug, an operating system for human-AI
collaboration. It has been extracted into its own repository because it solves a
fundamentally different problem (a programming language vs. a collaboration platform)
and has zero dependencies on Humbug.

Humbug consumes Menai as an external dependency. The coupling is minimal: three
Humbug modules import from `menai`, and the interface is a small surface area
(`Menai`, `MenaiError`, `MenaiString`, `MenaiList`, `MenaiValue`).

Menai does not know Humbug exists.

## What Menai is NOT

- **Not a general-purpose scripting language.** It has no I/O, no file access, no
  network access. It is a computational language, not a systems language.
- **Not a Lisp dialect.** It is Lisp-inspired but deliberately different. It has
  strict typing, no `cond`, no cons cells, and no implicit numeric coercion. Do not
  assume that because it looks like Scheme it behaves like Scheme.
- **Not dependent on Python.** The reference implementation is in Python, but the
  language is independent of its implementation.