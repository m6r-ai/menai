# ADR-0040: Module search path and application libraries

Date: 2026-10-01  
Status: Accepted

## Context

Menai resolves an `(import "name")` at compile time by searching a list of
directories for `"<name>.menai"`. The loader (`Menai.resolve_module`) already
supports this: it takes a list of directories and returns the first match, so the
mechanism is multi-path by design and needs no change to support more than one
library location.

What is missing is a *model* for what the directories are and how they compose.

The language has three sources of reusable code, and they should be distinct
layers:

- **The prelude** — the language's own functions, spliced into every program
  lexically by the prelude injector. It is not on the search path and is not a
  module; it is part of the language.
- **The standard library** — Menai's own modules, packaged with the language
  (ADR-0038).
- **Application libraries** — reusable modules that humans and agents write for
  their own tasks. These are the point of a module system: a user should be able
  to write a reusable module and have it found by name, exactly as a standard
  library module is.

Today the third layer has no home. There is no convention for where an
application library lives, no environment variable, and no flag. Each tool
independently constructs a search path, and none of them lets a user extend it:

- The evaluator and disassembler search the source file's directory and the
  current working directory.
- The test runner searches its own directory, the test file's directory, and the
  standard library.
- The pipeline runner and benchmark runner search their working directory and the
  standard library.

The result is that a user cannot point any tool at a directory of their own
modules, and the composition of the search path differs from tool to tool. The
standard library is not even present in the evaluator's path (ADR-0038 fixes that).

Two further questions are settled here because they are properties of the composed
path, not of any single directory:

- **Precedence.** With multiple directories and first-match-wins resolution, the
  order decides which module an import resolves to. The order must be defined and
  stable, not incidental to how each tool happens to build its list.
- **Shadowing.** Because module names are predictable (`<format>-<operation>`, see
  [ADR-0039](0039-standard-library-module-naming.md)), an application library can
  easily define a module whose name collides with a standard library module. Whether
  that is an override or an error is a decision.

## Decision

The module search path is composed of **three layers, in precedence order**, and
is constructed by the library rather than by each tool.

From highest precedence to lowest:

1. **Explicit directories** supplied by the caller — a `module_path` argument to
   `Menai`, or a `--module-path` flag on a tool.
2. **Application library directories** — discovered from the `MENAI_PATH`
   environment variable and from the source file's own directory.
3. **The standard library** — the location defined by ADR-0038, always searched
   last.

Resolution is first-match-wins in this order: the first directory containing
`"<name>.menai"` wins.

**Application libraries may shadow standard library modules.** Shadowing is
permitted deliberately, not tolerated accidentally. An application library that
defines `json-decode` overrides the standard library's `json-decode` for that
program. This is the same override mechanism a user needs to replace a standard
library module with a specialised or corrected version, and it is consistent with
Menai's lexical shadowing everywhere else: an inner binding shadows an outer one.
The standard library is the outermost binding.

Shadowing is resolved by search order, so the standard library is never consulted
for a name an application library provides.

**`MENAI_PATH` is a colon-separated list of directories**, like `PATH` and
`PYTHONPATH`. Empty segments are ignored. It is the mechanism by which a host
platform (such as Humbug) or a user injects application library directories
without editing every tool invocation. It composes: a host sets it once and every
Menai tool honours it.

**Every tool exposes a repeatable `--module-path DIR` flag.** Each occurrence
prepends a directory to the search path, ahead of the application layer and the
standard library. A tool that is given no flag still uses the full default
composition, so the standard library and `MENAI_PATH` are always available. The
tools are:

- `menai-eval`
- `menai-disassemble`
- `menai-test`
- `menai-pipeline`
- `menai-benchmark`

The source file's own directory remains part of the application layer, so a bare
import next to the file being run resolves without configuration.

The library owns path construction. A single method composes the default path from
the layers above, and the tools call it rather than building their own lists. This
is what makes the precedence identical across every tool and keeps the standard
library present everywhere.

## Alternatives considered

### Leave path construction to each tool

This is the status quo. It produces a different search path per tool, omits the
standard library from some tools, and gives a user no way to extend any of them.
It cannot express the three-layer model, so it is not viable.

### Forbid shadowing of standard library modules

Reserving standard library names would make collisions an error rather than an
override. This is defensible — accidental shadowing of `json-decode` is a real
hazard — but it removes the ability to override a standard library module
deliberately, which is exactly the capability a user needs when a standard library
module is unsuitable for their task. Menai's model is lexical shadowing
throughout; making the standard library the one unshadowable layer would be
inconsistent with that model. Shadowing is therefore allowed and documented, so
the hazard is visible rather than hidden.

### A configuration file instead of an environment variable

A per-project configuration file would be discoverable and explicit, but it
introduces a file format, a discovery rule for the file itself, and a new class of
error when the file is malformed or misplaced. The environment variable is the
established, composable mechanism for exactly this purpose and requires no new
format. A configuration file can be added later if a concrete need appears; the
environment variable is sufficient now.

### A fixed application library directory name

A single conventional directory (for example `modules/` next to the source file)
would give application libraries a home without configuration, but it fixes the
location to one place and does not compose: a host with libraries in several
locations could not express that. The source file's directory already provides the
"next to the file" case; `MENAI_PATH` provides the general case.

### Environment variable only, no flag

The environment variable handles the host and the persistent case, but a one-off
invocation — "run this file with these modules" — should not require setting an
environment variable. The flag is the direct, local mechanism and is cheap to
provide. Both are offered.

## Consequences

### Positive

- Application libraries have a defined home and are found by name, so a human or
  agent can write a reusable module and import it without special configuration.
- The three layers are explicit and their precedence is stable across every tool,
  so an import resolves to the same module regardless of which tool runs it.
- The standard library is present on every tool's path, including the evaluator,
  which cannot import standard library modules today.
- Shadowing is a deliberate, documented override rather than an accident, and it
  is consistent with lexical shadowing elsewhere in the language.
- A host platform can inject application libraries once through `MENAI_PATH`,
  without coordinating with each tool.

### Negative

- The evaluator's and disassembler's search paths change: they gain the standard
  library and the application layer, and they gain the flag. Their existing
  behaviour of searching the file's directory and the working directory is
  preserved, but the composed path is now larger.
- Every tool gains a flag and must be updated to use the library's path
  construction rather than its own. The tools must not keep building their own
  lists, or the precedence guarantee is lost.
- Shadowing means a standard library module can be silently replaced by an
  application library of the same name. The precedence is defined and documented
  so this is predictable, but it is a real hazard and users should be aware of it.
- `MENAI_PATH` is process-global state read from the environment. It is read once
  when a path is constructed, not consulted during resolution, so it does not
  introduce mutable global state into the compiler; but it does make the search
  path depend on the environment, which a user must account for when reproducing a
  result.
