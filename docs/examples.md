# Examples

This section contains complete Menai programs with explanation.

## Hello, world

```menai
(string-concat "Hello, " "World")
→ "Hello, World"
```

## Factorial

A recursive factorial function using `letrec`:

```menai
(letrec ((factorial (lambda (n)
                      (if (integer<=? n 1)
                          1
                          (integer* n (factorial (integer- n 1)))))))
  (factorial 10))
→ 3628800
```

For large inputs, use tail recursion to avoid consuming stack space (though Menai
optimises tail calls, this keeps the pattern clear):

```menai
(letrec ((fact-iter (lambda (n acc)
                      (if (integer<=? n 1)
                          acc
                          (fact-iter (integer- n 1) (integer* acc n))))))
  (fact-iter 20 1))
→ 2432902008176640000
```

## Fibonacci

```menai
(letrec ((fib (lambda (n)
                (if (integer<=? n 1)
                    n
                    (integer+ (fib (integer- n 1)) (fib (integer- n 2)))))))
  (fib 10))
→ 55
```

## Summing a list with fold

```menai
(fold-list integer+ 0 (range 1 11))
→ 55
```

## Filtering and mapping

Given a list of integers, keep the positive ones and double them:

```menai
(let ((nums (list -3 1 -1 4 -2 5 0)))
  (map-list (lambda (x) (integer* x 2))
            (filter-list (lambda (x) (integer>? x 0)) nums)))
→ (2 8 10)
```

## Building a lookup table

```menai
(let ((ages (dict "Alice" 30 "Bob" 25 "Carol" 35)))
  (let ((names (list "Alice" "Bob" "Carol")))
    (map-list (lambda (name)
                (string-concat name ": " (integer->string (dict-get ages name))))
              names)))
→ ("Alice: 30" "Bob: 25" "Carol: 35")
```

## Pattern matching on data

A function that classifies a value:

```menai
(let ((classify (lambda (x)
                  (match x
                    (0 "zero")
                    ((? integer? n) (if (integer>? n 0) "positive" "negative"))
                    ((? string? s) (string-concat "string: " s))
                    ((? list? l) (string-concat "list of " (integer->string (list-length l))))
                    (_ "unknown")))))
  (list (classify 0) (classify 42) (classify -7) (classify "hi") (classify (list 1 2 3))))
→ ("zero" "positive" "negative" "string: hi" "list of 3")
```

## Working with sets

```menai
(let ((evens (set 2 4 6 8 10))
      (primes (set 2 3 5 7)))
  (list
    (set->list (set-intersection evens primes))
    (set->list (set-union evens primes))
    (set->list (set-difference evens primes))))
→ ((2) (2 3 4 5 6 7 8 10) (4 6 8 10))
```

## Structs: a simple bank account

```menai
(letrec ((account (struct (balance))))
  (let ((make-account (lambda (initial) (account initial)))
        (deposit (lambda (acc amount)
                   (struct-set acc 'balance
                     (integer+ (struct-get acc 'balance) amount))))
        (withdraw (lambda (acc amount)
                   (let ((bal (struct-get acc 'balance)))
                     (if (integer<? bal amount)
                         (error "insufficient funds")
                         (struct-set acc 'balance (integer- bal amount)))))))
    (let ((acc (make-account 100)))
      (let ((acc (deposit acc 50)))
        (let ((acc (withdraw acc 30)))
          (struct-get acc 'balance))))))
→ 120
```

## JSON parser

The `json_parser.menai` module in `menai_modules/` is a complete JSON parser written
in Menai. It converts JSON strings to Menai values:

| JSON | Menai |
|------|-------|
| Object | dict |
| Array | list |
| String | string |
| Integer | integer |
| Float | float |
| `true` | `#t` |
| `false` | `#f` |
| `null` | `#none` |

Using it:

```menai
(let ((json (import "json_parser")))
  (let ((parse (dict-get json "parse")))
    (parse "{\"name\": \"Alice\", \"scores\": [95, 87, 92]}")))
→ {("name" "Alice") ("scores" (95 87 92))}
```

The parser uses an explicit work stack to avoid call-stack overflow on deeply nested
JSON. Each stack frame describes what to do when a sub-value is returned. This is a
good example of how to handle iterative parsing in a pure functional language.

The parser is also a good example of module structure: it uses `letrec` to define
mutually recursive helper functions, exports a single `parse` function in a dict,
and keeps all helpers private.

## Writing a test module

Test files use the `*_test.menai` convention and are run by the `menai-test` tool.
Here is the structure of a test module:

```menai
(let ((t (import "menai_test")))
  (let ((assert-equal (dict-get t "assert-equal")))
    (dict "tests" (list
      (list "addition" (lambda () (assert-equal (integer+ 1 2) 3)))
      (list "concatenation" (lambda () (assert-equal (string-concat "a" "b") "ab")))))))
```

Each test is a `(name function)` pair. The function is called with no arguments;
`assert-equal` raises an error if the expected and actual values differ.

Run tests with:

```bash
menai-test
```