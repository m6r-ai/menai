# ADR-0047: PPTX codec — the OOXML capture layer

Date: 2026-10-12  
Status: Accepted

## Context

[ADR-0046](0046-docx-codec-ooxml-capture-layer.md) established the DOCX codec as an
OOXML capture layer: `docx-decode` decodes a `.docx` package to a faithful capture of
its parts, and `docx-encode` writes it back, carrying a value round-trip. PPTX is the
next OOXML format, and it is the same kind of thing — a ZIP archive whose parts are XML
documents and media — so the same decision applies: the codec captures the package's
parts without interpreting the presentationML semantics, and a semantic presentation
model is a separate, higher layer.

PPTX differs from DOCX in one structural way that the codec must decide about. A
`.docx` package has a single content part, `word/document.xml`, whose content order is
literal. A `.pptx` package has a presentation part, `ppt/presentation.xml`, **plus one
part per slide**, and the slides are not addressed by a fixed name. The presentation
part carries a `<p:sldIdLst>` whose `<p:sldId>` elements name relationship ids that
resolve, through the presentation's relationships, to the slide parts. A slide's
**order** is therefore an indirection — it lives in the presentation's slide list, not
in the slide part names — and each slide has its own relationships part,
`ppt/slides/_rels/slideN.xml.rels`, carrying the slide's images and hyperlinks.

## Decision

### The PPTX representation is the OOXML capture layer

`pptx-decode` decodes a `.pptx` package into a value tree that captures the package's
parts, without interpreting the presentationML semantics. The decoded value is a dict:

```text
(dict
  "presentation"  <element>              ; ppt/presentation.xml, decoded
  "slides"        <list>                 ; ordered, one entry per slide
  "slide-masters" <list>                 ; ppt/slideMasters/*.xml, decoded
  "slide-layouts" <list>                 ; ppt/slideLayouts/*.xml, decoded
  "theme"         <element> or #none     ; ppt/theme/theme1.xml, decoded
  "relationships" <dict>                 ; presentation rels: id -> relationship
  "media"         <dict> or #none)       ; part name -> bytes
```

An `<element>` is the value tree `xml-decode` produces, and a relationship is a dict
`(dict "type" <string> "target" <string> "mode" <string> or #none)`, exactly as in
ADR-0046.

`"presentation"` is required: a package without it is not a presentation and raises.

This is faithful capture: the XML structure of each part survives verbatim, namespace
prefixes included, and nothing is resolved or interpreted.

### Slides are an ordered list, because slide order is not the part name

`"slides"` is an **ordered list**, one entry per slide in presentation order. Each entry
is a dict:

```text
(dict
  "part"          <string>              ; e.g. "ppt/slides/slide1.xml"
  "element"       <element>             ; the decoded slide part
  "relationships" <dict>)              ; the slide's own rels: id -> relationship
```

A list rather than a dict keyed by part name, because slide order is semantic and is
**not** recoverable from the part name: the order lives in the presentation's
`<p:sldIdLst>`, and a package whose slide parts are named `slide1.xml`, `slide2.xml` may
present them in any order. A dict keyed by part name would lose the order, which is
document content.

`"part"` is carried explicitly rather than derived from position, so that the slide
parts keep their real names and the round-trip does not depend on a naming convention.

### A slide's own relationships are captured

Each slide entry carries the slide's own relationships, read from
`ppt/slides/_rels/<name>.rels`. A slide's images and hyperlinks live in its own
relationships part, not the presentation's, so dropping them would lose the slide's
content. The map is empty when the slide has no relationships part.

### The presentation and its relationships are carried verbatim

`pptx-encode` writes the `"presentation"` element and the `"relationships"` map
**verbatim**. It does not regenerate the presentation's `<p:sldIdLst>` from `"slides"`,
and it does not synthesise the presentation's slide relationships.

This is the decision that makes the round-trip exact. The presentation's slide list and
slide relationships are derivable from `"slides"`, so a codec *could* regenerate them —
but then `decode(encode(v))` would report the regenerated forms, which need not equal
the caller's, and the round-trip would fail. Carrying them verbatim makes the round-trip
hold for every value tree `encode` accepts, at the cost of a redundancy the caller must
keep consistent: the presentation's slide list and slide relationships must agree with
`"slides"`. A value tree produced by `decode` satisfies this, because both were read
from the same package.

