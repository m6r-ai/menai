# AGENTS.md - Menai standard library

Guardrails for the `.menai` modules in `src/menai/stdlib/`. These are package data of the
`menai` package, so they ship in the wheel and are located through `importlib.resources`; a
new module is picked up by the `stdlib/*.menai` package-data glob with no registration step.

The root [`AGENTS.md`](../../../AGENTS.md) covers how to work in the repository, and
[`../AGENTS.md`](../AGENTS.md) covers the compiler that compiles these modules.

## Module header comments

A `.menai` module opens with a single comment block, then the code. The block carries what
applies to the module as a whole: a one-line summary, any framing, the shape of the values the
module produces or consumes, and any module-wide constraints (supported inputs, what raises).
Keep it to things a caller or maintainer needs and cannot read directly off the code.

The contract for each export belongs next to the export, as a comment immediately above its
binding, not in the top block. Documenting an export in two places invites the two copies to
disagree. When a module exports data with no enclosing lambda (a table, a constant), comment
each exported binding individually.

The result looks like this:

```menai
; module-name — one-line summary.
;
; Module-wide framing: what it is, the shape of the values it works with, and
; any constraints.

(let
  ((helper ...)

   ; What this export does, its arguments, and what it raises.
   (exported-thing
    (lambda ...)))

  (export exported-thing))
```

## Naming

A module's own bindings shadow same-named builtins and prelude functions throughout its body,
so a module and export name must avoid the names of the builtins and prelude functions the
module itself uses. There is no escape hatch: the `$`-prefixed form reaches only opcode-backed
primitives, and a prelude function such as `list` is not one. This is why the ZIP reader
exports `entries` rather than the naturally-named `list`.

A capability module exports the members of one capability under their bare operation names,
with no module-name prefix: `regexp` exports `compile`, `search`, `search?`, `search-all`,
`split` and `replace`, plus its compiled-regexp type, because the module name already supplies
the capability. An operation module exports one operation under its own name, and its decode
and encode halves pair by name rather than by structure.

See [ADR-0041](../../../docs/adr/0041-standard-library-module-taxonomy.md) and
[ADR-0052](../../../docs/adr/0052-regular-expression-captures.md).

## Codecs are capture layers, not document models

The OOXML and HTML codecs (`docx-decode`/`docx-encode`, `xlsx-*`, `pptx-*`, `html-*`) are
symmetric operation-module pairs that decode a package to a value tree and encode it back,
carrying a value round-trip guarantee. The representation is a faithful capture of the
package's parts: nothing is interpreted, namespace prefixes are kept verbatim as `xml-decode`
keeps them, and `[Content_Types].xml` and `_rels/.rels` are generated rather than carried, so
the round-trip is a value round-trip and not a byte round-trip.

A semantic document model — headings, paragraphs, runs, lists, tables, with styles and
numbering resolved — is deliberately not part of any codec. It is not specific to one format,
so it belongs in a separate higher layer that DOCX, HTML and Markdown can share, rather than
duplicated inside each codec. Do not fold interpretation into a decoder: the codec stays a
codec.

Format-specific details that must not be "helpfully" resolved:

- A relationships entry keeps the relationship's type and target mode, not only its target:
  the type is what distinguishes an image from a hyperlink, and a relationship without it is
  unusable.
- In XLSX, `sheets` is an ordered list carrying each sheet's name and part, because neither
  sheet order nor sheet name is recoverable from the worksheet part name; and a cell's
  `"s"`-typed value is not resolved to the shared string it indexes.
- In PPTX and XLSX, the package and its relationships are carried verbatim on encode rather
  than regenerated from the decoded structure, which is what makes the round-trip exact.

See [ADR-0046](../../../docs/adr/0046-docx-codec-ooxml-capture-layer.md),
[ADR-0047](../../../docs/adr/0047-pptx-codec-ooxml-capture-layer.md),
[ADR-0050](../../../docs/adr/0050-xlsx-codec-ooxml-capture-layer.md) and
[ADR-0055](../../../docs/adr/0055-html-codec-capture-layer.md).

## Tests

Each module has a `<module>.test.menai` suite beside it in `src/menai/stdlib/`, plus
`<name>-round-trip.test.menai` where the module has an encode/decode pair. These suites are the
module's specification and are driven from Python by `tests/src/menai/stdlib/`.

Run them with `menai-test src/menai/stdlib/` for the whole set, or name a single suite. They
are slow — each parametrised case compiles and runs a full Menai suite — so `make test-fast`
skips them while iterating, and `make test` and CI run everything.

`menai-format` is the canonical formatter for these files; formatting is a fixed style rather
than a description of the existing corpus, so do not adjust the formatter to match a file.
