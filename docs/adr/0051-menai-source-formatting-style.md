# ADR-0051: Menai source formatting style

Date: 2026-10-13  
Status: Accepted

## Context

Menai source is both hand-written and tool-generated, and `menai-format`
formats it. Until now the formatter's rules were its own, and they did not match
the style the standard library and prelude were written in: running the formatter
over a stdlib module reported that a hand-written file needed reformatting. A
formatter that disagrees with the canonical corpus is worse than no formatter,
because it invites churn on every run and makes "is this file formatted?" a
meaningless question.

A second problem motivated this decision. The formatter is the natural place to
diagnose malformed source: it already builds the full nesting structure, so it can
report *where* parentheses fail to balance rather than leaving the compiler to
report an error far from the cause. For that to be useful, its notion of structure
must be the one the corpus uses.

The style below is the one the prelude and standard library are written in. It is
recorded here because it cannot be read off any single file: the corpus is
hand-written and contains a handful of local inconsistencies, so the rule has to be
stated rather than inferred.

## Decision

### Width

The layout width is **100 columns**, measured as the column at which a form's
compact rendering would end. A form is laid out compactly when its compact
rendering ends at or before column 100, and multi-line otherwise. The width is a
property of the form's rendered position, not of its content alone, so an
identical form may be compact at a shallow indentation and multi-line at a deep
one.

A line may exceed the width when it contains a single token longer than the width,
because such a token cannot be broken. A long string literal or a `string-hex->bytes`
argument is the usual case. The width governs where the formatter *chooses* to break;
it is not a guarantee that no line exceeds it.

Such a token is the last meaningful thing on its line: the formatter never places
a further argument or value after it. What follows it is only the closing
parentheses of the forms it sits inside, which carry no information and which the
reader does not have to scan. The overflow is therefore confined to the token
itself and does not push any structure off the page.

### Compact or multi-line

A list is rendered compactly on a single line if and only if it contains no
comments and its compact rendering ends at or before column 100. Otherwise it is
rendered multi-line.

### Compound forms with a nested body are always multi-line

`if`, `let`, `let*`, and `letrec` are laid out multi-line whenever they
contain a nested form, even when the compact rendering would fit within the width.
A form whose parts are all atoms is compact. For the binding forms, the binding
list is not part of the body: `(let ((x 5)) x)` is compact because its body is an
atom, while `(let ((x 5)) (foo x))` is multi-line because its body is a call.

This set is the reserved forms only. A binding named `loop` is an ordinary call
and follows the general-list rule, like any other call.

This is the same principle already applied to `and`/`or` (always multi-line with
two or more operands) and `match` (always multi-line): where a form's readability
comes from its structure, layout follows the structure rather than the width.

The alternative — deciding each occurrence by whether it happens to fit — makes
the layout of an identical form depend on where it lands. The same `if` is compact
at a shallow indentation and multi-line at a deep one, so the reader cannot learn
the shape of a form from the form itself. Consistency is preferred over the
smallest number of source lines.

`lambda` is deliberately **not** included. A compact lambda with a short body is
readable, and expanding it adds a line without clarifying anything.

### General lists

A multi-line general list `(head arg1 arg2 ... argn)` places `head` and `arg1` on
the first line and each remaining argument on its own line, aligned under `arg1`.
This is the default for every form, including calls, `if`, `and`, and `or`.

Two shapes are exceptions to the alignment rule. Both break after the head,
placing each argument on its own line indented by two.

#### A lone argument

A general list with exactly **one** argument is the exception. It places `head`
alone on the first line and the argument on the next line, indented by two:

```menai
(list->string
  (list-reverse acc))
```

Aligning a lone argument under `arg1` is meaningless — there is no second argument
to align with — and it is actively harmful. The argument is pulled up onto the head
line, so the argument's own arguments then align under *its* first argument, and
the column compounds with each level of nesting. A chain of single-argument forms
marches to the right until it runs off the page, and the formatter has no column
left to lay out the innermost form. Breaking after the head resets the column to a
shallow indent at every level, which is what the hand-written corpus does.

#### A list of peers

A general list with two or more arguments that are **all** nested forms breaks
after the head, putting each argument on its own line indented by two:

```menai
(list
  (list "entries" ...)
  (list "errors" ...))
```

The arguments are peers: each is a form of equal standing, and the list is read by
scanning them. Keeping the first on the head line and aligning the rest under it
indents the siblings by the width of the first argument, which for a list of lists
is large and compounds with each level of nesting. Breaking after the head gives
every peer the same shallow indent.

`if`, `and`, and `or` are exempt. Their first argument is the condition, which is
read as part of the head — `(if condition` is a unit — so it stays on the head line
and the remaining arguments align under it, however deeply they nest.

This applies to a general list, not to the special forms, which already indent
their trailing elements by two.

### Special forms

`lambda`, `let`, `let*`, `letrec`, and `match` indent their trailing elements by
**two** columns from the form's opening parenthesis:

