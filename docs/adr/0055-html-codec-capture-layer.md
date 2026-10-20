# ADR-0055: HTML codec — the tag capture layer

Date: 2026-10-20
Status: Accepted

## Context

HTML is the most common document format an agent is handed: a fetched page, a saved
page, an email body. The standard library could not read it. `xml-decode` is the
obvious candidate, and it fails on almost every real page — a void element written
`<br>`, an omitted end tag, an attribute with no value, a named entity outside the
five XML predefined ones. The only alternative was for every agent to hand-write a
tag scanner, which is exactly the kind of repeated, error-prone work a standard
library exists to remove.

The two questions this ADR settles are the ones that shape the module: how much of
HTML's error recovery the decoder performs, and where the line between a codec and a
document model falls.

## Decision

### Two operation modules, not one capability module

`html-decode` and `html-encode` are a symmetric operation-module pair (ADR-0041).
HTML is one format with two directions, so the unit is the operation, not the
format: an agent searching for "decode HTML" finds `html-decode`. Each exports its
operation under the operation's bare name, so the call site is `(:: html-decode
decode)`.

There is no `text` helper, no `select`, no `find` — those would make the module a
capability, and they are not part of reading or writing the format.

### The value tree is the `xml-decode` element tree

An element is a dict:

```menai
(dict "tag" <string> "attrs" <dict> "children" <list>)
```

This is deliberately the same shape `xml-decode` produces, for three reasons. A
caller that can walk one can walk the other. A page that happens to be well-formed
XHTML decodes to the same tree through either module. And the two decoders compose
with the same encoders, so `html-encode` and `xml-encode` are interchangeable for a
tree that XML can also express.

Two node kinds are added because HTML has them and XML does not. Both are element
dicts with a reserved tag, so every child remains either a string or an element dict
and the walker needs no new case:

```menai
(dict "tag" "!doctype" "attrs" (dict) "children" (list "html"))
(dict "tag" "!--"     "attrs" (dict) "children" (list "a comment"))
```

The doctype's children are the declaration's text after `<!DOCTYPE `, and a comment's
children are the comment's text. They are carried rather than discarded because
discarding them would make the round-trip lossy for a whole class of document, and
because a comment is content a caller may legitimately want.

### `decode` is lenient; it is not a browser tree builder

`decode` raises only when its argument is not a string. Malformed input is recovered
from, never rejected. The recovery is the minimum that lets a real page be read:

- **Void elements** (`area base br col embed hr img input link meta param source
  track wbr`) have no content and no end tag. `<br>` and `<br/>` both decode to an
  empty element.
- **Raw-text elements** (`script`, `style`) have text content in which `<` does not
  start a tag, so a `<` inside a script is text, not markup.
- **Omitted end tags.** An end tag closes the nearest open element with that name.
  If there is none, it is ignored. An element may be closed by the start of a
  sibling that cannot nest inside it — `<li>` closes an open `<li>`, `<p>` closes an
  open `<p>` — so `<ul><li>a<li>b</ul>` decodes to two `li` elements.
- **Unclosed elements** at end of input are closed implicitly.
- **Attributes** may be unquoted, may be single- or double-quoted, and may have no
  value at all (`<input disabled>`), in which case the value is the empty string.
- **Character references** are decoded: the five XML predefined entities, the HTML
  named entities in common use (`&nbsp;` `&copy;` `&mdash;` and so on), and numeric
  references in decimal (`&#38;`) and hexadecimal (`&#x26;`). A `&` that does not
  begin a known reference is left as a literal `&`, which is what a browser does and
  what makes a page with a stray `&` readable.
- **A stray `<`** that does not begin a tag is text.

What `decode` deliberately does **not** do is build the HTML5 tree. There is no
implied `<html>`, `<head>`, or `<tbody>`; no foster parenting of content out of a
table; no adoption agency algorithm for misnested formatting elements. The decoder
reports the tags the document contains, nested by the tags' own start and end
markers, and nothing else.

That line is the same one ADR-0046 and ADR-0050 draw for the OOXML codecs: the codec
captures what was written, and interpretation is a separate layer. A browser's tree
is interpretation — it is a model of what the author *meant*, not a record of what
the file says — and building it would make the decoder's output depend on a
several-hundred-page algorithm whose result no longer round-trips.

The consequence is that a page relying on implied structure decodes to a tree that
differs from the browser's DOM. That is stated in the module header so a caller is
not surprised by it.

