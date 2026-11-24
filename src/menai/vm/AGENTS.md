# AGENTS.md - Menai C VM

Guardrails for `src/menai/vm/`: the C VM (`menai_vm_c.c` and its companions), the bridge to
Python, the value representation and the collector. `vm/menai_vm.py` is a thin wrapper that
exposes the C VM's `execute` and `cancel` to the rest of the codebase.

The root [`AGENTS.md`](../../../AGENTS.md) covers how to work in the repository, and
[`../AGENTS.md`](../AGENTS.md) covers the compiler that produces the bytecode this VM runs.

## The C VM has no process-global mutable state

All mutable VM state (pool allocator free-lists, singletons, the closure registry) is owned by
`MenaiVMState`, a per-instance struct allocated by the Python `MenaiVM` wrapper. A
`MenaiVMState *` is passed explicitly as the first argument to every C function that
allocates, frees or touches singletons; there are no file-level mutable statics outside the
bridge, which holds only read-only Python type references fetched at module init.

`menai_value_retain` is the one exception, since it only increments `ob_refcnt`. `MenaiCodeObject`
retain/release also omits the state pointer: C code objects are ephemeral (built and destroyed
within one `execute()` call) and never shared across instances or threads.

See [ADR-0006](../../../docs/adr/0006-no-process-global-mutable-state-in-c-vm.md).

## A `MenaiValue` must be tag-tested before it is dereferenced

`#none`, the booleans, integers in the fixnum range and floats in the inline window are
immediates: the value lives in the word and the low bits carry a tag, with tag zero meaning
"pointer to a pool object". A tagged word is not an address, so reading a pool header, testing
`ob_type`, touching `ob_refcnt` or casting to a `MenaiFoo *` will fault — and fault a long way
from the code that caused it.

Every place that turns a value into a type or a header carries the test first. These are the
choke points to check when adding an opcode, a builtin, a value type or a helper that walks
values:

- `menai_value_retain` / `menai_value_release` are already tag-aware. Do not inline a bare
  `ob_refcnt` adjustment anywhere else.
- The `IS_MENAI_*` macros are already tag-aware. Never write a bare
  `menai_get_pool_header(v)->ob_type == ...` comparison outside them.
- `menai_value_free`, `menai_value_hash`, `menai_value_equal` and the bridge's
  `menai_value_to_slow_value` each dispatch on the tag before the header.
- The cycle collector: `gc_mark_value`, `_gc_is_dead` and the Phase 3 capture edge-breaking.
  An immediate needs no count adjustment.

Immediates also have one canonical encoding each, which is why word identity is value equality
for them — but that is a property of the immediate types only, not a licence to compare values
by address.

### Integers are the awkward case

An integer is exactly one of two things, a fixnum immediate or a boxed bignum, and the forms
are mutually exclusive. Do not dereference a value you have only proven to be an integer:

- `menai_is_fixnum(v)` — an immediate, in `[MENAI_FIXNUM_MIN, MENAI_FIXNUM_MAX]`. The
  arithmetic fast paths branch on this and never touch memory.
- `menai_integer_is_big(v)` — a bignum; for a value known to be an integer this is just
  `!menai_is_fixnum`. Narrow with `menai_integer_boxed(v)` and use `->big` rather than
  repeating a cast at every use.
- `menai_integer_to_int64(v, &out)` — either form as an `int64_t`, returning
  `MENAI_ERR_OVERFLOW` when a bignum does not fit. A fixnum always fits, so this is the
  extraction the opcode fast paths use: it settles the representation question without
  narrowing the value. Callers keep their own error code, because "too large" is a
  different diagnosis for a shift count, an index and a radix.
  `menai_integer_to_long` and `menai_integer_to_long_long` narrow on purpose, and so report
  an out-of-range value themselves rather than leaving it to the caller.
