# Menai Language Manual

## Introduction

Menai is a pure functional programming language with Lisp-like S-expression syntax.
It is homoiconic, strictly typed, and has no side effects. It was designed
specifically for use by AI agents — its purity means it requires no sandboxing and
no user approval to execute — but it is a real programming language that can be
learned and used by anyone.

This manual is the authoritative human-readable reference for the Menai language.
It is written to be clear, precise, and self-contained. No prior knowledge of
Scheme, Lisp, or any other language is assumed.

### How to read this manual

The sections are ordered so that each builds on the previous one. If you are new to
Menai, read them in order. If you are looking up a specific feature, use the table
of contents below to jump to the relevant section.

Every section includes concrete examples. Examples show both the Menai expression
and its result, like this:

```menai
(integer+ 1 2 3)
→ 6
```

### What Menai is not

- **Not a general-purpose scripting language.** Menai has no I/O, no file access,
  no network access. It is a computational language, not a systems language.
- **Not a Lisp dialect.** Menai is Lisp-inspired but deliberately different. It has
  strict typing, no `cond`, no cons cells, and no implicit numeric coercion. Do not
  assume that because it looks like Scheme it behaves like Scheme.

### Conventions used in this manual

- `→` shows the result of evaluating an expression
- `;` introduces a comment (same as in Menai source code)
- Names in `backticks` refer to language constructs, types, or functions
- Square brackets `[...]` in function signatures indicate optional arguments

## Table of contents

1. [Syntax](syntax.md) — S-expressions, atoms, literals, comments
2. [Types](types.md) — all Menai types and their literals
3. [Core forms](core_forms.md) — `let`, `let*`, `letrec`, `lambda`, `if`, `match`, `quote`, `and`, `or`, `error`, `import`
4. [Pattern matching](pattern_matching.md) — patterns, destructuring, predicates
5. [Builtins](builtins.md) — all builtin functions, organised by category
6. [Modules](modules.md) — the module system, `.menai` files, import semantics
7. [Examples](examples.md) — complete programs with explanation