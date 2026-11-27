# Handoff: use-after-free in the C VM's code-object conversion (segmentation fault)

A working note for a future session.  Delete it once the fault is fixed.

## Symptom

A Python driver that compiles and executes several Menai programs **in one process**
dies with SIGSEGV during a compile-to-execute transition.  It is not a Menai-level
error: there is no `MenaiEvalError`, the interpreter dies mid-call into the extension.

The fault is a **use-after-free**: `menai_code_object_final` is called *while
converting* a Python `CodeObject` tree into native code objects, and something it
frees is still referenced.

## Reliable reproduction

```bash
source venv/bin/activate
python3 -X faulthandler gitsafe/idct_check.py
```

Crashed on every attempt — three runs, three faults, always after the first case has
completed (`dc-only  max error 0  ok`).  Exit status 139.

### Captured C stack (the valuable part)

```text
Fatal Python error: Segmentation fault

Current thread ... (most recent call first):
  File ".../src/menai/vm/menai_vm.py", line 36 in execute
  File ".../src/menai/menai.py", line 93 in execute_raw
  File ".../gitsafe/idct_check.py", line 81 in run
  File ".../gitsafe/idct_check.py", line 104 in <module>

Current thread's C stack trace (most recent call first):
  ... _Py_DumpStack, faulthandler_dump_c_stack, faulthandler_fatal_error, _sigtramp
  menai_vm_c.cpython-314-darwin.so  at menai_code_object_final+0x60
  menai_vm_c.cpython-314-darwin.so  at menai_code_object_from_python_rec+0x880
  menai_vm_c.cpython-314-darwin.so  at menai_vm_c_execute+0xa8
  ... cfunction_call, _PyObject_MakeTpCall, _PyEval_EvalFrameDefault, PyEval_EvalCode,
      run_mod, _PyRun_File, _PyRun_SimpleFile, _PyRun_AnyFile, pymain_run_file,
      pymain_run_file_obj, Py_RunMain, pymain_main, Py_BytesMain, start
```

