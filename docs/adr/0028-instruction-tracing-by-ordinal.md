# ADR-0028: Instruction tracing keyed by walk-order ordinals

Date: 2026-09-23  
Status: Accepted

## Context

The VM collects an opcode histogram: how many times each *kind* of opcode ran.
That answers "how much work is arithmetic vs. calling", but it cannot answer
"which instruction is hot" or "which function dominates", because every
instruction of a given opcode collapses into one bucket and nothing is
attributed to a function or source location.

Finding hot spots needs per-*instruction* execution counts and per-function call
counts, attributed to the disassembly so a hot instruction can be read against
the disassembler output.

The obstacle is identity. The C VM does not execute the Python `CodeObject`
tree; each `execute()` call converts that tree into a fresh native
`MenaiCodeObject` tree and destroys it when the call returns. Native code
object pointers are therefore ephemeral — they differ on every call and dangle
the moment execution ends — so they cannot be used as trace keys. A trace key
must be something stable that outlives the native tree.

A second constraint is that the C VM has no process-global mutable state
(ADR-0006): all mutable state lives in `MenaiVMState`, one per Python `MenaiVM`
instance.

## Decision

Trace counts are keyed by **walk-order ordinals** rather than by pointers.

`menai_render.menai_render_walk.walk_code_objects` defines a canonical ordering
of the code tree: depth-first, root first, children in list order, with a global
instruction ordinal assigned contiguously in visit order and a code ordinal per
code object. The C bridge assigns the same ordinals during conversion, stamping
each native code object with its code ordinal and instruction base. The VM then
records:

- `instr_counts[i]` — executions of the instruction with global ordinal `i`;
- `call_counts[c]` — calls to the code object with code ordinal `c`.

Both arrays live in `MenaiVMState`, are sized to the converted tree at the start
of each `execute()` call, and are zeroed so a trace reflects a single run.

Because the ordering is a pure function of the tree structure, the same Python
tree always yields the same ordinals, so counts collected in C resolve back to
the correct instructions in Python. The Python resolver
(`menai_trace.resolve_trace`) walks the tree in the same order and reconstructs
per-instruction and per-function records, which are rendered using the
disassembler's own instruction formatting and annotations.

For the ordinals to mean anything, each logical `CodeObject` must correspond to
exactly one native code object. The conversion therefore carries a
`ConversionContext` that maps a Python `CodeObject` (by identity) to the native
code object built for it, and a function constant reuses the native code object
already built for its bytecode rather than converting it a second time. Without
this, a function reached through a constant would be a *second* native instance
stamped from a fresh counter, and every count would collapse onto ordinal 0.

The trace is gated by the same enabled flag as the opcode histogram, so it adds
no new branch to the dispatch loop when instrumentation is off.

## Alternatives considered

### Key by native code object pointer

Use the `MenaiCodeObject *` as the key.

Rejected because native code objects are rebuilt on every `execute()` call and
destroyed afterwards. The pointers are unstable across calls and invalid after
the call returns, so a persistent trace buffer keyed by them would be
meaningless and unsafe.

### Key by source location

Key each count by `(source_file, source_line)`.

Rejected as the storage key because it is lossy: it merges every instruction on
a line and cannot distinguish two functions defined on the same line. Source
location is better treated as a *rendering* concern layered on top of exact
counts than as the key itself.

### Record a full ordered trace of every executed instruction

Log each instruction as it executes.

Rejected because the log is unbounded in program run time and needs a ring
buffer or a cap to be safe. Per-instruction counts and call counts are bounded
by program size and call count respectively, and are what hotspot analysis
actually needs. An ordered trace remains a possible separate feature.

### Emit counts via a callback into Python

Call back into Python on each instruction.

Rejected: the GIL is released during execution, and a per-instruction callback
would dominate the cost of execution.

## Consequences

### Positive

- Hot spots are attributable to a specific instruction and function, and the
  output lines up with the disassembler so counts can be read against the
  disassembly.
- The trace is bounded (by instruction count and code object count), so it needs
  no ring buffer or truncation policy.
- Ordinals are stable across repeated executions of the same compiled program,
  so a single trace buffer serves repeated runs.
- No new per-instruction cost when instrumentation is disabled.

### Negative

- The Python walk and the C ordinal assignment are two implementations of the
  same ordering and must agree exactly. If they drift, counts are silently
  attributed to the wrong instructions. The agreement is covered by tests and
  recorded as an architectural invariant in AGENTS.md.
- The trace buffer is sized to one converted tree at a time, so a single VM
  instance traces one program at a time. This matches the existing opcode
  profiler's behaviour.
