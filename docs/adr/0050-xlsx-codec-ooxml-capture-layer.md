# ADR-0050: XLSX codec — the OOXML capture layer

Date: 2026-10-13  
Status: Accepted

## Context

[ADR-0046](0046-docx-codec-ooxml-capture-layer.md) established the DOCX codec as an
OOXML capture layer, and [ADR-0047](0047-pptx-codec-ooxml-capture-layer.md) extended the
same decision to PPTX. XLSX is the third OOXML format, and it is the same kind of thing
— a ZIP archive whose parts are XML documents and media — so the same decision applies:
the codec captures the package's parts without interpreting the spreadsheetML semantics,
and a semantic workbook model is a separate, higher layer.

XLSX differs from DOCX and PPTX in two structural ways that the codec must decide about.

First, as with PPTX, a `.xlsx` package has a workbook part, `xl/workbook.xml`, **plus one
part per worksheet**, and the worksheets are not addressed by a fixed name. The workbook
part carries a `<sheets>` list whose `<sheet>` elements name relationship ids that
resolve, through the workbook's relationships, to the worksheet parts. A sheet's
**order** is therefore an indirection — it lives in the workbook's sheet list, not in the
worksheet part names — and each sheet has its own relationships part,
`xl/worksheets/_rels/sheetN.xml.rels`, carrying the sheet's drawings and hyperlinks.
Each `<sheet>` also carries a **name** (e.g. `"Sheet1"`), which is workbook content and
is not derivable from the part name.

Second, XLSX has a **shared string table**, `xl/sharedStrings.xml`. A cell whose type is
`"s"` does not carry its text inline; it carries an integer index into the shared string
table as its value. The string itself lives in the shared string table, not in the
worksheet part.

## Decision

### The XLSX representation is the OOXML capture layer

`xlsx-decode` decodes a `.xlsx` package into a value tree that captures the package's
parts, without interpreting the spreadsheetML semantics. The decoded value is a dict:

```text
(dict
  "workbook"       <element>              ; xl/workbook.xml, decoded
  "relationships"  <dict>                 ; workbook rels: id -> relationship
  "shared-strings" <list> or #none        ; xl/sharedStrings.xml, decoded <si> elements
  "styles"         <element> or #none     ; xl/styles.xml, decoded
  "theme"          <element> or #none     ; xl/theme/theme1.xml, decoded
  "sheets"         <list>                 ; ordered, one entry per worksheet
  "media"          <dict> or #none)       ; part name -> bytes
```

An `<element>` is the value tree `xml-decode` produces, and a relationship is a dict
`(dict "type" <string> "target" <string> "mode" <string> or #none)`, exactly as in
ADR-0046 and ADR-0047.

`"workbook"` is required: a package without it is not a workbook and raises.

This is faithful capture: the XML structure of each part survives verbatim, namespace
prefixes included, and nothing is resolved or interpreted.

### Sheets are an ordered list carrying their name, because order and name are not the part name

`"sheets"` is an **ordered list**, one entry per sheet in workbook order. Each entry is a
dict:

```text
(dict
  "name"          <string>              ; the sheet name, e.g. "Sheet1"
  "part"          <string>              ; e.g. "xl/worksheets/sheet1.xml"
  "element"       <element>             ; the decoded worksheet part
  "relationships" <dict>)              ; the sheet's own rels: id -> relationship
```

A list rather than a dict keyed by part name, for the reason ADR-0047 gives for slides:
sheet order is semantic and is **not** recoverable from the part name. The order lives in
the workbook's `<sheets>`, and a package whose worksheet parts are named `sheet1.xml`,
`sheet2.xml` may present them in any order.

`"name"` is carried explicitly because the sheet name is workbook content, not derivable
from the part name. `"part"` is carried explicitly rather than derived from position, so
that the sheet parts keep their real names and the round-trip does not depend on a naming
convention.

### A sheet's own relationships are captured

Each sheet entry carries the sheet's own relationships, read from
`xl/worksheets/_rels/<name>.rels`. A sheet's drawings and hyperlinks live in its own
relationships part, not the workbook's, so dropping them would lose the sheet's content.
The map is empty when the sheet has no relationships part.

### The shared string table is captured as its `<si>` elements, not resolved

`"shared-strings"` is the decoded `xl/sharedStrings.xml`'s `<si>` elements, in order, or
`#none` when the package carries no shared string table. The codec does **not** resolve a
cell's `"s"`-typed value to the string it indexes: resolution is interpretation, and the
codec stays a codec. A consumer that wants cell text reads the shared string table itself
and indexes it.

### The workbook and its relationships are carried verbatim

`xlsx-encode` writes the `"workbook"` element and the `"relationships"` map **verbatim**.
It does not regenerate the workbook's `<sheets>` list from `"sheets"`, and it does not
synthesise the workbook's sheet relationships.