Read it bottom-up: `execute()` enters `menai_vm_c_execute`, which calls
`menai_code_object_from_python_rec` to walk the Python code-object tree, and *that*
calls `menai_code_object_final` — a destructor — before dereferencing whatever it
freed.  So the conversion path is either double-freeing or freeing a code object that
is still live (in a registry, in the previous run's state, or in the just-built tree).

This is exactly the sort of lifetime rule `src/menai/vm/AGENTS.md` and
[ADR-0028](../../docs/adr/0028-instruction-tracing-by-ordinal.md) describe: native
code objects are **ephemeral** across `execute()` calls and ordinals, not pointers,
are the only stable key.  A stale pointer surviving a conversion is the failure mode
to look for.

## What did *not* reproduce it

`gitsafe/vm_crash_repro.py` tries the plausible ingredients separately, 8 rounds each
on one `Menai` instance: standard-library module vs. a scratch module, bytes payload
vs. a 64-element list-of-integers payload, and a scratch module rewritten between
compiles.  **None of variants 1–7 faulted.**  Three further focused cases also passed:

| case | result |
|------|--------|
| `decode-probe` + constant payload, result via `.describe()` | clean |
| `decode-probe` + constant payload, result via `.to_python()` | clean |
| `decode-probe` + payload varying per round, `.to_python()` | clean |

Two consequences worth knowing before you start:

1. **Instance reuse is not required.** `idct_check.py` now builds a *fresh* `Menai`
   per case and still crashes, so the state that goes stale is process-wide (a static
   or interpreter-level registry), not per-instance.
2. **The crash needs a specific program.** Six bisection variants and three focused
   cases run the same shape without faulting, so the difference lies in the generated
   module or the program itself.  `gitsafe/jpegdiff/decode-probe.menai` is regenerated
   by the driver from `src/menai/stdlib/jpeg-decode.menai` before the loop; it exports
   one function, `inverse-transform`, and the driver's expression is

   ```text
   (let ((p (import "decode-probe")))
     ((:: p inverse-transform) (list->vector (dict-get inputs "c"))))
   ```

   The most likely remaining discriminators, in order to try next: the size of the
   generated module (it is ~4 KB of nested `let` bindings with unrolled 8-term inner
   products — far larger than any repro module, so recursion depth in
   `menai_code_object_from_python_rec` may matter), the number of nested code objects,
   and the `list->vector` call in the expression.

## Suggested next steps

1. `make build-magic` then re-run — the use-after-free magic build may turn this into
   a clean abort naming the poisoned object.  `make build-leaks` and `make build-gc`
   are the other switches (see `Makefile`, `src/menai/vm/AGENTS.md`).
2. `lldb -- python3 gitsafe/idct_check.py`, then `bt all` at the fault.  The
   faulthandler stack stops at the top three frames; lldb gives the locals in
   `menai_code_object_from_python_rec` (which code object is being finalised, and
   whether its refcount or its parent pointer is the bad one).
3. Read `menai_code_object_from_python_rec` and `menai_code_object_final` in
   `src/menai/vm/menai_vm_c.c` with the question: *when a conversion fails or is
   retried, is the partially built tree freed and then reused?* Look for a `goto`
   error path that frees and then continues to use, and for any cache keyed by the
   Python code object's identity that survives across `execute()` calls.
4. Only then consider an ASAN build (`setup.py build_ext` with `-fsanitize=address`);
   the magic build and lldb should be enough.

## What "fixed" looks like

- A Python regression test under `tests/src/menai/` that compiles and executes at
  least three distinct programs in one process, at least one importing a generated
  module and injecting a list payload.  Small is fine: it pins the lifetime rule, not
  throughput.  It must fail on the current build.
- The invariant written where it belongs — `src/menai/vm/AGENTS.md` — if it is one
  that a future reader could not infer from the code (e.g. "a native code object must
  never outlive the `execute()` that produced it, and the conversion must not cache
  pointers across calls").
- A `CHANGELOG.md` line under bug fixes in the released version's section.

## Then resume the decoder work (untouched)

The decoder butterfly has not been started, and the design is settled:

- The inverse transform is **not** the encoder's network with renamed outputs; it is
  the **transpose** of it.  The encoder's butterfly is `A = diag(m)·B` with
  `m(u) = 4·aanscale(u)` and `aanscale(u) = C(u)·cos(u·π/16)` (`C(0) = 1/√2`, else 1),
  verified numerically to 12 digits against the exact DCT.
- So the inverse needs `B' = A'·diag(1/m)`, which the transposed network computes.  The
  per-coefficient factor `aanscale(u)·aanscale(v)/8` must be folded into the
  **dequantisation** step — multiply each DQT entry by it when the segment is parsed.
  That is O(64) per table, not per block, so per-block cost is unchanged.
- The column pass must write its outputs down a **column** (`v`, `8 + v`, … `56 + v`).
  Writing them across a row silently transposes the result and destroys horizontal
  detail; that trap cost the most time on the encoder.
- Validating in this order:
  1. `python3 gitsafe/idct_check.py` — per-block agreement with the exact IDCT, every
     coefficient pattern within one level.  **Currently blocked by this crash**, which
     is why it comes first.
  2. `python3 -W ignore gitsafe/jpeg_check.py` — whole-image agreement with libjpeg;
     the decoder is at max 0/1/2 levels per fixture today, so worse is a regression.
  3. `menai-test src/menai/stdlib/jpeg-decode.test.menai src/menai/stdlib/jpeg-round-trip.test.menai`
  4. `menai-benchmark --suite jpeg-decode --opcodes --iterations 1 --no-color`, summed
     `TOTAL` rows, against 12,739,857 for `colour-444-128x128`.

## Current verified state

- decode 12,739,857 instructions (−29% on the original 18,102,803)
- encode 9,125,330 instructions (−59% on the original 22,369,968)
- 67/67 JPEG tests pass, `tools.code_checker` clean, both modules `menai-format` clean
- last commit: `3486e2a Improve JPEG performance`

## Scratch files

`gitsafe/` is gitignored; nothing here is committed.

| file | purpose |
|------|---------|
| `gitsafe/idct_check.py` | **reproducer** and, later, the per-block IDCT gate |
| `gitsafe/vm_crash_repro.py` | seven bisection variants; none fault — documents what is *not* the trigger |
| `gitsafe/jpeg_check.py` | whole-image libjpeg gate for both directions |
| `gitsafe/splice_butterfly.py` | splices the encoder butterfly in, asserting paren balance |
| `gitsafe/jpegdiff/` | probe modules, logs, `decode-probe.menai`, `pin_*.py` focused cases |
| `gitsafe/jpegdiff/idct-run.log` | captured faulting run with the C stack above |

## Addendum: the forward network, measured (supersedes guesswork above)

Probing the encoder's `butterfly` on unit impulses (`gitsafe/jpegdiff/fwd-measure.log`)
gives the ground truth the decoder work needs, so nobody has to derive it from
memory or from a literature network again:

```text
m = [2.828427, 3.923141, 3.695518, 3.325878, 2.828427, 2.222281, 1.530734, 0.780361]
JtJ = 8 * I      (diagonal [8, 15.391, 13.657, 11.061, 8, 4.939, 2.343, 0.609],
                  largest off-diagonal 3e-10)
```

So the network is `A = diag(m)·B` with `B` the orthonormal DCT basis, and
`m(u) = 4·aanscale(u)`, `aanscale(u) = C(u)·cos(u·π/16)`, `C(0) = 1/√2`, otherwise 1.
Note `m(0) = 2√2`, **not** 4 — the DC term is the one that breaks the pattern, and
getting it wrong yields coefficients 16× too large, which overruns the Huffman
categories and corrupts the whole scan.

The inverse network must equal `Aᵀ·diag(1/m)`, with `1/m(u)·1/m(v)` folded into
dequantisation at DQT parse time.

### Test the transpose correctly

Do **not** assert `transpose(forward(x)) == 8x`.  That identity holds for
`Aᵀ = diag(1/m)·Bᵀ`, not for the raw network transpose, and using it as the gate
sends you chasing a phantom bug — it cost most of one session.  The right checks are:

- `adjoint(y)[k] == Σ_u A[k][u]·y[u]` for random `y`, with `A` from the measured matrix
- `diag(1/m)·Aᵀ·diag(m)` composed with the forward being `8·I`

### Where the automation stands

`gitsafe/derive_inverse.py` parses the encoder's `butterfly` straight out of the
module, flattens its nested `let`s, and applies reverse-mode adjoint rules to emit the
transposed network as Menai code.  Parsing, flattening and emission all work; the
emitted expressions **do not yet verify** (adjoint mismatch 5.1e+02), so the
expression-orientation handling has a bug.  That is an hour of debugging against the
measured matrix, and it is the right way in: no hand-derivation, and no reliance on my
recollection of libjpeg.

### Do not use the Python per-block harness yet

`gitsafe/idct_check.py` triggers the segfault above, which is why this document
exists.  Until the VM fault is fixed, validate the transform **inside one Menai
program** — a `.menai` file that computes the transform and compares it against
expected values, run with `menai-eval` — or through whole-image agreement via
`gitsafe/jpeg_check.py`, which does not fault.
