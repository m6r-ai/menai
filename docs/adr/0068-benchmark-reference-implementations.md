# ADR-0068: Benchmark suites may time a non-Menai reference implementation

Date: 2026-11-23  
Status: Accepted

## Context

The benchmark tool answers "how fast is this Menai program", and its numbers are
only meaningful relative to something. Two questions get asked of them:

1. Is a slow suite slow because of the language, or because of the algorithm?
2. Where does Menai lose to another language, and by how much?

Both need a second measurement in the same units, taken on the same inputs, at
the same time. Collecting that by hand — a scratch script, a remembered figure —
does not survive: the numbers drift from the code they describe, and a comparison
of two different algorithms masquerades as a comparison of two languages. The
first time this was done by hand, in fact, the hand-written reference solver was
buggy and its figures were invalid; nothing caught it, because nothing was
checking that the two solvers did the same work.

The benchmark tool therefore needs a first-class notion of "the same job, done
another way", with the equivalence it relies on stated and enforced where it can
be.

## Decision

A suite may declare a non-Menai reference implementation of the same work as its
Menai program. The reference is timed on the same cases, reported in adjacent
columns, and summarised by a `ref/menai` ratio.

The reference must perform the same work as the Menai program: the same algorithm,
the same search, and, where the work can be counted, the same count. The suite must
assert that, because a ratio between two searches of different sizes is
meaningless. The check belongs in the reference's untimed preparation step, so it
costs nothing in the measured section and fails the run rather than the report.

Within that constraint the reference must be the fastest implementation we know how
to write in the host language, not a transcription of Menai's data structures. The
requirement has a direction and it is not neutral: a reference that is slower than
it needs to be understates the host language and flatters Menai, so such a ratio
makes a claim about the wrong thing.

Which representation is faster is measured, never assumed. An in-place variant of
the Rubik's cube solver was written because mutation is what a Python programmer
normally reaches for, and it measured 1.7x *slower* than the immutable version,
because one tuple construction of nine loads beats walking a cycle with
bytecode-level index writes; it was discarded. The same reasoning in the other
direction kept an in-place sudoku solver that measured 2x faster than the immutable
one. A suite records the variant it chose and the measurement that chose it, so the
decision is not re-litigated from intuition and the faster version is not quietly
replaced by a tidier one.

The consequence is that the ratio compares *implementations*, not virtual machines.
It measures Menai as the module happens to be written against Python as well as we
can write it, so a gap can have either of two causes: the VM is slower, or the
module is written in a shape that forfeits something the VM can do. The ratio alone
does not say which, and it must not be reported as a pure measure of the VM.

Language-level purity does not settle this. Menai has no mutation a program can
express, but the VM still updates a collection in place whenever the update cannot
be observed — when the destination is the same register the collection was read
from, its reference count is one, and it owns its storage (ADR-0063, with
ADR-0064 and ADR-0065 extending the same rule to bytes and strings). So "Menai
cannot adopt a faster mutable representation" is false, and the useful question is
narrower: does this algorithm's data shape let the VM apply that rule? A cube held
as a struct of six vectors does not, because each move constructs fresh faces rather
than replacing an element of a loop-carried collection; a cube held as one flat
54-element vector, updated with `vector-with` as a loop-carried accumulator, would.
Whether a recursive search can reach the same path is a question for the update
opcodes' stated conditions, not something to assume either way. Where a Python
reference wins by mutating, the first thing to check is whether the Menai module is
written in a shape that forfeits the in-place path, and the answer belongs in the
suite rather than in an assumption about the language.

A reference must not delegate the work to a compiled library behind the host
language's builtin. That measures the compiled library rather than the host
language, and a suite that wants it is asking a different question from the one
this decision answers.

The Menai column is the VM's internal execution time; a reference is timed with a
host clock around the call. The two intervals are not identically scoped — the
reference figure includes host-language call overhead and the Menai figure
excludes the bridge into the VM — so a difference of a percent or so is not
evidence of anything.

The two implementations must also do their one-off setup the same number of times.
A host-language module's tables are built at import, outside the timed section; a
Menai module has no import-time state, so its equivalent is rebuilt on every
execution and lands inside the timed section. A suite whose workload has such
setup must measure it and say so, rather than leave the ratio to absorb it.

## Alternatives considered

**Leave comparisons out of the tool and keep them as ad-hoc scripts.** Rejected:
this is what produced the invalid reference figures described above. A scratch
comparison has no assertion that the two implementations do the same work, and no
place to record what the numbers mean.

**Time the reference with the same VM instrumentation as the Menai
implementation.** Not possible: the VM's counters describe bytecode it executed,
and a reference executes none. A common clock is available for both, but only by
timing the Menai implementation from outside too, which would discard the
existing per-iteration VM timing the rest of the tool is built on and make every
suite's figures incomparable with those recorded previously.

**Make reference implementations a per-suite script with its own output format.**
Rejected: two output formats mean the numbers are read side by side only by eye,
and the ratio would be computed differently, or not at all, in each one. Sharing
the report also means the reference is timed on exactly the cases and iteration
counts the suite already defines, rather than a second definition that can drift.

**Require the reference to mirror the Menai representation step for step.**
Rejected: this was the original rule, and it makes the ratio a cleaner measurement
of the VM while making it a worse answer to the question being asked. A reference
written in Menai's own data structures is answering "what would Menai's program cost
in Python", which no one needs to know. A reference written the way the host
language's practitioners write it is answering "what would a Python user pay for
this job", and that is the comparison that decides whether a suite is worth moving.
The pin on the work, not on the representation, is what keeps the ratio honest.

**Require a reference for every suite.** Rejected: most suites have no honest
counterpart in another language, and inventing one for the sake of symmetry would
put numbers in the report that cannot be interpreted. The default is no reference.

## Consequences

### Positive

- The report shows, in one table, how a Menai implementation compares with another
  language on the same inputs, with no extra invocation and no second definition of
  the cases.
- "Menai costs X relative to the best Python we can write for this job" is a claim
  the tree can substantiate and re-check, because the equivalence of the work is
  asserted rather than asserted-in-prose.
- Requiring the faster representation means the reported ratio cannot be dismissed
  as a consequence of a lazily written reference, which is the failure mode that
  makes cross-language benchmarks easy to argue with and hard to use.

### Negative

- A suite gains a dependency on another language's runtime. That version is part of
  the measurement and is not pinned by this project.
- A reference is a second implementation that must be kept honest. The node-count
  assertion catches a divergence in search size, but not one that preserves the node
  count while changing the work done per node; that gap is what the structural
  checks exist to narrow.
- The ratio is a comparison between implementations, not between virtual machines,
  and it will drift whenever either side is improved. A better Menai module and a
  better Python module both change it, and the direction of the change is not
  apparent from the number alone.
- The ratio column depends on `perf_counter` around the reference while the Menai
  column does not, so the comparison is systematically very slightly pessimistic
  about the reference. This is documented but not corrected.
