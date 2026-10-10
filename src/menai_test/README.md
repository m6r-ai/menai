# Menai Test Runner

A test runner for Menai modules. Test files use the `.test.menai` suffix and
export a structured tree of named test thunks. The runner discovers, executes,
and reports them with full isolation between tests.

## Running tests

```bash
# Run all tests under a directory (recursive)
menai-test src/menai/stdlib/

# Run a single test file
menai-test src/menai/stdlib/json-decode.test.menai

# Show passing tests as well as failures
menai-test src/menai/stdlib/ --verbose

# Filter by name (case-insensitive substring match on full path)
menai-test src/menai/stdlib/ --filter "parse-string"
```

The runner exits with code 0 if all tests pass, non-zero if any fail.

## Writing tests

A test file is a `.menai` module that imports `menai-test` and exports a dict
with a `"tests"` key containing a node-list:

```menai
(let ((mymod (import "my_module"))
      (t (import "menai-test")))
  (let ((my-fn (dict-get mymod "my-fn"))
        (assert-equal (dict-get t "assert-equal"))
        (expect-pass (dict-get t "expect-pass"))
        (expect-fail (dict-get t "expect-fail")))
    (dict
      (list "tests" (list

        (list "group name" (list
          (expect-pass "test name" (lambda () (assert-equal (my-fn 1) 2)))
          (expect-pass "another" (lambda () (assert-equal (my-fn 0) 0)))
          (expect-fail "rejects 0" (lambda () (my-fn 0)) "must be positive")
        ))

      )))))
```

### Node structure

The `"tests"` value is a **node-list** — a list of nodes, where each node is a
two-element list `(name thing)`:

- **Leaf**: `thing` is a zero-argument lambda (the test thunk)
- **Dict leaf**: `thing` is a dict carrying a thunk and an explicit expectation
- **Branch**: `thing` is another node-list (a named group)

Nesting is arbitrary. Branch names appear in the output path separated by ` > `.

### Writing tests: `expect-pass` and `expect-fail`

Positive and negative tests are written with the `expect-pass` and `expect-fail`
helpers.  Both take a name and a thunk, so the two kinds of test look the same:

```menai
(list (expect-pass "parses header" (lambda () (assert-equal ...)))
      (expect-fail "rejects bad input" (lambda () (parse bad-input)) "missing signature"))
```

`expect-pass` builds a leaf whose thunk should return normally; use
`assert-equal` inside it to check the produced value.

`expect-fail` builds a leaf whose thunk should raise.  Its optional third
argument is a **substring that must appear in the raised error's message** —
this ensures the *right* error fired rather than an unrelated one.  Omit it to
accept any error, though supplying it is strongly encouraged:

```menai
(expect-fail "rejects bad input" (lambda () (parse bad-input)))                       ; any error
(expect-fail "rejects bad input" (lambda () (parse bad-input)) "missing signature")  ; specific error
```

For a negative test the pass/fail outcome is inverted: raising an error is a
pass, returning normally is a failure.

Menai has no in-language error handling, so this expectation is expressed in the
node structure and enforced by the runner, which invokes the thunk and inspects
the outcome at the Python level.

### Dict leaf keys

`expect-pass` and `expect-fail` are thin constructors over the underlying dict
leaf form, which you can also write directly:

| Key                     | Required | Description                                                  |
|-------------------------|----------|--------------------------------------------------------------|
| `thunk`                 | yes      | Zero-argument function                                       |
| `expect-error`          | no       | `#t` for a negative test; absent or `#f` for a positive test |
| `expect-error-contains` | no       | Substring that must appear in the raised error's message     |

A dict leaf with `expect-error` absent (or `#f`) behaves exactly like a plain
lambda leaf.  A bare lambda remains accepted as shorthand for a positive leaf.

### Isolation

Each leaf thunk runs in a **fresh VM invocation**. A runtime error (type error,
assertion failure, etc.) in one test does not affect any other. This is the
mechanism that makes `assert-equal` safe to use — the VM terminates on the
first failure and the runner catches it at the Python level.

### assert-equal

`assert-equal` compares two values for structural equality across all Menai
types (boolean, integer, float, complex, string, symbol, none, list, vector,
dict, set, struct instance, enum value, structtype, enumtype). On mismatch it
raises with a message showing the expected and actual values:

```text
assert-equal failed
  expected: 42
  actual:   43
```

On a runtime error (type error, etc.) the error message from the VM is shown
directly — no special handling is needed.

## Test support module

The runner prepends its own directory to the Menai module path, making
`(import "menai-test")` available to all test files. This module is **only**
available when running under the test runner — it is not part of the standard
module library.

Exports:

| Name            | Description                                              |
|-----------------|----------------------------------------------------------|
| `assert-equal`  | Raises if two values are not structurally equal          |
| `expect-pass`   | Builds a positive test node `(name thunk)`               |
| `expect-fail`   | Builds a negative test node `(name thunk [contains])`    |
| `test-find`     | Internal — used by the runner to locate leaf thunks      |

## Output format

```
src/menai/stdlib/json-decode.test.menai
  ✓  objects > empty object
  ✓  objects > single key
  ✗  strings > escapes > unicode
       Error: assert-equal failed
         expected: "A"
         actual:   "a"
  3/4 FAILED

============================================================
Total: 3/4 passed, 1 FAILED
```