### `encode` carries a value round-trip

For every value tree `v` that `encode` accepts, `(decode (encode v))` reproduces `v`.
This is a value round-trip, not a byte round-trip, on the same terms as the DOCX and
XLSX codecs: HTML has many equivalent spellings of the same tree, and reproducing the
original spelling is not meaningful.

`encode` writes what the tree says and adds no structure. An element with no children
is written with an explicit end tag rather than self-closing, because HTML has no
self-closing syntax outside foreign content and `<div/>` is not a `<div>` to a
browser. Text is escaped minimally (`&`, `<`, `>`); attribute values additionally
escape `"`. The doctype and comment nodes are written back as the declarations they
capture.

A value that is not a well-formed tree raises: a missing or non-string `"tag"`, a
non-dict `"attrs"`, a non-list `"children"`, a non-string attribute value, or a child
that is neither a string nor an element dict. This is the same contract `xml-encode`
has, and it is the only thing `encode` raises for.

### The named-entity table is a shared-data module

The named character references are a specification table, not code. They live in
`html-entities`, a shared-data module (ADR-0041) exporting the table, used by
`html-decode` for decoding and by `html-encode` for nothing — the encoder escapes
only the five characters that must be escaped and emits everything else literally,
because a named entity in the output would decode to the same character and the
encoder has no reason to prefer it.

The table is deliberately the common subset, not all 2231 entries of the HTML
standard's list. The full list is a large data dependency whose tail is obscure
symbols and mathematical operators that a page being read for its content does not
use. A reference outside the table is left as a literal `&name;` text run by
`decode` — visible to the caller rather than silently wrong — and the table can grow
without changing any operation's signature.

## Alternatives considered

### Extending `xml-decode` to accept HTML

Rejected. The two dialects disagree on fundamentals: XML requires every element to be
closed and every attribute to be quoted, and a decoder that accepted both would have
to decide which dialect it was reading, which is a mode flag on an operation that
currently has one behaviour. It would also make the XML decoder's strictness — a
property callers rely on to catch a malformed document — conditional.

### A full HTML5 tree builder

Rejected. It is the browser's algorithm, not a codec: it invents elements the file
does not contain, moves content between parents, and produces a tree that cannot be
encoded back to the input. An agent that needs the browser's tree wants a browser,
and an agent that needs the file's tags wants this module. Keeping the decoder a
capture layer keeps the round-trip honest and keeps the module a few hundred lines
rather than a few thousand.

### Raising on malformed input

Rejected. Real HTML is malformed by design — omitted end tags and unquoted attributes
are legal, not errors — so a strict decoder would reject most real pages. A decoder
that raises on a page it cannot fully understand is worse than one that returns what
it found, because the caller has no way to catch the error and no way to ask for a
partial result.

### A capability module exporting `decode`, `encode`, `text`, and `select`

Rejected. `text` and `select` are queries over a tree, not part of reading or writing
the format, and they belong with the semantic document model that ADR-0046 and
ADR-0050 already defer. Adding them here would make the module a capability and would
put two unrelated concerns in one file.

### Discarding the doctype and comments

Rejected. They are content the file contains, and a codec that drops them cannot
round-trip a document that has them. Carrying them as reserved-tag element dicts
costs two lines in the decoder and nothing in the walker.

## Consequences

### Positive

- An agent can read a real HTML page — fetched, saved, or pasted — into a value tree
  it can walk, without hand-writing a scanner.
- The tree is the `xml-decode` shape, so one walker handles both, and the two
  encoders are interchangeable for trees XML can express.
- The round-trip is a testable contract and is tested.
- The decoder is a capture layer, so its output reflects the file rather than a
  browser's interpretation of it, and it cannot invent content.
- The named-entity table can grow without changing an operation's signature.

### Negative

- The tree is not the browser's DOM. A page that relies on implied `<html>`,
  `<head>`, or `<tbody>`, or on foster parenting, decodes to a different structure
  than a browser would show. This is documented in the module header, but it is a
  real difference a caller must know about.
- The named-entity table is a subset, so a page using an entity outside it decodes
  with a literal `&name;` text run rather than the character. This is visible rather
  than silent, but it is a limitation.
- `encode` writes explicit end tags and never self-closes, so its output is more
  verbose than a hand-written page and is not byte-identical to any input.
- Two modules and a data module are three files where a single "HTML" module would be
  one. This is the cost of the operation-module taxonomy, and it is the same cost
  every codec pair in the library pays.
