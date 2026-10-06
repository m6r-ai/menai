# ADR-0041: Standard library module taxonomy

Date: 2026-10-06  
Status: Accepted

## Context

The standard library is written and consumed by AI agents. An agent does not browse a
directory tree; it searches for a capability, finds a module, and calls it. The
properties that make a module usable are therefore not the properties that make a
library pleasant for a human to navigate. What matters is:

- the module is **findable** by the term an agent would search for;
- the module name **states the capability**, so an agent can use it without opening it;
- the naming makes a module's **counterpart predictable**, so an agent that finds one
  operation can name the inverse operation without searching for it.

The standard library had grown to a set of modules whose names were chosen ad hoc
(`json_parser`, and so on). Without a convention, an agent cannot predict what a module
is called from the capability it wants, and cannot predict the name of the inverse of an
operation it has found.

[ADR-0039](0039-standard-library-module-naming.md) established a convention to fix this:
a module does one thing, its name is `<format>-<operation>`, and it exports `<operation>`.
That model works for a codec or a container reader, where the module *is* one operation.

It does not work for a **capability** that is not a single operation. A regular-expression
engine is the first such case: it is a pattern compiler, a matcher, and a family of
search, split, and replace operations, all built on a pattern dialect that is a Menai
design decision. There is no single `<operation>` that names it, and forcing it into the
`<format>-<operation>` shape would either split the capability across several modules
(so an agent that finds one operation cannot find its siblings) or pick one operation to
stand for the whole (so the module name lies about what the module does).

ADR-0039 foresaw this only partially. It named a second kind of module — shared-data
modules such as `deflate-tables` — but treated "not an operation" as a narrow exception
for constant tables. A capability module is a third kind, and it is not an exception: it
is a legitimate module whose unit is a capability rather than an operation.

This ADR replaces ADR-0039's model with one that covers all three kinds, and supersedes
ADR-0039.

## Decision

### Three kinds of standard library module

A standard library module is one of three kinds. The kind determines how the module is
named and what it exports.

**Operation modules** do one thing. The name is `<format>-<operation>`:

- `json-decode` — decode JSON.
- `json-encode` — encode JSON.
- `deflate-compress` — compress DEFLATE.
- `deflate-decompress` — decompress DEFLATE.

The unit is the operation, not the format. A format with two directions is two modules,
not one module with two exports. An agent searches for the operation it wants to perform,
so the operation belongs in the name it searches for. Grouping a format's operations into
a single module would put the search term in the export rather than the name, and would
force the agent to open the module to discover what it does.

Pairing is by name, not by structure. `json-decode` and `json-encode` are a pair because
their names say so. They are not required to live together, and no module exists whose
only purpose is to group them.

**Shared-data modules** hold a specification's constant data that several operation
modules need. They are named for what they hold, not for an operation. `deflate-tables`
(the RFC 1951 constant tables, used by both `deflate-compress` and `deflate-decompress`)
is the example. A data module is not an operation and is not named as one.

**Capability modules** provide a whole capability that is not a single operation. They
are named for the capability, and they export a family of operations that the capability
comprises. `regexp` is the example: it exports the compiled-regexp type and the compile,
search, split, and replace operations that make up regular-expression matching.

A capability module is not a bundle of unrelated operations. It is a single coherent
capability whose parts are not individually useful — a compiled regexp with no search
operation does nothing, and a search operation with no way to compile a pattern does
nothing. The parts belong together because the capability is the unit.

### Choosing a kind

The kind follows from what the module is, not from a preference:

- If the module is one operation on one format, it is an **operation module**.
- If the module is constant data several modules share, it is a **shared-data module**.
- If the module is a capability made of several operations that only make sense together,
  it is a **capability module**.

The default is the operation module. A capability module is chosen only when the
capability genuinely is not one operation; if a module can be named `<format>-<operation>`
without lying, it is an operation module. This keeps the common case searchable by
operation, which is what an agent does.

### Inverse operations are named as a symmetric pair

Operations that are true inverses are named as a symmetric pair and carry a round-trip
guarantee. Operations that are not inverses are named individually, and no symmetry is
implied.

