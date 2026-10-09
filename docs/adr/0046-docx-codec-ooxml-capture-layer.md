# ADR-0046: DOCX codec — the OOXML capture layer

Date: 2026-10-09  
Status: Accepted

## Context

The standard library decodes binary formats into structured value trees: BMP, PNG,
JSON, ZIP, tar, gzip, zlib, DEFLATE, and XML. DOCX is the next format, and it is
different in kind from the ones already present.

A `.docx` file is not a single self-describing binary layout. It is an OOXML package:
a ZIP archive whose parts are XML documents and media. The document content lives in
`word/document.xml`; supporting parts (`word/styles.xml`, `word/numbering.xml`,
`word/_rels/document.xml.rels`, `word/media/*`) carry styles, list numbering,
relationships, and images. The format is therefore a *container of XML*, not a byte
layout, and a decoder for it is a composition of two decoders the library already
ships: `zip-extract` and `xml-decode`.

There is a second, larger question the format forces. DOCX has no single obvious
value-tree shape. Two are plausible:

- a **faithful capture** of the OOXML parts, in which the decoded value is the XML
  structure of the package, uninterpreted; and
- a **semantic document model**, in which the decoded value is a document — headings,
  paragraphs, runs with formatting, lists, tables — with the OOXML mechanics (styles,
  numbering, relationships) resolved away.

The second is what a consumer usually wants, and it is a substantial design in its own
right. It is also not specific to DOCX: the same model would be produced from HTML or
Markdown. Humbug, the platform Menai was extracted from, has exactly this layering: a
`docx` package that captures the OOXML structure faithfully, and a separate
`document_ir` package that is the format-agnostic model, with bridges between them.
The `docx` parser's own contract is that it "faithfully captures the OOXML structure
without semantic interpretation".

This ADR decides the first layer only. The semantic document model is a separate
capability, layered on top, and is not decided here.

## Decision

### The DOCX representation is the OOXML capture layer

`docx-decode` decodes a `.docx` package into a value tree that captures the package's
parts, without interpreting the wordprocessingML semantics. The decoded value is a
dict:

```text
(dict
  "document"      <element>              ; word/document.xml, decoded
  "styles"        <element> or #none     ; word/styles.xml, decoded
  "numbering"     <element> or #none     ; word/numbering.xml, decoded
  "relationships" <dict>                 ; relationship id -> relationship
  "media"         <dict> or #none)       ; part name -> bytes
```

An `<element>` is the value tree `xml-decode` produces:

```text
(dict "tag" <string> "attrs" <dict> "children" <list>)
```

A relationship is a dict `(dict "type" <string> "target" <string> "mode" <string> or
#none)`.

This is faithful capture: the XML structure of each part survives verbatim, namespace
prefixes included, and nothing is resolved or interpreted. A consumer that wants
headings and paragraphs builds them from this tree; the codec does not build them.

### The semantic document model is a separate, higher layer

A semantic document model — the shape that resolves styles, numbering, and
relationships into a document — is deliberately **not** part of the DOCX codec. It is a
distinct capability that is not specific to DOCX, and it is layered on top of this
representation rather than folded into it. This mirrors Humbug's `docx` /
`document_ir` split.

Two consequences follow, and they are the reason for the boundary:

- The DOCX codec stays a **codec**. It decodes a package and encodes it back, and it
  does not decide what a document means.
- The semantic model can be written once and fed from DOCX, HTML, and Markdown alike,
  rather than being duplicated inside each format's codec.

### Relationships carry their type

The relationships map keeps each relationship's type URI and target mode, not only its
target. A relationship without its type is unusable: the type is how a consumer tells
an image relationship from a hyperlink relationship, which is the entire reason to
resolve a relationship at all. Keeping the type is faithful capture, not interpretation.

### The codec is a symmetric pair with a value round-trip

`docx-decode` exports `decode` and `docx-encode` exports `encode`, following the
operation-module naming convention of ADR-0041. The pair carries the round-trip
guarantee that ADR-0041 attaches to a symmetric pair:

```text
(decode (encode v)) reproduces v  for every value tree v that encode accepts
```

