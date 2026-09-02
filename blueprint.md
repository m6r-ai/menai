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
and executed by a register-based C VM.  Optimisation passes (constant folding, dead
binding elimination, branch constant propagation, phi chain collapsing, peephole
optimisation) are an integral part of the design, not an afterthought.

### Why there are different representations

The compiler pipeline uses five distinct representations of the program — AST, IR,
CFG, VCode, and bytecode. Each layer exists because it makes specific analyses and
optimisations natural that would be awkward, fragile, or fidelity-losing in any
other layer.

**AST — source fidelity and validation.** The AST preserves the full surface
language, including constructs like `match`, `and`, `or`, and `letrec` that will
later be desugared. This is where semantic validation happens (arity checks, binding
structure, pattern validity) because the AST carries source locations and the
original syntactic structure needed for precise error messages. The AST constant
folder also runs here, folding literal expressions like `(integer+ 1 2)` → `3`
before the program is even lowered — this is the natural place for it because the
AST still carries the named builtins and literal types directly.

**IR — tree-shaped scope and inlining.** The IR is a symbolic expression tree where
variables are still named (not numbered) and scope is lexical. This is the only layer
where function inlining is practical: the tree structure makes it straightforward to
substitute a lambda body at a call site and rebind parameters, because scope is
still lexical names rather than SSA values or slots. The IR optimizer also does
dead binding elimination here — if a `let` binding's variable is never referenced,
the binding can be dropped — which relies on the tree structure to count uses per
scope. These transformations would be much harder on a flat CFG where scope has
been dissolved into SSA values and control-flow edges.

**CFG — control flow and dataflow.** The CFG lowers the tree into SSA form with
basic blocks, explicit control-flow edges, and phi nodes. This is the only
representation where branch constant propagation, phi chain collapsing, and block
simplification are possible. These passes need to reason about which predecessors
lead to which join points, which incoming phi values are statically known, and
which blocks are unreachable — questions that have no natural expression in a tree
IR. Type propagation and guard insertion also live here: the CFG's def-use chains
let the pass track which values have known types and where runtime type guards are
needed, which would require a separate dataflow analysis on top of the IR tree.

**VCode — linearisation and register allocation.** VCode is a flat instruction list
with labels and jumps replacing CFG blocks and edges, and phi nodes replaced by
explicit move instructions. This is where slot allocation happens: a linear scan
over the flat instruction list assigns virtual registers to concrete slots based
on per-definition lifetimes. The flat form is essential — liveness analysis on a
tree-structured IR or a graph-structured CFG would require a separate pass to
compute the same information that falls out naturally from the linear order. The
peephole optimizer also runs here, pattern-matching adjacent instructions (redundant
move elimination, jump-over-jump folding, conditional-branch/load-const/return
folding) — patterns that only become visible when instructions are laid out
sequentially.

**Bytecode — the execution format.** The final bytecode is a compact integer
array of opcodes and operands consumed by the register-based C VM. It has no
structure beyond the instruction sequence — no blocks, no phi nodes, no labels
(resolved to indices), no type annotations. Every optimisation has already
happened; the bytecode emitter just translates VCode to opcodes and resolves
label references to instruction indices. This layer exists to be fast to decode
and execute, not to be analysed.

The key insight is that each representation is shaped to make its associated
optimisations obvious. Trying to do branch constant propagation on a tree IR
would require reconstructing the control-flow graph implicitly. Trying to do
inlining on a CFG would require rebuilding lexical scope that has already been
dissolved. Each layer's data structure is the minimal one that makes its passes
natural, and the lowering cost between layers is modest because each lowering
step is a straightforward structural translation.

## Architecture

Menai uses an optimizing compiler pipeline feeding into a bytecode VM. The pipeline
is authoritative in `src/menai/menai_compiler.py` — it is always current and should
be read directly rather than reproduced in documentation.

The C VM (`menai_vm_c`) is compiled from C source and bundled inside platform-specific
wheels built with cibuildwheel. Wheels for all supported platforms (5 Python versions ×
5 OS/arch combos) are published to PyPI on version tags. `pip install menai` selects the
correct wheel automatically — no runtime binary download is needed.

The C VM currently only uses Python runtime library functionality within the bridge layer
between C and Python.

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
  strict typing, no `cond`, no `cons` operation or improper lists in the surface
  language, and no implicit numeric coercion. Do not
  assume that because it looks like Scheme it behaves like Scheme.
- **Not dependent on Python.** The reference implementation is in Python, but the
  language is independent of its implementation.
