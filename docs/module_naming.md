# Module naming (working document)

**Status: working document — not a decision.** This note captures an in-progress
discussion about how standard library modules and their exports should be named. It
will be superseded by an ADR once the approach is settled. The existing modules have
been renamed to follow the convention below, but the convention is not yet final.

## Why this matters

The standard library is written and consumed by AI agents. An agent does not browse a
directory tree; it searches for a capability, finds a module, and calls it. So the
properties that make a module usable are not the properties that make a library pleasant
for a human to navigate. What matters is:

- the module is **findable** by the term an agent would search for;
- the module name **states the capability**, so an agent can use it without opening it;
- the naming makes a module's **counterpart predictable**, so an agent that finds one
  operation can name the inverse operation without searching for it.

## The unit is the operation

A module does **one thing**, and its name is `<format>-<operation>`:

- `json-decode` — decode JSON.
- `json-encode` — encode JSON.
- `deflate-compress` — compress DEFLATE.
- `deflate-decompress` — decompress DEFLATE.

The unit is the operation, not the format. A format with two directions is two modules,
not one module with two exports. This is deliberate: an agent searches for the operation
it wants to perform, so the operation belongs in the name it searches for. Grouping a
format's operations into a single module would put the search term in the export rather
than the name, and would force the agent to open the module to discover what it does.

**Pairing is by name, not by structure.** `json-decode` and `json-encode` are a pair
because their names say so. They are not required to live together, and no module exists
whose only purpose is to group them. The naming convention makes the pair visible; the
directory layout does not need to.

## The principle

**Operations that are true inverses are named as a symmetric pair and carry a
round-trip guarantee. Operations that are not inverses are named individually, and no
symmetry is implied.**

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
not been written yet carries no round-trip obligation, and the name alone does not
claim one — the obligation arises from the pair, not from the single name.

## The operation vocabulary

The operation part of a module name is drawn from a small, fixed vocabulary, so that the
inverse of an operation is always predictable:

| Operation | Inverse | Meaning |
|-----------|---------|---------|
| `decode` | `encode` | value ↔ `bytes`/`string` representation |
| `compress` | `decompress` | byte stream ↔ compressed byte stream |
| `entries` | — | read a container and return metadata about its contents |
| `extract` | — | read a container and also unpack its contents |
| `create` | — | build a container |

`entries`, `extract`, and `create` are container operations and are **not** a symmetric set.
They are named individually because they are not inverses:

- `entries` reads a container and returns metadata about its contents.
- `extract` is defined in terms of `entries` — it reads the container and additionally
  unpacks the contents. It is a derived read, not a peer of `entries`.
- `create` builds a container. Its relationship to the reads is a **content round-trip**,
  not a byte round-trip: reading back a container produced by `create` recovers the
  logical entries that were put in, not a byte-identical container.

The container shape is deliberately asymmetric because the operations are asymmetric. The
use pattern is to read a container's entries, decide what is worth investigating, and
extract that — a read-then-read flow, with `create` as the separate write side.

## Two kinds of module

Not every module is an operation.

- **Operation modules** do one thing and are named `<format>-<operation>`.
- **Shared-data modules** hold a specification's constant data that several operation
  modules need. They are named for what they hold, not for an operation. `deflate-tables`
  (the RFC 1951 constant tables, used by both `deflate-compress` and `deflate-decompress`)
  is the example.

This is not an exception to the model; it is a second kind of module. A data module is
not an operation and should not be named as one.

## Naming constraints

A module's own bindings shadow same-named builtins and prelude functions throughout its
body. A module that exports a name it also needs internally therefore breaks itself: it
can no longer call the builtin or prelude function of that name. There is no escape
hatch, because the `$`-prefixed form only reaches opcode-backed primitives, and most of
the names at risk (`list`, `map-list`, `range`, ...) are prelude functions rather than
primitives.

**A module and export name must avoid the names of the builtins and prelude functions
the module itself uses.** This is a general constraint on the whole vocabulary, not a
special case: it applies to every module name and every export name.

The container read operation is the case where this constraint bit. `list` was the
natural name, and it is unusable; `entries` is the name that was chosen instead. See
[Why the container read operation is `entries`, not `list`](#why-the-container-read-operation-is-entries-not-list).

## Exports

A module exports its operation under the same name as the operation in its module name.
`json-decode` exports `decode`; `deflate-compress` exports `compress`. The export name is
therefore redundant with the module name, and that redundancy is accepted: the export name
is predictable from the module name, and it reads naturally at the call site
(`(:: json-decode decode)`).

## Reasoning behind the choices

### Why the operation is in the module name

An agent searches for the operation it wants to perform. Putting the operation in the
module name means the search term and the module name match, and the module name alone
states the capability — no opening the file to discover what it does.

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
for the opposite verb" as a rule if one module breaks it. Consistency of the *shape* of
the pair is worth more than fidelity to one format's terminology. The format is named
`deflate`; the operations are `compress` and `decompress`.

### Why containers are not forced into the codec shape

A container is not a codec. Its operations are `entries` / `extract` / `create`, and they
are not inverses of each other. Forcing a container into a `decode` / `encode` shape would
hide the distinction between inspecting a container and unpacking it, which is a
distinction the use pattern depends on.

### Why the container read operation is `entries`, not `list`

`list` is the natural word for "show me what is in this container", and it was the first
choice. It is ruled out by the shadowing constraint above. `entries` is the replacement,
and it is arguably more descriptive anyway: the operation returns a dict whose principal
field is `"entries"`.

## Open questions

These are not settled. They are recorded so they are not lost.

### Is the unit a module at all, or a capability?

If AIs both write and consume this code, the natural unit may not be a file with an export
list. It may be a *searchable capability* — "decompress DEFLATE", "decode PNG" — where the
implementation is an implementation detail and what matters is that the capability is
discoverable, named, versioned, and trusted. If that is the direction, the module layout is
an implementation concern and the interesting design work is in the capability layer.

### How are modules located?

Whether modules are grouped into subdirectories, and whether each module occupies its own
directory, is undecided. Under the convention above, a module's name is its identity and
its location is an organisational detail, so this question is separable from naming.

### The retrieval layer

The naming convention makes a module findable *by name*. It does nothing for an agent that
does not know the name to search for. Discovery at scale is a retrieval problem — an index,
a description per module, a search interface — and that layer does not exist yet. It is
likely the larger part of making a large AI-authored library usable.