This is a **value** round-trip, not a byte round-trip. The package's two structural
parts, `[Content_Types].xml` and `_rels/.rels`, are not part of the value tree, so
`encode` generates them from the parts present, and `decode` discards them. The
generated structural parts and the ordering of the relationships are canonical, so the
package need not be byte-identical to the package the same value tree came from. The
tree survives; the original layout does not. This is the same distinction ADR-0041
draws for container `create` operations.

### The codec composes existing decoders

`docx-decode` reads the package with `zip-extract` and decodes each part with
`xml-decode`. `docx-encode` writes the package with `zip-create` and serialises each
part with `xml-encode`. The codec adds the DOCX-specific structure — which parts exist,
which are required, and how relationships are laid out — and nothing else.

A consequence is that the codec's behaviour on a malformed package is the behaviour of
its parts: a corrupt ZIP raises from `zip-extract`, and malformed XML raises from
`xml-decode`.

## Alternatives considered

### Fold the semantic document model into the codec

Have `docx-decode` return a document — headings, paragraphs, runs, lists, tables —
with styles and numbering resolved.

Rejected. It conflates a codec with a document model. The model is not specific to
DOCX, so folding it in would duplicate it in every format's codec and make the codec's
value tree depend on a large body of interpretation rather than on the package's
structure. It would also make the round-trip guarantee far harder to state, because
the model is lossy with respect to the OOXML it came from: style inheritance and
numbering are resolved away, so `encode` could not reproduce the tree it was given.

### A byte round-trip

Have `encode` reproduce a byte-identical package for a decoded one.

Rejected. It would require the value tree to carry the generated structural parts, the
ZIP entry order, and the compression choices of the original — none of which are
document content. The value round-trip is the meaningful guarantee: it is what makes
`decode` and `encode` a testable pair, and it is what a consumer actually relies on.

### A relationships map of id to target, without the type

Keep only the relationship id and target, as the smallest possible map.

Rejected. It makes the map unusable. The relationship type is what distinguishes an
image from a hyperlink, and a consumer resolving `r:embed="rId5"` needs to know which it
is. Dropping the type is not a simplification of faithful capture; it is a loss of
captured structure.

### Resolve namespaces

Resolve each XML namespace prefix to its URI, so that `w:p` becomes a qualified name in
the wordprocessingML namespace.

Rejected. `xml-decode` keeps qualified names verbatim and does not resolve namespaces,
and the DOCX codec composes it. Resolving namespaces in the codec would make the DOCX
representation inconsistent with the XML representation it is built from, and would be
a large piece of work (namespace scope, default namespaces, `xmlns` attributes) for no
gain at this layer.

### Decode only, with no `encode`

Ship `docx-decode` alone, carrying no round-trip obligation.

Rejected. The round-trip is the property that makes the pair testable and that proves
the representation is complete: if `decode` and `encode` round-trip, then the value
tree captures everything `encode` needs, and the two agree on the shape. A decode-only
module would leave that unproven.

## Consequences

### Positive

- The codec is small, because it composes `zip-extract`, `xml-decode`, `zip-create`,
  and `xml-encode` rather than reimplementing them.
- The representation is faithful, so no information in the package's parts is lost to
  interpretation, and a consumer can build whatever model it needs on top.
- The round-trip is a testable contract, and testing it proves the representation is
  complete with respect to `encode`.
- The semantic document model has a defined home — a separate, higher layer — so it can
  be shared across DOCX, HTML, and Markdown rather than duplicated.
- The relationships map is usable, because it carries the type that makes a
  relationship meaningful.

### Negative

- The decoded value is verbose: a consumer that wants a document must walk an XML tree
  and resolve styles and numbering itself. The codec does not do that work.
- The value round-trip does not preserve the original package's bytes, entry order, or
  compression. A consumer that needs byte fidelity cannot use the pair.
- The codec depends on `zip-extract`, which decompresses every entry, so decoding a
  package with large media inflates all of it even when only the document is needed.
- The representation is coupled to `xml-decode`'s namespace handling: if `xml-decode`
  ever resolved namespaces, the DOCX representation would change with it.

## Open questions

### Should the semantic document model be a standard library capability?

The model is the layer a consumer usually wants, and it is not specific to DOCX. It is
not decided here whether it belongs in the standard library, what its value-tree shape
should be, or whether it should be a capability module (ADR-0041) with its own
operations. It is recorded as the next layer, deliberately left open.