This is the decision that makes the round-trip exact, for the reason ADR-0047 gives for
the presentation: the workbook's sheet list and sheet relationships are derivable from
`"sheets"`, but regenerating them would make `decode(encode(v))` report the regenerated
forms, which need not equal the caller's. Carrying them verbatim makes the round-trip
hold for every value tree `encode` accepts, at the cost of a redundancy the caller must
keep consistent. A value tree produced by `decode` satisfies this, because both were read
from the same package.

### The codec is a symmetric pair with a value round-trip

`xlsx-decode` exports `decode` and `xlsx-encode` exports `encode`, following the
operation-module naming convention of ADR-0041. The pair carries the round-trip
guarantee:

```text
(decode (encode v)) reproduces v  for every value tree v that encode accepts
```

As in ADR-0046 and ADR-0047, this is a **value** round-trip, not a byte round-trip. The
structural parts `[Content_Types].xml` and `_rels/.rels` are generated from the parts
present, so the package need not be byte-identical to the package the same value tree
came from.

### The codec composes existing decoders

`xlsx-decode` reads the package with `zip-extract` and decodes each part with
`xml-decode`. `xlsx-encode` writes the package with `zip-create` and serialises each part
with `xml-encode`. The codec adds the XLSX-specific structure — which parts exist, which
are required, how the sheet list resolves to worksheet parts, and how each sheet's
relationships are laid out — and nothing else. A malformed package therefore fails as its
parts fail: a corrupt ZIP raises from `zip-extract`, and malformed XML raises from
`xml-decode`.

### A sheet whose relationship does not resolve raises

A `<sheet>` whose `r:id` does not resolve to a worksheet part is a malformed package, and
`decode` raises. Real workbook packages always carry the workbook relationships that
resolve their sheet list, so this is a genuine malformation rather than a case to
tolerate.

## Alternatives considered

### Fold a semantic workbook model into the codec

Have `xlsx-decode` return a workbook — sheets, rows, cells, with shared strings and
styles resolved.

Rejected, for the reason ADR-0046 gives for DOCX: it conflates a codec with a document
model, and the model is not specific to XLSX. The codec stays a codec.

### Resolve shared string references in the codec

Have `xlsx-decode` replace each `"s"`-typed cell value with the string it indexes.

Rejected. Resolution is interpretation: it would make the codec understand cell types and
the shared string table's indexing, and it would lose the distinction between a cell that
holds text and a cell that holds a number. The codec captures the parts; a consumer that
wants resolved cell values layers that on top.

### A dict of sheets keyed by part name

Have `"sheets"` be a dict mapping each worksheet part name to its element.

Rejected for the reason ADR-0047 gives for slides: it loses sheet order, which is
semantic and is not recoverable from the part name.

### Regenerate the workbook's sheet list and relationships on encode

Have `encode` derive the `<sheets>` list and the workbook's sheet relationships from
`"sheets"`.

Rejected, for the reason ADR-0047 gives for the presentation: it breaks the round-trip.

### Derive the sheet part name from position

Have the sheet part name be `xl/worksheets/sheet<N>.xml` by position, dropping `"part"`.

Rejected, for the reason ADR-0047 gives for slides: real packages do not guarantee that
naming, and a codec that renamed the parts on encode would no longer be a faithful
capture.

### Resolve namespaces

Rejected for DOCX in ADR-0046, and the same reasoning applies: `xml-decode` keeps
qualified names verbatim, and resolving them in the codec would make the XLSX
representation inconsistent with the XML representation it is built from.

## Consequences

### Positive

- The codec is small, because it composes `zip-extract`, `xml-decode`, `zip-create`, and
  `xml-encode` rather than reimplementing them.
- The representation is faithful, so no information in the package's parts is lost.
- Sheet order and sheet names are preserved, which a name-keyed or position-keyed
  representation would lose.
- The round-trip is exact and testable, because the workbook and its relationships are
  carried verbatim rather than regenerated.
- The codec is consistent with the DOCX and PPTX codecs, so the three are learnable as
  one shape.

### Negative

- The decoded value is verbose: a consumer that wants cell values must walk an XML tree,
  resolve the sheet list, and index the shared string table itself.
- The caller of `encode` must keep the workbook's sheet list and sheet relationships
  consistent with `"sheets"`. `decode` produces such a value tree, but a hand-built one
  can be inconsistent, and `encode` does not detect it.
- The codec depends on `zip-extract`, which decompresses every entry, so decoding a
  package with large media inflates all of it even when only the workbook is needed.

## Open questions

### Should the semantic workbook model be a standard library capability?

As ADR-0046 records for the document model and ADR-0047 for the presentation model, the
workbook model — sheets, rows, cells, with shared strings and styles resolved — is the
layer a consumer usually wants and is not specific to XLSX. It is not decided here
whether it belongs in the standard library or what its value-tree shape should be.
