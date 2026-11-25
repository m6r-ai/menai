# ADR-0070: An accessor applied to a container literal folds

Date: 2026-11-25  
Status: Accepted

## Context

Menai's variadic prelude functions are written as a dispatcher over a rest
argument.  `integer<?` is the shape all of them share:

```menai
(lambda (. args)
  (if ($integer=? ($list-length args) 2)
      ($integer<? ($list-first args) ($list-last args))
      (if ($integer<? ($list-length args) 2)
          (error "Function 'integer<?' requires at least 2 arguments")
          (let ((loop ...)) (loop ($list-rest args) ($list-first args))))))
```

`args` is the caller's literal list of surplus arguments, so almost every line of
the dispatcher is decided by a fact the compiler has: the list's length, and
which element sits at which position.  Two of them are constants whenever the
call's arity is written out — `(integer<? a b)` always sees a two-element list —
so the dispatch and the raise arm are unreachable.

Nothing folded them.  The AST constant folder needs every element to be a
constant, and branch constant propagation tracks constants rather than shapes.
An inlined `integer<?` therefore kept its three-arm dispatcher, allocated a
two-element list per call, and paid `LIST_LENGTH` and two `LIST_FIRST`/`LIST_LAST`
to read elements the compiler had just packed.  In a merge sort's comparator that
is per comparison.

The same fact is worth having on its own.  `(list-length (list a b))` is emitted
by every hand-rolled variadic builtin, and it costs a `MAKE_LIST` and a
`LIST_LENGTH` to discover a number that was written down.

## Decision

An accessor applied to a container literal folds to what the literal's shape
decides.  The accessors are the ones a rest-list dispatcher uses: `list-length`,
`list-first`, `list-last`, `list-rest`, `list-nth`, `vector-length` and
`vector-nth`.

Two properties of the fold matter more than its scope.

**It agrees with the VM on every shape.**  An accessor that would raise for the
literal's shape is not folded, because replacing it with a value would suppress
the raise.  `(list-first ())` and `(list-nth (list a b) 5)` stay calls.

**A folded element is still evaluated once.**  Folding a read that replaces an
accessor with an element duplicates that element's computation unless the read is
the element's only reader.  Where the literal is a binding, each element is
therefore bound to its own variable and the accessor reads the binding; the
literal itself becomes unreferenced, and dead-binding elimination drops it.
Where the literal is written inline as the accessor's argument, the fold removes
the literal with the accessor and no duplication arises.

The fold is not a value-level analysis.  It does not carry "this register holds a
two-element list" through the passes; it matches a literal's shape against the
accessor reading it.  That is enough for the dispatcher, whose reads are all of
the literal that was bound for it, and it needs no new lattice.

## Alternatives considered

### A "known list" value flowing through the passes

A richer notion of statically known shape, built from arbitrary expressions and
tracked like a type.  This is more general — it would fold a literal that reaches
an accessor through a phi or a call — but it duplicates the machinery the local
fold gets for free and needs a lattice, a join and a fixed point.  Rejected as
disproportionate to the shapes that exist.

### Fold in the CFG, where the constructor and the accessors are adjacent

The CFG has def-use counts, so "this constructor's only readers are folded" is
directly available there, and the constructor removal is visible to the profile.
Rejected because the dispatch arms' *deadness* has to be established before
branch constant propagation, which runs early in the CFG pass list, whereas the
IR optimizer runs after inlining and before the CFG is built.

### Specialise the wrapper per arity in the inliner

Rewrite a variadic lambda into a fixed-arity copy at each call site.  Rejected:
it needs an admission rule of its own, and it duplicates the wrapper's body once
per distinct arity across the program.  Once the accessors fold, the general body
shrinks to the arm the arity selects, which is the same result without the
duplication.

### Let the VM dispatch a variadic builtin's function value without a frame

A prelude wrapper value that carries its opcode and minimum arity, so a call to
it enters the opcode directly.  This would fix every builtin passed to a
higher-order function at once with no inliner work, but it needs a new value
type, a finalizer and a hashing story, and it puts prelude knowledge into the VM,
which ADR-0026 and ADR-0041 deliberately keep in the language.

## Consequences

### Positive

- A call to a variadic prelude wrapper no longer allocates its rest list, and the
  dispatch collapses to the arm the arity selects.
- `(list-length (list a b))` and its siblings fold wherever they appear, so code
  written against the dispatcher's shape is not penalised for it.
- The fold is local: it needs no fact from another pass and no state between
  rounds beyond the optimizer's own fixed point.

### Negative

- Folding `list-length` deletes the list, and with it every element that nothing
  else reads.  Purity makes that safe (ADR-0007), but it means an element
  expression that would raise is no longer evaluated.  This is the position
  dead-binding elimination already takes, and the two are now consistent; it is
  worth knowing that both exist.
- The fold introduces a binding per element of a folded literal, so a program
  whose literal is bound and read by several accessors gains locals.  Each is a
  register read replacing a list walk, which is the trade being made, but the
  local count of the enclosing function rises.
- `list-nth` and `vector-nth` fold only at a constant index.  An index computed
  at run time still walks the list, so a rest-list dispatcher that indexed its
  arguments would not benefit.

## Related

- [ADR-0007](0007-dead-code-elimination-always-safe.md) is what makes the
  constructor's removal safe once its readers are folded.
- [ADR-0049](0049-hoisting-must-not-introduce-a-raise.md) is the reason an
  accessor that would raise is left alone.  That decision governs motion rather
  than elimination, but the same observation — a raise is visible — is why this
  fold declines the shapes that raise.
- [ADR-0069](0069-loop-parameters-carry-callee-identity.md) is what makes the
  wrapper's body inlineable at a call site in the first place; this decision
  decides what the inlined body compiles to.
- [ADR-0026](0026-single-builtin-table.md) and
  [ADR-0041](0041-standard-library-module-taxonomy.md) keep prelude knowledge in
  the language, which is why specialising the wrapper is preferred to teaching
  the VM about it.
