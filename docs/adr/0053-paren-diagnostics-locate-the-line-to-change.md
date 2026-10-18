# ADR-0053: Paren diagnostics locate the line to change, not the symptom

Date: 2026-10-17
Status: Accepted

## Context

An unbalanced parenthesis is the error an AI writer hits most often, and the
parser hits it in the worst possible place: the symptom is many lines from the
mistake. A missing `)` deep in a binding's value makes the *next* binding read as
a third element of the first, so the parser reports a structural error at the
form that follows rather than at the close that is absent. A `)` one level too
shallow closes an enclosing form early, so the forms that should have been its
body are read as siblings and the parser reports a spurious second body.

Reporting the symptom is close to useless. The reader is told that a form has the
wrong number of elements, at a line where the code looks correct, and has to
count parentheses backwards to find the one to change. The diagnostic therefore
has to name the line the reader must edit.

There are two ways a file can be unbalanced, and they are duals:

- A `)` is **missing**. Some form never closes, so a form that should have closed
  is still open when the parser reaches the next form.
- A `)` is **extra**. Some form closes before its body, so a form that should
  still be open has already closed.

The evidence available for the two is not the same kind. A missing `)` leaves a
form open, and the open-form stack identifies it directly. An extra `)` has
already closed a form, so by the time the parser reaches the symptom the form is
gone from the stack; the only remaining evidence is the *shape* of the forms that
follow, which must be reconstructed.

This asymmetry is not obvious from the code, where the two techniques sit in
different error builders and look like duplication. It was investigated with the
intention of merging them into one mechanism and found not to be mergeable: at
the symptom of an early close the token-stream depth profile is identical to a
correct program's, so a depth-based rule cannot see it, while at the symptom of a
missing close the offending form is still on the stack, so shape reconstruction
is unnecessary. They are not two ways to do one thing; they are one way each to
do two things.

## Decision

A parenthesis diagnostic reports **the line the reader has to change**, derived
from the token stream, not the line at which the parser noticed the problem.

Three techniques are used, and each is required:

### The open-form stack locates a missing `)`

The token stream is scanned once to produce a depth profile and the stack of
forms still open at end of input. The innermost open form is the one whose close
is absent, and the profile shows the line where the depth first fails to return
to its starting depth. This is reported as the insertion point.

The stack is computed from the token stream rather than from the parser's own
in-progress stack, because the parser's stack diverges from the token stream once
it begins recovering from the error.

### The parser's cursor locates an unclosed value

When a binding's value is missing its closing `)`, the binding pair absorbs the
next binding and the parser reports a third element. The parser has already
parsed the value, so the end of the last token it consumed is exactly where the
missing `)` belongs. That position is the insertion point, and it is the line to
change — one line above the third element the parser flagged.

### Shape reconstruction locates an extra `)`

When a bindings list closes one binding too early, the remaining bindings are
read as body expressions. The evidence is the shape of what follows: the forms
after the bindings list, except the last, are binding-shaped (a two-element
`(name value)` form). The `)` that closed the bindings list is then the line to
change. This is the one case where the information is not recoverable from the
token-stream stack, because the close that caused it has already happened.

### A named form closed early is located by element count

A form with a required body — `let`, `lambda`, `if`, and the rest — that closes
with fewer elements than its body needs has closed before its body. The close is
the line to change. A bindings list is a bare `(` with no head symbol and so has
no minimum element count; this technique cannot see it, which is why shape
reconstruction exists separately.

## Alternatives considered

### One unified depth-based rule

Rejected on evidence. A single rule that reads the depth profile and reports
where it diverges cannot handle the early-close case: at the symptom the profile
is indistinguishable from a correct program, because the erroneous close has
already returned the depth to where the enclosing form expects it. The
distinguishing evidence is the shape of the following forms, which is a different
kind of analysis.

### Retaining the bindings-list state instead of reconstructing it

Rejected: the information does not exist at the moment of the mistake. Whether a
`)` closes a binding's value or the bindings list is not visible in the token
stream — the token and the depth are the same either way. It only becomes
decidable when the parser sees what follows.

### Reporting the symptom and letting the reader count parentheses

Rejected: this is the behaviour the decision replaces. The symptom is many lines
from the mistake, and counting parentheses backwards is exactly the task the
diagnostic exists to remove.

## Consequences

### Positive

- The reader is told the line to change, which is the line they must edit, rather
  than a line where the code reads correctly.
- Each technique is applied only where its evidence exists, so no technique is
  asked to answer a question the token stream cannot answer.
- The depth profile is included in the message, so the reader can verify the
  diagnosis and see the divergence for themselves.

### Negative

- Three techniques where a reader might expect one. The reason they cannot be
  merged is recorded here so that the next reader does not attempt it.
- The techniques live in different error builders and are reached by different
  parser branches, so a new parser branch that detects a structural error must be
  wired to the right one. A branch that reports a symptom without consulting the
  diagnosis will regress to the old behaviour.
- Shape reconstruction is a heuristic on form shape. It is only reported when
  every form after the bindings list except the last is binding-shaped, so a
  genuine second body is not misreported, but a program whose body happens to be
  binding-shaped throughout could in principle be misdiagnosed. The diagnosis
  names the position it inferred, so the reader can reject it.