Symmetry in naming is earned by an underlying inverse relationship, not asserted for
tidiness. If two operations are inverses, the naming makes that visible and the
implementation is held to it. If they are not inverses, a symmetric name would be a lie
about the design.

The round-trip guarantee is what makes the symmetry meaningful:

- `decode` / `encode` — `decode(encode(v))` reproduces `v` for every value `encode`
  accepts.
- `compress` / `decompress` — `decompress(compress(b))` reproduces `b` for every `bytes`
  `b`.

A symmetric pair is therefore a testable contract, not just a naming convention: the
round-trip is a property that must hold and must be tested.

The guarantee applies whenever both halves of a pair exist. A module whose inverse has
not been written yet carries no round-trip obligation, and the name alone does not claim
one — the obligation arises from the pair, not from the single name.

### The operation vocabulary

The operation part of an operation module's name is drawn from a small, fixed vocabulary,
so that the inverse of an operation is always predictable:

| Operation | Inverse | Meaning |
|-----------|---------|---------|
| `decode` | `encode` | value ↔ `bytes`/`string` representation |
| `compress` | `decompress` | byte stream ↔ compressed byte stream |
| `entries` | — | read a container and return metadata about its contents |
| `extract` | — | read a container and also unpack its contents |
| `create` | — | build a container |

`entries`, `extract`, and `create` are container operations and are **not** a symmetric
set. They are named individually because they are not inverses:

- `entries` reads a container and returns metadata about its contents.
- `extract` is defined in terms of `entries` — it reads the container and additionally
  unpacks the contents. It is a derived read, not a peer of `entries`.
- `create` builds a container. Its relationship to the reads is a **content round-trip**,
  not a byte round-trip: reading back a container produced by `create` recovers the
  logical entries that were put in, not a byte-identical container.

The container shape is deliberately asymmetric because the operations are asymmetric. The
use pattern is to read a container's entries, decide what is worth investigating, and
extract that — a read-then-read flow, with `create` as the separate write side.

### Exports

An operation module exports its operation under the same name as the operation in its
module name. `json-decode` exports `decode`; `deflate-compress` exports `compress`. The
export name is therefore redundant with the module name, and that redundancy is accepted:
the export name is predictable from the module name, and it reads naturally at the call
site (`(:: json-decode decode)`).

A capability module exports the members of the capability under their bare operation
names, with no module-name prefix. `regexp` exports `compile`, `search`, `search?`,
`search-all`, `split`, and `replace`, plus the compiled-regexp type. The module name
supplies the capability, so repeating it in each export would be redundant: the call site
is `(:: regexp search)`, not `(:: regexp regexp-search)`. This is the same principle as
the operation module's redundant export name, applied to a capability instead of an
operation.

### Naming constraints

A module's own bindings shadow same-named builtins and prelude functions throughout its
body, so a module and export name must avoid the names of the builtins and prelude
functions the module itself uses. This is a general constraint on the whole vocabulary,
not a special case. See
[ADR-0026](0026-single-builtin-table.md) and the corresponding invariant in `AGENTS.md`.

This constraint is why the container read operation is `entries` and not `list`: `list`
is the natural word, but it is a prelude function the ZIP reader needs, so it is unusable
as an export name.

## Alternatives considered

### Keep ADR-0039 and treat a capability module as an exception

This was the status quo. It leaves the model describing two kinds of module and treating
anything else as an anomaly, which does not scale: a second capability module would be a
second exception, and the model would not tell an author which shape to choose. Making the
capability module a named kind, with a rule for when to choose it, keeps the model total.

### Split a capability into one module per operation

A `regexp-compile`, `regexp-search`, `regexp-split`, and `regexp-replace` module would fit
the `<format>-<operation>` shape exactly. It is rejected because the parts are not
independently useful: they share the compiled-regexp type and the pattern dialect, and a
user needs several of them together to do anything. Splitting them forces the agent to
find and import every part, and it puts the shared type in an arbitrary one of them. The
capability is the unit, so the module is the unit.