### The codec is a symmetric pair with a value round-trip

`pptx-decode` exports `decode` and `pptx-encode` exports `encode`, following the
operation-module naming convention of ADR-0041. The pair carries the round-trip
guarantee:

```text
(decode (encode v)) reproduces v  for every value tree v that encode accepts
```

As in ADR-0046, this is a **value** round-trip, not a byte round-trip. The structural
parts `[Content_Types].xml` and `_rels/.rels` are generated from the parts present, and
the generated slide master and layout part names are canonical, so the package need not
be byte-identical to the package the same value tree came from.

### The codec composes existing decoders

`pptx-decode` reads the package with `zip-extract` and decodes each part with
`xml-decode`. `pptx-encode` writes the package with `zip-create` and serialises each
part with `xml-encode`. The codec adds the PPTX-specific structure — which parts exist,
which are required, how the slide list resolves to slide parts, and how each slide's
relationships are laid out — and nothing else. A malformed package therefore fails as
its parts fail: a corrupt ZIP raises from `zip-extract`, and malformed XML raises from
`xml-decode`.

### A slide whose relationship does not resolve raises

A `<p:sldId>` whose `r:id` does not resolve to a slide part is a malformed package, and
`decode` raises. Real presentation packages always carry the presentation relationships
that resolve their slide list, so this is a genuine malformation rather than a case to
tolerate.

## Alternatives considered

### Fold a semantic presentation model into the codec

Have `pptx-decode` return a presentation — slides, shapes, text frames, with layouts and
masters resolved.

Rejected, for the reason ADR-0046 gives for DOCX: it conflates a codec with a document
model, and the model is not specific to PPTX. The codec stays a codec.

### A dict of slides keyed by part name

Have `"slides"` be a dict mapping each slide part name to its element.

Rejected. It loses slide order, which is semantic and is not recoverable from the part
name. Order is the one thing the presentation's slide list exists to express.

### Regenerate the presentation's slide list and relationships on encode

Have `encode` derive the `<p:sldIdLst>` and the presentation's slide relationships from
`"slides"`, so the caller need not keep them consistent.

Rejected. It breaks the round-trip: `decode(encode(v))` would report the regenerated
forms rather than the caller's, and the two need not agree. The round-trip is the
property that makes the pair testable and proves the representation is complete, and it
is worth a redundancy in the caller's value tree to keep it exact.

### Derive the slide part name from position

Have the slide part name be `ppt/slides/slide<N>.xml` by position, dropping `"part"`.

Rejected. Real packages do not guarantee that naming, and a codec that renamed the parts
on encode would no longer be a faithful capture. Carrying `"part"` keeps the real names.

### Resolve namespaces

Rejected for DOCX in ADR-0046, and the same reasoning applies: `xml-decode` keeps
qualified names verbatim, and resolving them in the codec would make the PPTX
representation inconsistent with the XML representation it is built from.

## Consequences

### Positive

- The codec is small, because it composes `zip-extract`, `xml-decode`, `zip-create`, and
  `xml-encode` rather than reimplementing them.
- The representation is faithful, so no information in the package's parts is lost.
- Slide order is preserved, which a name-keyed representation would lose.
- The round-trip is exact and testable, because the presentation and its relationships
  are carried verbatim rather than regenerated.
- The codec is consistent with the DOCX codec, so the two are learnable as one shape.

### Negative

- The decoded value is verbose: a consumer that wants slides must walk an XML tree and
  resolve the slide list, layouts, and masters itself.
- The caller of `encode` must keep the presentation's slide list and slide relationships
  consistent with `"slides"`. `decode` produces such a value tree, but a hand-built one
  can be inconsistent, and `encode` does not detect it.
- The generated slide master and layout part names are canonical, so a package with
  differently-named masters or layouts is not reproduced byte-for-byte.
- The codec depends on `zip-extract`, which decompresses every entry, so decoding a
  package with large media inflates all of it even when only the presentation is needed.

## Open questions

### Should the semantic presentation model be a standard library capability?

As ADR-0046 records for the document model, the presentation model is the layer a
consumer usually wants and is not specific to PPTX. It is not decided here whether it
belongs in the standard library or what its value-tree shape should be.
