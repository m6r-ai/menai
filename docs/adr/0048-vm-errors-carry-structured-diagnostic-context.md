# ADR-0048: VM errors carry structured diagnostic context

Date: 2026-10-12  
Status: Accepted

## Context

The C VM reports a runtime error as a `MenaiVMError` struct holding an error code and
execution context (opcode, instruction pointer, call depth). The bridge packages that
into a `_MenaiVMRuntimeError` sentinel, and `menai_vm_errors.translate_vm_error` maps
the code to a Python exception with a message.

The message a code maps to is fixed in a table. For most codes that is enough: the code
says what went wrong. But some codes say what went wrong without saying *where* or
*with what*, and the context needed to act on them is known at the raise site and thrown
away.

`MENAI_ERR_ARITY_MISMATCH` was the clearest case. It is raised at ten sites, each of
which knows the callee's declared parameter count and the number of arguments supplied.
None of it survived. The user saw:

```text
Error: arity mismatch

Call stack:
    1: <module>
```

The backtrace does not help when the failing call is at module top level, because there
is no function name to show. The error is unactionable: it names neither the callee nor
the mismatch.

`MENAI_ERR_UNDEFINED_VARIABLE` already solved this shape of problem by carrying the
variable name in the `user_value` field. That established the pattern — a code whose
message needs context gets the context threaded to the bridge — but `user_value` holds a
`MenaiValue *`, which is the wrong shape for a pair of integers.

## Decision

### An error code that needs context gets a field for it

`MenaiVMError` gains fields for the context an error code needs, set at the raise site
and threaded to the bridge alongside the existing `user_value`, backtrace, and
frame-dump fields.

For `MENAI_ERR_ARITY_MISMATCH` and `MENAI_ERR_STRUCT_ARITY_MISMATCH` the fields are:

```c
int arity_expected;     /* callee's declared parameter count, or -1 */
int arity_actual;       /* arguments supplied by the caller, or -1 */
char *arity_callee;     /* strdup'd callee name, or NULL */
```

`-1` means "not an arity error", so a site that does not set them leaves them inert.

`arity_callee` is a `strdup`'d copy, freed by the bridge after translation, for the
reason the backtrace strings are copied: the code objects are released before the
bridge runs, so a borrowed pointer to `co->name` would dangle. The bridge frees it on
every path, and `menai_vm_execute_native` resets it to NULL on entry, so a run that
errors and is never translated does not leave a stale pointer.

`translate_vm_error` formats the message from the fields:

```text
arity mismatch: "expect-pass(2 params)" expects 2 arguments but got 3
```

### The context is threaded, not formatted in C

The C VM records the raw values; the Python error table formats the message. This keeps
message text in one place — the error table, which is already the single source of truth
for code-to-message mapping — rather than splitting it between C and Python. The C side
does not know how an arity mismatch should read.

## Alternatives considered

### Add a general-purpose formatted-detail string to MenaiVMError

Have any raise site populate a `const char *detail` with a pre-formatted fragment, and
have the bridge append it to the message.

Rejected. It moves message formatting into C, splitting the code-to-message mapping
across two languages and two files. It also invites each site to invent its own phrasing,
which is how a message table stops being a table. A typed field per context keeps the
formatting where the table is.

### Reuse user_value with a synthetic MenaiValue

Pack the expected and actual counts into a value and carry them in `user_value`.

Rejected. `user_value` is a `MenaiValue *` and its meaning is "the value the error is
about" — the raised value for `USER_ERROR`, the variable name for
`UNDEFINED_VARIABLE`. Overloading it with a pair of integers would make its type and its
meaning both depend on the code, which is exactly the kind of implicit coupling the
typed field avoids.

### Include the callee name for struct and enum constructors too

Report the struct or enum type name in `arity_callee` for the constructor arity errors.

Rejected for now, on YAGNI grounds. A `MenaiStructType` and `MenaiEnumType` name their
type with a `MenaiString`, whose data is UTF-32 codepoints, so producing a C string
would need a conversion helper that nothing else needs yet. The constructor messages
report both counts and say "struct constructor" or "enum constructor", which is enough
to locate the call. The name can be added when a case needs it.

## Consequences

### Positive

- An arity error names the callee and both counts, which is what makes it actionable.
- The pattern for carrying per-code context is established and typed: a code that needs
  context gets a field shaped to that context, set at the raise site and formatted in
  the error table.
- Message text stays in one place, the Python error table.

### Negative

- `MenaiVMError` grows a field per code that needs context, and the bridge tuple and the
  Python sentinel grow with it. The struct is not a fixed size.
- `arity_callee` adds a `strdup`/`free` lifecycle that must be matched on every path,
  like the backtrace strings. A missed free leaks; a missed reset risks a double free.
  The `MENAI_DEBUG_LEAKS` build covers the first.