### One module per format, with all operations as exports

This is the shape ADR-0039 rejected for codecs, and it is still wrong for them: an agent
searches for an operation, so the operation belongs in the name. It is not what a
capability module does either — a capability module is named for a capability, not a
format, and its exports are the parts of that capability, not the two directions of one
operation.

### Why `decode` / `encode` rather than `parse` / `encode`

`parse` and `encode` are not antonyms. An agent that sees `parse` does not predict
`encode`. `decode` and `encode` are a recognisable inverse pair, so the naming carries
the relationship. They are also format-neutral and have no spelling variants.

### Why not `read` / `write`

`read` and `write` are clear antonyms, but they carry an I/O connotation. Menai has no
I/O — a module receives a value and returns a value — so `read` and `write` would suggest
a file handle or stream that does not exist.

### Why not `parse` / `serialise`

`serialise` is the precise term for the inverse of `parse`, but the two do not name two
directions of one relationship; they name two different relationships. `serialise` also
has a common spelling variant (`serialize`), which is a hazard when the code is
AI-generated and the spelling may be chosen inconsistently.

### Why not the RFC's own terms (`inflate` / `deflate`) as operation names

`inflate` and `deflate` are the correct terms for the two directions of DEFLATE, and they
are a real inverse pair. But they are the only format whose terms are not a recognisable
antonym pair in general English, so they do not generalise: an agent cannot learn "look
for the opposite verb" as a rule if one module breaks it. Consistency of the shape of the
pair is worth more than fidelity to one format's terminology. The format is named
`deflate`; the operations are `compress` and `decompress`.

### Why containers are not forced into the codec shape

A container is not a codec. Its operations are `entries` / `extract` / `create`, and they
are not inverses of each other. Forcing a container into a `decode` / `encode` shape would
hide the distinction between inspecting a container and unpacking it, which is a
distinction the use pattern depends on.

### Why the operation is in the module name rather than the export

An agent searches for the operation it wants to perform. Putting the operation in the
module name means the search term and the module name match, and the module name alone
states the capability. Putting it only in the export would force the agent to open the
module to discover what it does.

## Consequences

### Positive

- An agent can predict a module's name from the capability it wants, and can predict the
  name of an operation's inverse from the operation it has found.
- A symmetric pair is a testable contract: the round-trip property must hold and is tested.
- The naming is uniform across formats, so the shape of a pair is learnable as a rule.
- A capability that is not one operation has a defined home, so the next one does not
  require a new exception to the model.
- A capability module's exports carry no module-name prefix, so the call site reads
  naturally and the module name supplies the capability.

### Negative

- A format with two directions is two files rather than one, so the standard library has
  more modules than a format-grouped layout would.
- The export name is redundant with the module name for operation modules. This is
  accepted, but it is duplication.
- The taxonomy has three kinds, so an author must decide which kind a new module is. The
  rule ("the default is the operation module; a capability module only when the capability
  genuinely is not one operation") is intended to make that decision mechanical, but it is
  a decision.
- The convention is a constraint on vocabulary: a natural name that collides with a
  builtin or prelude function the module uses (such as `list`) is unavailable, and a
  less natural name must be chosen instead.

## Open questions

Two questions are deliberately left open. They are not settled and are recorded so they
are not lost.

### Is the unit a module at all, or a capability?

If AIs both write and consume this code, the natural unit may not be a file with an export
list. It may be a *searchable capability* — "decompress DEFLATE", "decode PNG", "match a
regular expression" — where the implementation is an implementation detail and what
matters is that the capability is discoverable, named, versioned, and trusted. A
capability module is closer to that unit than an operation module is, but it is still a
file with an export list. If that is the direction, the module layout is an implementation
concern and the interesting design work is in the capability layer.

### The retrieval layer

The naming convention makes a module findable *by name*. It does nothing for an agent that
does not know the name to search for. Discovery at scale is a retrieval problem — an index,
a description per module, a search interface — and that layer does not exist yet. It is
likely the larger part of making a large AI-authored library usable.
