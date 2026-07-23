# Menai

Menai is a pure functional programming language with Lisp-like S-expression syntax,
designed primarily for use as an AI tool. It is homoiconic, strictly typed, and has
no side effects, making it safe for AI tool integration.

> **Language reference**: Menai is designed to be used by AIs. The authoritative language
> reference is the AI tool description, which any AI assistant in a system that embeds Menai
> can access directly. If you are a human user and want to know how a language feature works,
> just ask the AI.

## Features

- Pure functional: no side effects, immutable data
- Homoiconic: code and data share the same representation (S-expressions)
- Strict, runtime type system with no implicit coercion between numeric types
- Proper lists only (no cons cells or improper lists)
- Tail call optimization for recursive functions
- Pattern matching
- Dictionaries with O(1) lookup
- Bytes type with multi-byte integer read/write (little/big-endian, LEB128)
- Module system

## Architecture

Menai uses an optimizing compiler pipeline feeding into a bytecode VM:

```text
Source code
    │
    ▼
MenaiLexer                – tokenization
    │
    ▼
MenaiASTBuilder           – S-expression parsing to AST (MenaiASTNode)
    │
    ▼
MenaiASTSemanticAnalyzer  – type checking, scope analysis, free variable detection
    │
    ▼
MenaiASTModuleResolver    – resolve and inline imports (recursive module compilation)
    │
    ▼
MenaiASTDesugarer         – expand syntactic sugar (let, let*, letrec, quote, etc.)
    │
    ▼
MenaiASTConstantFolder    – constant folding optimization pass
    │
    ▼
MenaiIRBuilder            – lower AST to IR (MenaiIRNode)
    │
    ▼
MenaiIROptimizer          – IR-level dead binding elimination (fixed-point loop)
    │
    ▼
MenaiCFGBuilder           – lower IR to SSA control-flow graph (MenaiCFGFunction)
    │
    ▼
MenaiCFG passes           – CollapsePhiChains, BranchConstProp, SimplifyBlocks
    │
    ▼
MenaiVCodeBuilder         – lower CFG to linear VCode (phi-free, virtual registers)
    │
    ▼
MenaiBytecodeBuilder      – slot allocation, peephole optimisation, emit CodeObject
    │
    ▼
MenaiVM                   – register-based bytecode virtual machine
```

Modules are resolved and cached before optimization passes run, allowing
cross-module optimizations.

## Implementation

- Python 3.10+, no external dependencies
- All runtime values are immutable frozen dataclasses (`MenaiValue` hierarchy)
- Lists are backed by Python tuples
- Alists use a tuple of pairs with a hash-backed dict for O(1) lookup
- Tail calls are detected during compilation and optimized in the VM
- A C VM is available for performance (compiled from `src/menai/vm/*.c`)

## Python API

### Basic usage

```python
from menai import Menai

tool = Menai()
result = tool.evaluate("(integer+ 1 2 3)")  # Returns: MenaiValue
```

### Configuration

```python
tool = Menai(
    max_depth=200,                          # Maximum call stack depth
    module_path=[".", "menai_modules"],     # Module search path
)
```

### Module management

```python
tool.clear_module_cache()                   # Clear cached modules
tool.set_module_path([".", "my_modules"])   # Update search path (clears cache)
```

### Error handling

```python
from menai import Menai, MenaiError, MenaiParseError, MenaiEvalError

tool = Menai()
try:
    result = tool.evaluate("(integer+ 1 2")
except MenaiParseError as e:
    print(f"Parse error: {e}")
except MenaiEvalError as e:
    print(f"Eval error: {e}")
except MenaiError as e:
    print(f"Menai error: {e}")
```

### Value types

Runtime values are instances of the `MenaiValue` hierarchy:

| Class           | Menai type  |
|-----------------|-------------|
| `MenaiInteger`  | `integer`   |
| `MenaiFloat`    | `float`     |
| `MenaiComplex`  | `complex`   |
| `MenaiString`   | `string`    |
| `MenaiBoolean`  | `boolean`   |
| `MenaiSymbol`   | `symbol`    |
| `MenaiBytes`    | `bytes`     |
| `MenaiList`     | `list`      |
| `MenaiDict`     | `dict`      |
| `MenaiSet`      | `set`       |
| `MenaiFunction` | `function`  |

## Module system

Modules are `.menai` files containing a single expression that evaluates to a value
(typically an dict of exported functions). They are imported with `(import "module-name")`.

## Development tools

Menai includes several command-line tools for working with Menai source files:

- `menai-benchmark` – performance benchmarking (`src/menai_benchmark/`)
- `menai-check` – parenthesis balance checker (`src/menai_checker/`)
- `menai-disassemble` – bytecode disassembly (`src/menai_disassembler/`)
- `menai-pretty-print` – code formatting (`src/menai_pretty_print/`)
- `menai-profile` – profiling (`src/menai_profiler/`)
- `menai-test` – test runner for `*_test.menai` files (`src/menai_test_runner/`)

## Getting started

### Installation

```bash
pip install -e ".[dev]"
```

### Building the C VM

The C VM is optional but recommended for performance.

**Option A — Download a pre-built binary (recommended):**

Pre-built binaries are available via GitHub Releases for all supported platforms.

**Option B — Build from source:**

```bash
python setup.py build_ext --inplace
```

Requires a C compiler (gcc, clang, or MSVC).

### Running tests

```bash
python -m pytest tests/
```

## License

Apache License, Version 2.0. See [LICENSE.txt](LICENSE.txt).