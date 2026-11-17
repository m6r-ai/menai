# Testing modules

Menai test suites are ordinary Menai modules. A suite declares a tree of named
tests, and a test tool runs each leaf in isolation and reports the outcomes.
The file format is the contract; the tool that consumes it is supplied by the
host.

## Test files

A test suite is a `.menai` file named `<subject>.test.menai`, conventionally
stored beside the `<subject>.menai` module it tests. The name before
`.test.menai` is the module name, so `foo.test.menai` is imported as
`foo.test`.

A test file may import the module under test and the `menai-test` support
module, and it must end by exporting a binding named `tests`:

```menai
(let ((foo (import "foo"))
      (t (import "menai-test"))
      (inc (:: foo inc))
      (assert-equal (:: t assert-equal))
      (expect-pass (:: t expect-pass))
      (expect-fail (:: t expect-fail))

      (test-increments
       (lambda () (assert-equal (inc 1) 2)))

      (test-rejects-negative
       (lambda () (inc -1)))

      (tests
       (list
         (list "arithmetic"
               (list
                 (expect-pass "increments" test-increments)
                 (expect-fail "rejects negative" test-rejects-negative "negative"))))))
  (export tests))
```

`menai-test` is not part of the standard library and is not on the default
module path. The test tool makes it importable for the duration of a run.

## Node structure

The exported `tests` value is a **node-list**: a list of nodes, where each node
is a two-element list `(name thing)` and `name` is a string.

| Form | `thing` is | Meaning |
|------|------------|---------|
| Leaf | A zero-argument function | The thunk passes if it returns normally and fails if it raises |
| Branch | Another node-list | A named group; nesting is arbitrary |
| Dict leaf | A dict | A leaf with an explicit expectation |

Branch names appear in the report path separated by ` > `.

A dict leaf has these keys:

| Key | Required | Description |
|-----|----------|-------------|
| `thunk` | yes | Zero-argument function |
| `expect-error` | no | `#t` for a negative test; absent or `#f` for a positive test |
| `expect-error-contains` | no | Substring that must appear in the raised error's message |

## Helpers

`(import "menai-test")` provides the helpers that build leaves:

- `(assert-equal expected actual)` raises unless the two values are
  structurally equal. It compares every Menai type, including lists, vectors,
  dicts, sets, structs and enums.
- `(expect-pass name thunk)` builds a positive leaf.
- `(expect-fail name thunk [contains])` builds a negative leaf. It passes only
  if the thunk raises. Supplying `contains` requires that substring to appear
  in the error message, so an unrelated error does not satisfy the test.

## Isolation

Each leaf runs on a fresh VM invocation, so one failure does not stop the run.
This is what lets `assert-equal` signal failure with `(error ...)`: the error
ends that leaf only, and the tool observes it at the host level.

## Relation to the language

The format is not a language feature. It is a convention over ordinary modules,
data construction and functions, and it needs no new syntax. A host that
supports testing discovers `*.test.menai` files, puts the support module on the
module path, and runs the leaves.

## Library API

The `menai` package exposes the runner so a host tool can embed it without
depending on the `menai-test` console script:

```python
from menai import run_test_suite

outcomes = run_test_suite("math_utils.test.menai", module_path)
```

`run_test_suite(test_file, module_path=None)` loads the suite, executes each
leaf on a fresh VM invocation, and returns one `TestOutcome` per leaf, in
node-list order. Each outcome has:

| Field | Description |
|-------|-------------|
| `path` | Full leaf path, including branch names |
| `passed` | `True` when the leaf behaved as expected |
| `error` | Why the leaf failed, or `None` |

`module_path` is the same search path a `Menai` instance uses. Its directories
are searched ahead of the suite's own directory and the standard library, and
the support module is prepended automatically. A malformed suite raises
`ValueError`; a module that fails to compile raises `MenaiError`.

The runner has no dependency on the `menai-test` console tool or its coverage
support, so it is safe to embed anywhere the `menai` package is available.

The repository's own runner is documented in
[`src/menai_test/README.md`](../src/menai_test/README.md).
