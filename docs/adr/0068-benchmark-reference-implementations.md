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

The reference must reimplement the Menai algorithm step for step, so that the
ratio isolates the language's cost for identical work. Where the work can be
counted, the suite must assert that the counts agree, because a ratio between
two searches of different sizes is meaningless. The check belongs in the
reference's untimed preparation step, so it costs nothing in the measured section
and fails the run rather than the report.

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

**Require a reference for every suite.** Rejected: most suites have no honest
counterpart in another language, and inventing one for the sake of symmetry would
put numbers in the report that cannot be interpreted. The default is no reference.

## Consequences

### Positive

- The report shows, in one table, how a Menai implementation compares with another
  language on the same inputs, with no extra invocation and no second definition of
  the cases.
- The same-work kind makes "the language costs X relative to Y" a claim the tree can
  substantiate and re-check, because the equivalence is asserted rather than
  asserted-in-prose.
- The two kinds are distinguishable in the code that declares them, so a ratio
  cannot silently be read as a language comparison when it is really an algorithm
  comparison.

### Negative

- A suite gains a dependency on another language's runtime and, for a same-task
  reference, on a host library's behaviour. Those versions are part of the
  measurement and are not pinned by this project.
- A same-work reference is a second implementation that must be kept honest. The
  node-count assertion catches a divergence in search size, but not one that
  preserves the node count while changing the work done per node.
- The ratio column depends on `perf_counter` around the reference while the Menai
  column does not, so the comparison is systematically very slightly pessimistic
  about the reference. This is documented but not corrected.