- `menai_integer_to_fixnum(v, &out)` — a fixnum's value, and `MENAI_ERR_OVERFLOW` for a
  bignum. This is what an opcode taking an index, offset, slice bound or field index uses:
  a bignum can never be a valid one, so refusing it loses no diagnosis beyond which
  out-of-range error gets named, and it keeps the bignum path out of the opcode body. Use it
  only where the operand cannot be legitimate: `SWITCH_INTEGER`, `FUNCTION_ACCEPTS_P`,
  `RANGE` and `FLOAT_LDEXP` are the counterexamples, where a bignum operand is a real value
  and the opcode must keep using `menai_integer_to_int64`.

An intermediate that holds an integer's value is `int64_t`, never `long`. The fixnum
payload is 61 bits on every target while a C long is 32 bits on Windows, so a fast path
written in `long` truncates the operand there and the same expression yields a different
value per platform: `integer-gcd` of two large fixnums, or a `range` over them, returns a
wrong answer silently rather than boxing. `long` is only for values that really are C-long
sized — a byte value, a radix that has been validated, a count compared against a small
field — and a value on its way into one is range-checked, not cast.

`alloc_menai_integer_from_*` returns a `MenaiValue *`, not a `MenaiInteger *`, because the
result is usually a fixnum. They are the only place that chooses a representation, and they
choose from the value alone. A second path that built an integer would produce two forms of
one value and break hashing, equality and every dict keyed on it — which is also why
`alloc_menai_integer_from_bigint` must test the fixnum range rather than "fits a long".

### Floats follow the same pattern with a different window

A float is inline when its exponent is inside `[MENAI_FLOAT_EXP_BASE, MENAI_FLOAT_EXP_MAX]`
and boxed otherwise; zero, denormals, NaN, the infinities and magnitudes outside the window are
the boxed cases. Read a float through `menai_float_value(v)`, never
`((MenaiFloat *)v)->value`. `menai_float_is_inline(v)` is the range test, and
`alloc_menai_float` is the only constructor and the only thing that may choose the form. The
inline encoding keeps the full 52-bit mantissa and uses an *offset* exponent window, because an
aligned 256-exponent block could not cover both 1.5 and 3.0.

`menai_hash_double` collapses both zeros to one bucket, because IEEE equality says
`0.0 == -0.0` and a dict or set must not miss a key that compares equal; the sign survives in
the value, so `float-copysign` and division still observe it. It also normalises every NaN to
one pattern for the same reason.

See [ADR-0060](../../../docs/adr/0060-immediate-values-in-tagged-words.md).

## The closure cycle collector only frees closures with refcnt == 0

The collector runs at the end of every `menai_vm_execute_native` call and at teardown. It marks
closures reachable from globals and the execute result, then sweeps unreachable ones. Phase 3
breaks internal edges (dead-to-dead capture references) with bare refcount decrements, so Phase
4 must only call `menai_value_free` on closures whose `ob_refcnt` is exactly 0. A closure with a
positive refcount has an external reference — for example from a code object's constant pool,
which is released after the collector runs — and must be returned to the registry instead.

The collector does not trace code object constants as roots. That is safe because closures in
constants are non-cyclic (the bridge strips captures when round-tripping), and the Phase 4
refcount guard prevents a use-after-free when such a closure is unreachable from roots but
still held by the code object. The `MENAI_DEBUG_LEAKS` build monitors this.

See [ADR-0016](../../../docs/adr/0016-reference-counting-with-closure-cycle-collection.md).

## An update opcode may write into a uniquely-owned collection, but only on four conditions

Menai values never change, so `vector-with`, `dict-with` and their relatives allocate a
replacement. They also update in place when nothing else can observe the collection, which is
what makes an accumulating loop O(n) rather than O(n²). The conditions, all of which must hold,
are listed at the `OP_VECTOR_WITH` fast path; the two that are easy to get wrong are:

- `dest == src0` is required, not merely `ob_refcnt == 1`. A register that is still live below
  the instruction holds a reference, so a count of one does not mean the value is unobservable:
  `(let ((a (vector 1 2 3))) (list a (vector-with a 0 8)))` has one reference and must not
  mutate. Only the slot allocator knows when a reference is being consumed, and `dest == src0`
  is the runtime-visible form of that.
- A slice view can be uniquely owned, so the ownership test is independent of the count:
  writing through a view's element pointer would corrupt the vector it views.
- A dictionary records `cap`, which may exceed `length`. A uniquely-owned dictionary that
  is appended to in place uses the spare slots and is grown geometrically when full, so a
  sequence of appends is O(n) overall rather than O(n²). A *shared* dictionary is still
  allocated tight: slack is only requested by the in-place path.
- Bytes work the same way, with `cap` replacing the dictionary's, because an append also
  changes the length (`menai_bytes_reserve`, ADR-0064). They need no self-reference test — a
  byte sequence holds only octets — but they do carry a `hash` field that a lookup reads
  without retaining the value, so every in-place bytes write must set `hash = -1`. A missing
  clear is a silent lookup failure, not a wrong value, so it is the one invariant here that
  the type system cannot help with.
- Strings work the same way as bytes (`menai_string_reserve`, ADR-0065), with one difference:
  `string-slice` and the trims produce slice views into their source (ADR-0066), so the
  ownership test is required here. A view is always allocated exactly tight, so an append to
  one would copy on growth in any case — the test states the invariant rather than depending
  on that. A string also caches its hash, under the same obligation as bytes'.

The fallback is always correct — it is what the opcode did before — so this is an optimisation
that can only cost time. Never express the prediction as an `assert`: a constant-pool entry, a
closure capture or another collection's element can each hold a second reference to a value
whose only register is the one being overwritten, which is an ordinary legal program rather
than a compiler bug, and `/DNDEBUG` is on in the Windows build.

An accumulating loop only stays in place while its accumulator is a loop-carried variable of
the loop that performs the write. A helper that survives inlining is called with the collection
as an argument, receives it through a `MOVE` into the outgoing slot, which retains, so every
write inside the helper copies — and there is no diagnostic when a refactor moves the write
across that boundary.

Which helpers survive inlining is the inliner's decision, not a property of the accumulator.
A small non-recursive lambda used once or twice is inlined at its call sites, and the write it
contains then happens in the caller's registers, in place, so a higher-order fold or a named
helper is not disqualifying on its own. What cannot be inlined is a recursive lambda (the
merge in `sort-vector`) and one the inliner leaves as a real call. So `sort-vector` must stay
one flat loop because its merge is recursive, not because helpers are inherently wrong.

`dest == src0` also means **one update per iteration**. Two updates chained in one iteration
consume each other's results, so each needs a register distinct from the accumulator and at
most the outermost one can write into it. `(bytes-append-u8 (bytes-append-u8 acc a) b)`
therefore copies the whole accumulator twice per iteration and is quadratic: four nested
appends per iteration building 87 KB took 55.7 ms against 1.14 ms for the same output written
by one append per iteration. Append a single piece per iteration and carry the rest of the
decision as loop state; do not nest updates to save iterations or to avoid recomputing an
operand.

Do not generalise from an inlined helper to a hand-written flat loop: the flat loop is no
faster once the helper is inlined, and it is much harder to read.

Iterate a dictionary or set with `length`, never with `cap`: slots past `length` are
uninitialised, and `menai_dict_final` and `menai_set_final` depend on that. The hash table
borrows its keys from the elements array, so replacing an element must call
`menai_ht_replace_key` and shifting elements must clear and re-insert the table.

A two-operand operation (`dict-merge`, `set-union`, `set-intersection`, `set-difference`)
also needs `b != a` in the in-place test: a count of one does not exclude the operands
being the same object, because both operands may read the same register. Order the work so
every fallible step comes first — size the table for the worst case up front — because a
failure after a partial mutation would leave a collection that later passes cannot
describe.

