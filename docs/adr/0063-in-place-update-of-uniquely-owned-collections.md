# ADR-0063: Uniquely-owned collection values are updated in place

Date: 2026-11-19
Status: Accepted

## Context

Menai is pure: a value never changes, and every operation that appears to update a
collection in fact builds a replacement. `vector-with`, `dict-with`, `set-with` and
their `-without` counterparts therefore copy the collection on every call.

For the idiom that builds a collection one step at a time this is quadratic. A
`let`-bound tail-recursive loop is flattened into a self-loop with the accumulator
in a register, and each iteration copies the whole accumulator:

```menai
(let ((loop (lambda (i acc)
              (if (integer=? i n) acc (loop (integer+ i 1) (vector-with acc i x))))))
  (loop 0 (vector n 0)))
```

The VM already reference counts every `MenaiValue`, so it can tell when nothing else
holds a particular object. When a collection is the sole owner of itself, replacing
an element of it is not observable by any part of the program, and the copy is
avoidable.

## Decision

A VM update opcode may replace an element of a collection in place when **all** of
the following hold:

1. **The destination field equals the source field**, and the source operand is a
   register rather than a constant-pool index.
2. **The collection's reference count is exactly one.**
3. **The collection owns its storage** — for `MenaiVector` and `MenaiBytes`, the value
   is not a slice view.
4. **The value being stored is not the collection itself.**

Condition 1 is the one that needs explaining, because it is the only one that is not
about the value. Condition 2 on its own is **not sufficient**. In

```menai
(let ((a (vector 1 2 3)))
  (list a (vector-with a 0 8)))
```

`a` has exactly one reference — the register that is still live below the
`vector-with` — and replacing element 0 in place would change the value the program
goes on to read as `a`. Reference counting cannot distinguish "this is the only
reference" from "this is the only reference and it is being consumed"; only the
allocator knows that. `dest == src0` is the runtime-visible proxy for it: the result
is written into the register the collection was read from, so that register's
reference is consumed by the write and the old value becomes unobservable.

Condition 3 is independent of condition 2. An owning slice view is reachable with a
reference count of one, and writing through it would corrupt the vector it views.
Conversely an owner with a live view also has a count above one, because a view
retains its owner — so the two conditions agree in that direction, but the ownership
test is the one that must be written down.

Condition 4 guards against a cycle. A collection holding itself is an infinite
structure that no reference count could reclaim, because the collector reclaims
closures only. Reference counting does not exclude this case: when the source
operand also appears as the value operand, the two fields read one register.

The check is worth its cost even though it can fail. It replaces an allocation and
an O(n) copy with a load and a compare, and the opcode must contain a conditional
anyway: the allocating path is still needed for condition 3, which no amount of
compiler analysis can remove. See "Why the runtime check is not optional", below.

The check is written inline in the update opcode rather than behind a helper. It has
one call site, and the retain-before-release ordering and the bare `break` that leaves
the reference in its register are only correct in the context of that instruction, so
they read better where they are used.

### Collections with variable length also carry capacity

`vector-with` replaces an element and never changes a vector's length, so it needs no
capacity field. Dictionaries and sets are different: appending is the common case, and
`elements` is a flexible array member inside the collection's own block, so it cannot grow
in place. A dictionary therefore records `cap`, the number of slots its inline array holds,
which may exceed `length`.

An in-place append uses the spare slot when there is one. When there is not, the dictionary
is reallocated with `cap = 2 * (length + 1)` and copied once, so a sequence of `n` appends
copies `n + n/2 + n/4 + ... = 2n` element pointers in total: O(n), where copying at every
step would be O(n²). The hash table is grown separately by `menai_ht_ensure`, because
`ht.slots` is its own block and only needs the space its current load requires. Removal
needs no shrink: the array keeps its capacity and the table is rebuilt in the existing
allocation by clearing and re-inserting the survivors.

The slack is only ever asked for by the in-place path, so a dictionary that is shared
rather than mutated is still allocated tight and carries no spare memory. This does mean
the allocating path is not always "exactly the old behaviour": when the source is uniquely
owned but full, it deliberately over-allocates. It is still always *correct*.

Sets and dictionaries share the mechanism. A set has one simplification: it has no
update-existing case, because adding an element that is already present returns the set
unchanged with no allocation, so only appends and removals need the treatment. It also needs
no self-reference test — a set's element must be hashable and a set is not, so the hash check
already rejects a set containing itself.

### Two-operand operations need one extra condition

`dict-merge` and `set-union`, `set-intersection` and `set-difference` take two collections, so
the test gains `b != a`. A count of one does *not* exclude the operands being the same object,
because both may read the same register: `(set-union s s)` has one reference in total. Every
in-place path also needs all its fallible work done first, which is why the hash table is sized
for the worst case up front — over-sizing a table is harmless, whereas discovering mid-way that
it needs to grow would leave a half-updated collection behind.

The two families differ in what they require of capacity:

- **Merge and union only grow.** They are done in place when the receiver has room for
  `length + other length`, the worst case being every one of the other operand's entries being
  new. Otherwise the result is allocated with headroom, so a fold of merges does not rebuild
  from scratch at every step.
- **Intersection and difference only shrink.** They need no capacity at all and cannot fail
  part-way: the surviving members are compacted down the existing array and the table is
  rebuilt in its existing allocation. These are therefore unconditional on capacity, and only
  require the receiver to be uniquely owned.

