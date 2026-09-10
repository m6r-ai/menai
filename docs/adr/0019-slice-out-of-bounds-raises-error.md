# ADR-0019: Slice operations raise errors on out-of-bounds indices

Date: 2026-09-10  
Status: Accepted

## Context

Menai has four slice operations — `string-slice`, `list-slice`, `vector-slice`,
and `bytes-slice` — that extract a sub-sequence from a sequence value given a
start index and an optional end index. An out-of-bounds index in caller code
likely indicates a logic error, and the correct response is to surface it
rather than silently return a shorter-than-expected sub-sequence.

## Decision

All four slice operations raise a runtime error when any of the following
conditions hold:

- `start` is negative — `NEGATIVE_SLICE_INDEX`
- `end` is negative — `NEGATIVE_SLICE_INDEX`
- `start` is greater than the sequence length — `SLICE_START_OUT_OF_RANGE`
- `end` is greater than the sequence length — `SLICE_END_OUT_OF_RANGE`
- `start` is greater than `end` — `SLICE_START_AFTER_END`

These checks are performed in the order listed above.

## Alternatives considered

### Silent clamping

Clamp out-of-bounds indices to the valid range instead of raising an error.
Rejected because it silently masks bugs in caller code — a program that passes
an out-of-bounds index likely has a logic error.

## Consequences

### Positive

- All four slice operations have identical bounds-checking semantics, making
  the API predictable and consistent.
- Out-of-bounds indices in caller code are surfaced as errors rather than
  silently producing unexpected results.

### Negative

- Code that passes out-of-bounds indices to a slice operation will receive a
  runtime error. Such code must use valid indices explicitly.