`struct-with` and `struct-with-indexed` share `menai_struct_set_exclusive` and need no
capacity: a struct's field count is fixed. They still need the self-reference test, because a
record may legally hold a record. Structs are hashable, so unlike the collections the
uniqueness test is also what keeps an enclosing struct's cached hash valid.

The copying happens inside an opcode body, so **executed instruction counts do not change** and
the opcode profile cannot see this class of change at all.

See [ADR-0063](../../../docs/adr/0063-in-place-update-of-uniquely-owned-collections.md).

## A VM error code that needs context gets a typed field for it

`MenaiVMError` carries the context an error code needs, set at the raise site in
`menai_vm_c.c` and threaded to the bridge along with the code and execution context. The C VM
records raw values; `menai_vm_errors.translate_vm_error` formats the message. Message text must
not be composed in C — the Python error table is the single source of truth for the
code-to-message mapping.

`user_value` is the precedent (the raised value for `USER_ERROR`, the variable name for
`UNDEFINED_VARIABLE`); the arity fields (`arity_expected`, `arity_actual`, `arity_callee`) are
the second case. A field shaped to its context is preferred over a general-purpose
formatted-detail string, which would split formatting across two languages.

A field holding a pointer that must outlive the code objects — a name, a string — is a
`strdup`'d copy owned by the error struct and freed by the bridge after translation, because
the code objects are released first. The bridge frees it on every path, and
`menai_vm_execute_native` resets it on entry so a run that errors and is never translated does
not leave a stale pointer.

See [ADR-0048](../../../docs/adr/0048-vm-errors-carry-structured-diagnostic-context.md).

## Debug build flags

The C VM supports compile-time debug features via preprocessor defines:

- `MENAI_DEBUG_LEAKS` — tracks every `MenaiValue` allocation in a per-instance hash set and
  reports anything still tracked at teardown (excluding known singletons) to stderr. Build
  with `make build-leaks`. Used to verify that reference counting reclaims all values,
  including the cyclic closures that need the collector.
- `MENAI_DEBUG_MAGIC` — every `MenaiValue` and `MenaiCodeObject` carries an `ob_magic` field
  set at allocation and cleared before the block is freed or pooled; `MENAI_CHECK_MAGIC` aborts
  on a mismatch, catching use-after-free and double-free at the point of access. Build with
  `make build-magic`, or `make build-debug` for both flags.
- `MENAI_DEBUG_GC` — traces the collector and the pool allocator to stderr: the collector's
  entry state, every closure and orphan it frees or returns to the registry, the
  register-release/collection ordering in the per-execution teardown, and three pool
  invariants (a block returned to a free-list twice, a free-list whose links form a cycle, and
  a closure freed while still in the registry or on a free-list). Build with `make build-gc`.

The `MENAI_DEBUG_GC` signals are what distinguish a lifetime bug from a functional one when a
`MenaiVMState` is reused across many `execute()` calls — the configuration that exposes
registry and pool corruption a fresh VM per run never reaches. `menai-test` reuses one instance
across every leaf of a file by default, so running it against this build exercises that path
(pass `--isolate` to run each leaf on a fresh instance).

## C formatting

Do NOT use lines of characters in comments. E.g. never use something like:

```c
/* --------------------------------
 * This is a bad comment - don't do this!
 * -------------------------------- */
```

For single line comments put the open and close of the comment on the same line:

```c
/* This is a good single line comment */
```

For multi-line comments the open and close go on their own lines:

```c
/*
 * This is a great multiline comment.
 * Where we have more than one line of text.
 */
```

Do NOT use excess whitespace to line up things on adjacent lines. E.g. never do:

```c
int x_with_long_name = 0;
iny y                = 1;
```

Instead do:

```c
int x_with_long_name = 0;
int y = 1;
```

Do NOT put code on the same line after an opening brace. E.g. never do:

```c
if (foo) { something(); }
```

Instead do:

```c
if (foo) {
    something();
}
```