Elements continue to be shared rather than copied, so a merge or union of two collections
retains the other operand's element objects directly instead of duplicating them.

### Structs are the simplest case, and the weakest win

`struct-with` and `struct-with-indexed` replace one field of a fixed-size record, so they need
neither capacity nor a hash table: the fast path is `retain(new); release(old); items[i] = new`.
The conditions are the same as everywhere else, and the value being stored must not be the
struct itself — a record may legally hold a record, so unlike a set this cannot be left to the
hash check. The two opcodes share `menai_struct_set_exclusive`.

This is the one member of the family that buys no asymptotic improvement. A struct's field
count is fixed by its type declaration, so a loop that updates a field is already linear in the
loop count; the fast path removes one small allocation and k retain/release pairs per update.
It is therefore worth doing for uniformity, and because it makes the functional-update form as
cheap as the hand-rolled positional reconstruction it replaces, but it cannot be justified by
the reasoning that carries the variable-length collections.

Structs are hashable when their fields are, so the uniqueness test does extra work here that it
does not for vectors, dicts or sets (which are unhashable): it guarantees no enclosing struct
already used as a dict key or set member can have its cached hash invalidated. It holds for the
same reason as elsewhere — a container retains the struct, putting the count above one.

## Alternatives considered

### A PATCH-style opcode with no destination operand

`PATCH_CLOSURE` establishes the shape "mutate the value named by src0 and leave the
result there". A `PATCH_VECTOR_WITH` would make `dest == src0` structural rather than
checked, and would drop the `dest` release.

It was rejected as redundant. The emitter would only be able to emit it where the
allocator had already assigned the result to the source slot, which is exactly the
case the runtime test recognises — so the opcode would encode nothing the VM cannot
read off the instruction word, at the cost of a second opcode, a validator entry and
a renderer case. If a use ever appears for consuming the source *and* writing the
result elsewhere, this is the decision to revisit.

### A compiler-emitted "consume src0" flag

This would be strictly more general, because it could consume the source reference
even when the result lands in a different slot. It was rejected because it can only
be correct if the VM also releases the source slot: the VM does not reclaim a dead
register until something overwrites it (`OP_RETURN` releases only the code object;
the register file is swept once, at the end of `execute`). A consume flag without
that release leaves the collection at a count of two for the rest of the function, so
the fast path fires once and then silently disables itself. Measured on the standard
library and benchmark corpus, the additional reach did not justify a new instruction
encoding plus an unverifiable deadness claim.

### Assert that the reference count is one

The emitter's `dest == src0` decision and the reference count are independent claims,
and a mismatch is a compiler bug rather than a user error, so an assertion is
tempting. It was rejected because the two are not in fact independent at the
granularity that matters: a register and a constant-pool entry, a closure capture or
another collection's element can each hold a second reference to a value whose only
*register* is the one being overwritten. Those are ordinary legal programs, not
compiler failures. `(let ((v (vector 1 2 3))) (loop i 0 (vector-with v i x)))` has a
count of two on its first iteration, because the code object owns the literal.

The assert would also be unreliable in exactly the build that ships: the MSVC default
compile flags include `/DNDEBUG`, so an `assert` would be live on macOS and Linux and
compiled out on Windows, turning the trap into a silent wrong answer on one platform.

## Consequences

### Positive

- Building a collection one element at a time is amortised O(1) per element rather
  than O(n), for the accumulator idiom the language encourages.
- No compiler change, no bytecode format change, no change to the slot allocator, the
  peephole or the validator. The whole mechanism is one branch in the opcode.
- Values that are shared — a literal, a captured value, a container element, a
  function parameter — keep the allocating path, so no observable behaviour changes.

### Negative

- Executed Menai instruction counts do not change at all, because the copying happens
  inside a C opcode body. The opcode profile, which is this repository's standard
  measurement, is blind to the optimisation; allocation counts or wall-clock time on
  a large enough input are the only things that show it.
- Each update opcode carries a branch that fails on a large share of dynamic
  executions. Measured over the benchmark corpus at its smallest case sizes, 34% of
  executed collection-update instructions take the fast path (58.7% of `dict-with`,
  23.4% of `vector-with`). The misses are dominated by source operands that are
  fixed slots — parameters, captures, loop-carried variables — which the allocator
  can never assign as a destination.
- Many of those misses could not be optimised under any encoding: a captured
  collection always has a count above one because a closure retains its captures,
  and a parameter is reached through a `MOVE` into the outgoing slot, which retains.
  Uniqueness and slot reuse are close to the same property, which is why the cheap
  test and the sound cases line up.

### Neutral

- The check must never be an `assert`. The fallback is always correct — it is the
  behaviour that existed before — so a wrong prediction costs time, not correctness.

## Why the runtime check is not optional

`(vector-with (vector-slice v 0 2) 0 9)` is legal Menai. A slice view's element pointer
targets its owner's inline storage, so an in-place write through the view would corrupt
the owner, and the view can be uniquely owned. `vector-slice` produces a vector whose
ownership is not known at compile time and cannot be proven either way, so the
allocating path is required regardless of how much analysis the compiler performs.
Adding the reference-count test to a conditional that already exists costs a cached
load and a compare.
