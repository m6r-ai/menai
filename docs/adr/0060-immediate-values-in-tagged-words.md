# ADR-0060: Values that fit in a word are immediates, not heap objects

Date: 2026-11-08  
Status: Accepted

## Context

Every Menai value is a reference-counted pool block, and the reference count is
the mechanism the VM pays for on every assignment. For a value with no payload
that is a poor trade: `#none`, `#true` and `#false` are the result of a
predicate, of a comparison, of `and`/`or`, and of every "not found" path in the
collection operations. The overwhelming majority of those results are written
into a register and dropped shortly afterwards, without ever being stored in a
container that outlives the frame.

Because the pool allocator keeps a per-instance free list and small integers are
cached as singletons, the cost of these values was never an allocation. It is the
reference count itself: two cache touches per value, on the two hottest inlines
in the VM, paid for a value that has no contents to protect.

The purity of the language is what makes this worth fixing. Values are immutable
and nothing can alias a boolean, so a boolean does not need identity, ownership,
or a lifetime. It only needs to be a value.

## Decision

Values whose entire content fits in a machine word are represented as immediates
inside the word itself, distinguished by a tag in the low bits. A pointer to a
pool block always has those bits clear, because the pool prepends its header to a
block aligned for any type, so the tag costs nothing and needs no separate word.
Tag zero means "heap object"; the remaining tags name the immediate kinds.

Five kinds are immediates: `#none`, `#true`, `#false`, a fixnum, and a float whose
exponent falls in a window the payload can hold. An integer outside the fixnum
range is a boxed `MenaiInteger`, and that box holds a `MenaiBigInt` and nothing
else.

Integers therefore have exactly two representations, and the fixnum range uses the
whole payload. This is a tagged word, so it is pointer-sized: every supported target
has 64-bit pointers and the same 61-bit payload, and the range does not vary with the
width of a C long. Bounding it by a 32-bit `long` would box values the word can
already hold.

The float window is a different compromise. A double needs sign, 11 exponent bits
and 52 mantissa bits; 61 bits leaves 9 for sign and exponent, one short of a full
exponent. So the inline form keeps the whole mantissa and covers 256 consecutive
exponents at full double precision — the width of the binary32 exponent range,
without giving up a mantissa bit. Zero, denormals, NaN, the infinities and anything
outside the window stay boxed. The window is offset rather than being an aligned
block of 256, because an aligned block cannot cover both 1.5 and 3.0.

An earlier revision kept a third form — a boxed integer with an inline `long` — to
cover the range above the fixnum maximum. Widening the fixnum range to the payload
removed the reason for it: it held only a sliver of values, and it forced every
arithmetic path to decide between three representations rather than two.

The constructor is the single point that chooses a representation, and it chooses
from the value alone. So no integer exists in two forms at once, and an integer that
leaves the range and comes back is indistinguishable from one that never left. That
canonicalisation is not a nicety: hashing and equality treat the fixnum and boxed
paths separately, and they agree only because a given value can only take one of
them. Anything that creates an integer outside the constructor would break dict and
set behaviour.

An immediate is not allocation-free in the sense of being cached: it is not an
object at all. Nothing owns it, nothing counts it, and no destructor runs for it,
so retain and release are no-ops for it and a register write is a plain store.
Each immediate value has exactly one encoding, which makes word identity value
equality for the immediate types.

## Alternatives considered

### Immortal reference-count sentinel

Reserve a sentinel count meaning "never freed, never counted", and set it on the
`#none`/boolean singletons and an enlarged integer cache. This makes cached values
as cheap to retain as an immediate, with no change at any dereference site.

Rejected because for exactly the types that become immediates it is dominated by
the immediate: it still allocates the object, and it still reads the word before
deciding not to count it. It cannot help a computed integer at all, which is where
the remaining cost is. For any value that is genuinely boxed for its whole life —
a float or string constant in a code object's constant pool — the sentinel remains
the only option, but that is a different and much smaller set, and there is no
reason to decide it now.

### Fat value: an explicit tag plus payload word

A value becomes a struct of a tag and a union of a pointer and a machine word.
Type dispatch becomes a field read with no masking and no pointer arithmetic.

Rejected because it doubles the width of every register slot, closure capture,
list cell, dict element and vector element. The register-based VM's hot loop is
dominated by moving values between slots, so paying two words per value to make a
tag test branch-free is the wrong trade: the tag test is a predictable branch on a
word that is already in a register.

### NaN boxing

Store every value in one double: floats as themselves, pointers and immediates in
the payload space of a quiet NaN.

Rejected. It is the only scheme that unboxes *every* float, including the zero,
denormal, NaN and out-of-window values the tagged form still boxes, and it also
folds the fixnum step into the same change. But it makes every value word a double,
depends on pointers fitting in the 48–51 bit NaN payload on every platform a wheel
is built for, and requires canonicalising the NaN payload that arithmetic produces.
The tagged form gets most of the benefit for a fraction of that risk, and it pays it
per type rather than all at once. NaN boxing would supersede this ADR, not extend it.

## Consequences

### Positive

- Predicates, comparisons, `and`/`or`, and the "not found" paths of the collection
  operations no longer touch the allocator or a reference count. Benchmarks are
  neutral to positive, with gains up to 6–7% on the suites that are dominated by
  control flow and collection misses.
- Fixnums extend that to arithmetic. Integer-heavy work gains far more, because a
  computed integer used to allocate and free a pool block on every operation once it
  left the old small-integer cache: the compression, archive and image suites
  improved by 10–34%.
- The per-instance `#none` and boolean singletons, and their lifecycle in the VM
  state, disappear entirely.
- The small-integer cache and its per-instance singleton array disappear too: the
  fixnum range subsumes them, and a computed value outside the cache used to be the
  common case rather than the exception.
- Inline floats remove the box from most float arithmetic. The measured gain is
  modest — low single digits on the statistics suite — because that suite is
  dominated by comparator calls and guards rather than by allocation, but the guards
  themselves got cheaper, since a type check on an inline float is a tag comparison
  rather than two header loads.
- Type checks for the immediate types get cheaper and simpler: they are a tag
  comparison on a value that is already in a register, where they used to be a load
  from a header eight bytes below a possibly cold pointer.

### Negative

- The invariant this introduces is the dangerous part: **a `MenaiValue` must be
  tag-tested before it is dereferenced.** Reading a header, testing `ob_type`,
  incrementing `ob_refcnt`, or casting to a `MenaiFoo *` on an immediate is not a
  wrong answer, it is a fault. This is easy to overlook in a new opcode or a new
  builtin, and the resulting crash is far from the code that caused it. Every
  place that turns a value into a type or a header must carry the test: the
  retain/release pair, the `IS_MENAI_*` tests, the value free/hash/equal
  dispatch, the bridge conversion to a Python value, and the collector's marking
  and edge-breaking.
- The tag is part of the interface between the C VM and its embedding bridge.
  Tagged words must never be handed out as pointers, so every conversion across
  that boundary has to go through the tag-aware dispatch rather than the header.
- ISO C does not bless pointer tagging. It is well defined on every ABI in the
  release matrix, and neither pointer authentication nor top-byte-ignore collides
  with the low bits, but the technique is outside the letter of the standard and
  should not be used as precedent for further type punning.
- Adding an immediate type is not local. It extends the tag space, which means
  revisiting type dispatch, hashing, equality, the finalizer, the cycle collector,
  the leak detector and the bridge in the same change.