- `lambda`: the body.
- `let`, `let*`, `letrec`: the binding list and the body.
- `match`: the arms.

A `match` is always laid out multi-line, one arm per line, even when it would fit
on one line: a table of arms is read by scanning the patterns, which the compact
form does not support. The other special forms are compact when they fit.

### Bindings

A binding `(name value)` that is laid out multi-line puts `name` on the first line
and `value` on the next, indented to the column of `name` (the binding's opening
parenthesis plus one):

```menai
(name
 value)
```

Whether a binding is laid out compactly is decided by measuring the value at the
column it occupies after `(name `, not at the column of the binding's opening
parenthesis. A long binding name therefore leaves less room for the value, and a
value that would fit after a short name goes multi-line after a long one.

A binding list is placed on the same line as the form name when it fits there, and
on the following line indented by two otherwise.

A binding that is laid out multi-line is given visual whitespace around it: it is
separated from the preceding binding and from the following binding by a blank
line, unless it is the first or last binding respectively.  A blank line between
two bindings is placed immediately after the first, so it precedes any comment
that introduces the second.  Single-line bindings are not separated by blank
lines.  This separates the entries of a module's binding list visually, which is
what makes a long list of definitions scannable.

### Dicts group their arguments in pairs

A `dict` is a flat list of alternating keys and values, and a multi-line `dict`
keeps each key with its value: `(dict k1 v1` on the first line, then each
subsequent `ki vi` pair on its own line aligned under `k1`.  Within a pair the
key and the value's first line share the line, so a pair whose value is itself a
multi-line list or dict keeps the value's opening on the key's line.  This is the
general-list rule with the arguments grouped in pairs rather than taken one at a
time, and it is what makes a table of entries readable.

### `and` and `or` are flat and variadic

`and` and `or` are variadic and are written flat: `(or a b c d)`, never as a
right-nested cascade `(or a (or b (or c d)))`. The cascade carries no meaning —
both forms are associative and short-circuit left to right — so nesting them is
formatting noise that pushes each successive condition further right. A flat
`and`/`or` follows the general-list rule: compact when it fits, otherwise one
operand per line aligned under the first operand.

An `and` or `or` with two or more operands is always laid out multi-line, one
operand per line aligned under the first operand, even when it would fit on one
line. A chain of conditions is the one case where the compact form is harder to
read than the expanded form, so the width rule does not apply to it. A
single-operand `and`/`or` is compact.

## Alternatives considered

### A width other than 100

Widths of 60 and 80 were rejected because the corpus contains many compact forms
that exceed them; adopting a narrower width would reflow a large fraction of the
standard library for no readability gain. 120 was rejected because it is wide
enough that a form which fits is already hard to scan. 100 is the narrowest width
that leaves the bulk of the corpus unchanged.

### A content-length width

Measuring the compact rendering's own length, independent of indentation, was
rejected because it does not match the corpus: deeply-indented forms in the
standard library are expanded even when they are short, precisely because they
have run out of room.

### Keeping the cascade idiom for `and`/`or`

Rejected because the nesting is meaningless and it drives the formatter's
indentation one level right per condition, so a long chain marches off the page.
Flat variadic forms are already accepted by the compiler and semantic analyser, so
this required no language change.

### Treating `if` as a special form

Rejected as a layout rule: `if` is laid out as a general list, with the condition on
the head's line and the branches aligned under the condition. It is, however, one of
the compound forms that is always multi-line when it has a nested body.

### Deciding every form by width alone

Rejected because it makes the layout of an identical form depend on where it lands.
The same `if` is compact at a shallow indentation and multi-line at a deep one, so a
reader cannot learn a form's shape from the form itself. The compound-form rule
trades a larger diff for a layout that is a function of the form's shape.

## Consequences

### Positive

- The formatter agrees with the canonical corpus, so `menai-format --check`
  is meaningful and can be enforced in CI.
- The rules are uniform: only the special forms, the compound forms, and bindings
  are exceptions, and everything else follows one general-list rule.
- Flattening `and`/`or` removes the cascade's compounding indentation and makes
  long condition chains readable.
- A compound form's layout is a function of its shape, not of where it lands, so
  the reader learns the shape of a form from the form itself.
- The formatter's structure is the corpus's structure, which makes it a sound
  basis for reporting where parentheses fail to balance.

### Negative

- The corpus has a handful of local inconsistencies that no rule reproduces (for
  example a standalone two-operand `or` laid out multi-line where a larger one is
  compact). Reformatting normalises these, so the first format pass over the
  standard library is a non-trivial diff.
- Forcing compound forms multi-line expands many forms that would have fit,
  increasing the line count of the corpus. This is accepted deliberately:
  consistency is preferred over the smallest number of source lines.
- The width is a free parameter. 100 was chosen to minimise churn, not because it
  is uniquely correct.
