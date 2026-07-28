# Pattern matching

`match` is Menai's primary tool for conditional dispatch and data destructuring. It
evaluates an expression and tries to match the result against a series of patterns.
The first matching pattern's body is evaluated and returned.

## Basic structure

```menai
(match expression
  (pattern1 result1)
  (pattern2 result2)
  (_ default-result))
```

Patterns are tried in order — first match wins. The `_` wildcard matches anything,
so it is typically used as the final catch-all pattern.

## Literal patterns

A literal value as a pattern matches only that exact value:

```menai
(match 42
  (0 "zero")
  (42 "the answer")
  (_ "other"))
→ "the answer"
```

Literals of any type work as patterns:

```menai
(match "hello"
  ("hi" "greeting")
  ("hello" "hello to you")
  (_ "unknown"))
→ "hello to you"
```

## Wildcard pattern

`_` matches any value without binding a name:

```menai
(match x
  (42 "found it")
  (_ "something else"))
```

## Variable binding patterns

A predicate pattern `(? pred var)` tests a value with a predicate and, if it
matches, binds the value to a variable:

```menai
(match 7
  ((? integer? n) (integer* n 2))
  ((? string? s) (string-upcase s))
  (_ "unknown"))
→ 14
```

The predicate can be any expression that evaluates to a function, including
user-defined predicates:

```menai
(let ((positive? (lambda (x) (integer>? x 0))))
  (match -5
    ((? positive? n) "positive")
    (_ "not positive")))
→ "not positive"
```

A predicate pattern without a variable `(? pred)` tests the predicate but does not
bind a name.

## Empty list pattern

`()` matches an empty list:

```menai
(match (list)
  (() "empty")
  (_ "non-empty"))
→ "empty"
```

## List destructuring patterns

A list pattern `(p1 p2 ...)` matches a list of the same length, binding each element
to the corresponding sub-pattern:

```menai
(match (list 1 2 3)
  ((a b c) (integer+ a b c))
  (_ "not three elements"))
→ 6
```

A single-element list pattern:

```menai
(match (list 42)
  (() "empty")
  ((x) (string-concat "singleton: " (integer->string x)))
  (_ "multiple"))
→ "singleton: 42"
```

### Dotted patterns (cons-pattern)

A dotted pattern `(head . tail)` matches a list, binding `head` to the first element
and `tail` to the rest of the list:

```menai
(match (list 1 2 3)
  ((head . tail) (list head (list-length tail)))
  (_ "empty"))
→ (1 2)
```

This is the only context in Menai where a dotted (cons-style) pattern appears.
There are no cons cells in Menai's data model — this is purely a pattern matching
convenience.

## Nested patterns

Patterns can be nested to any depth:

```menai
(match (list 42 "hello")
  (((? integer? x) (? string? y)) (list x y))
  (_ "no match"))
→ (42 "hello")
```

## Struct destructuring patterns

A struct pattern `(TypeName field1 field2 ...)` matches a struct instance of the
given type and binds each field by position:

```menai
(let ((point (struct (x y))))
  (let ((p (point 3 4)))
    (match p
      ((point x y) (integer+ x y))
      (_ 0))))
→ 7
```

The type name in the pattern must be a structtype value that is in scope. The
compiler resolves field bindings at compile time.

There is also a predicate form for matching a specific struct type without
destructuring:

```menai
(match p
  ((? (lambda (x) (struct-is-instance? x point)) p) (struct-get p 'x))
  (_ 0))
```

## First match wins

Patterns are tested top to bottom. Put more specific patterns before more general
ones:

```menai
(match x
  (42 "exactly 42")
  ((? integer? n) (string-concat "integer: " (integer->string n)))
  (_ "other"))
```

If `x` is `42`, the first pattern matches. If `x` is any other integer, the second
pattern matches. Otherwise, the wildcard catches everything else.

## Complete example

```menai
(match data
  (42 "the answer")
  ((? integer? n) (integer* n 2))
  ((? string? s) (string-upcase s))
  ((head . tail) (list head (list-length tail)))
  ((? dict? d) (dict-length d))
  (_ "unknown"))
```
