# ADR-0038: Standard library packaging and location

Date: 2026-10-01  
Status: Accepted

## Context

Menai ships a standard library of `.menai` modules — decoders, encoders,
compressors, and container readers — that implement capabilities the language
itself does not provide as primitives. These modules are part of the language's
offering: a user who installs Menai expects the standard library to be present.

Today it is not. The modules live in a `menai_modules/` directory at the
repository root, outside the Python package, and the packaging configuration does
not include them. There are two consequences:

- **A wheel install has no standard library.** `pip install menai` produces a
  working language with zero standard library modules. The modules exist only in a
  source checkout.
- **The tools locate them by guessing.** The test runner, pipeline runner, and
  benchmark runner each compute a repository-relative path to `menai_modules/` by
  walking up from the source tree. That path is only correct in a checkout and is
  never correct for an installed package. The evaluator does not reference the
  standard library at all.

The directory name is also misleading. It reads like a Python package but is not
one, and the `menai_` prefix is redundant in a repository where everything is
Menai.

A standard library module is consumed in two ways, and both matter:

- **The compiler loads it** to resolve an `import`, which requires the module's
  source text.
- **An agent reads it.** The standard library is written and consumed by AI
  agents. When an agent does not understand what a module does — the shape it
  returns, the arguments it accepts, the error it raises — it needs to read the
  module's actual source. A standard library that is present but opaque is only
  half-usable.

The prelude already establishes the pattern that solves both. `prelude.menai` is
a `.menai` source file shipped inside the `menai` package as package data and read
back at runtime through `importlib.resources`:

```python
from importlib.resources import files
return (files("menai") / "prelude.menai").read_text()
```

This works from a wheel and from a checkout, and it hands back the source text
rather than a compiled artefact.

## Decision

The standard library is packaged as **package data inside the `menai` package**,
under `src/menai/stdlib/`, and is loaded the same way as the prelude.

- Each module remains a `.menai` source file. Modules are **not** pre-compiled
  into bytecode for distribution: the source must ship, because an agent reads it
  to understand a module it is using. Menai compiles modules at import time
  regardless, so shipping source costs nothing at runtime.
- The modules are declared in `[tool.setuptools.package-data]` under the `menai`
  package, alongside `prelude.menai`, so they are included in every wheel.
- The repository-root `menai_modules/` directory is removed; the standard library
  lives only under `src/menai/stdlib/`.

The **library owns the standard library's location**, not the tools. The `Menai`
class exposes the standard library through two accessors:

- `stdlib_path() -> Path | None` — the on-disk directory containing the standard
  library modules, when the package is installed unpacked (the normal case for a
  wheel). Returns `None` when there is no filesystem location.
- `stdlib_source(module_name: str) -> str` — the source text of a standard library
  module, resolved through `importlib.resources`.

`stdlib_source` is the primitive: it works for every installation shape, because
`importlib.resources` can read a resource whether or not it corresponds to a real
file. `stdlib_path` is a convenience for an agent or tool that prefers to browse
the directory directly, and is explicitly allowed to return `None`.

The standard library is the **lowest-precedence** directory on the module search
path: a caller may place additional directories ahead of it, and the standard
library is the fallback that is always searched last.

## Alternatives considered

### Keep the modules at the repository root and copy them into the package at build time

This avoids moving the files in the source tree, but it introduces a second copy
of the same files and a build step that can drift. It also leaves the runtime
location problem unsolved: the tools would still need to be told where the copied
files landed, and a source checkout and an installed wheel would resolve the
standard library differently.

### Publish the standard library as a separate distribution

A separate `menai-stdlib` package would allow independent versioning, but it makes
the "install Menai and get a standard library" expectation worse rather than
better: there would be two things to install, and forgetting the second would
produce the same empty standard library the current situation produces. The
standard library is not independently useful and has no independent release
cadence, so a separate distribution adds cost without a corresponding benefit.

### Pre-compile the standard library to bytecode and ship only that

Shipping compiled modules would be smaller and would remove compile-on-import
cost, but it destroys the property that an agent can read a module it does not
understand. The source is the documentation. This is rejected outright.

### Expose only a filesystem path

A path-only interface is simpler but fails for any installation shape where the
package is not unpacked to a real directory (for example a zipped distribution).
`importlib.resources` is the mechanism that works in all cases, so the source-text
accessor is the primitive and the path is the convenience, not the reverse.

## Consequences

### Positive

- `pip install menai` delivers a working standard library. The modules are present,
  importable, and readable in every installation shape.
- The standard library's location is a single fact owned by the library, so the
  tools stop guessing and stop disagreeing with each other.
- An agent can read the source of any standard library module it is using, which is
  what makes a module usable when its behaviour is not obvious.
- The pattern is already proven: the standard library rides the same
  package-data-plus-`importlib.resources` rails as the prelude.

### Negative

- The repository-root `menai_modules/` directory disappears, so every reference to
  it must be swept: the tools, the tests, the benchmark and pipeline string
  literals, the documentation, and `AGENTS.md`. This is a repository-wide rename,
  not a file move.
- The evaluator's behaviour changes as a side effect: once the standard library is
  on the default search path it can import standard library modules, which it
  cannot do today. This is a fix, but it is a behaviour change and must be tested.
- The standard library is now inside the Python package, so the package grows and
  the source tree's top level is one directory smaller.
