# Core forms

Core forms are the fundamental building blocks of Menai programs. Unlike function
calls, which evaluate all arguments and pass them to the function, core forms have
special evaluation rules — some arguments are evaluated lazily, some are not
evaluated at all.

## let — parallel bindings

`let` creates local bindings where all binding expressions are evaluated in the
enclosing scope, then bound to their names simultaneously. Bindings cannot reference
each other.

```menai
(let ((x 5) (y 10))
  (integer+ x y))
→ 15
```

The binding expressions `5` and `10` are evaluated before `x` or `y` are bound. This
means `y` cannot reference `x`:

```menai
(let ((x 5) (y (integer* x 2))) ...)   ; ERROR: x is not in scope yet
```

Use `let*` for sequential bindings where later bindings can reference earlier ones.

### Syntax

```menai
(let ((name1 expr1) (name2 expr2) ...) body)
```

The body is a single expression. Use `let` for independent bindings.

## let\* — sequential bindings

`let*` creates local bindings where each binding expression is evaluated in the scope
of all previous bindings. Later bindings can reference earlier ones.

```menai
(let* ((x 5) (y (integer* x 2)))
  (integer+ x y))
→ 15
```

Shadowing works — a later binding can reuse a name from an earlier one:

```menai
(let* ((x 1) (x (integer+ x 10)))
  x)
→ 11
```

### Syntax

```menai
(let* ((name1 expr1) (name2 expr2) ...) body)
```

Use `let*` when a binding depends on a previous binding.

## letrec — recursive bindings

`letrec` creates bindings where all names are in scope for all binding expressions.
This enables self-recursion and mutual recursion.

```menai
(letrec ((fact (lambda (n)
                 (if (integer<=? n 1)
                     1
                     (integer* n (fact (integer- n 1)))))))
  (fact 5))
→ 120
```

Mutual recursion:

```menai
(letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
         (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
  (even? 10))
→ #t
```

### Struct definitions in letrec

Struct type definitions can appear in `letrec` alongside function definitions. They
are hoisted to `let` automatically. Use this when defining a module that exports a
struct type alongside its associated functions:

```menai
(letrec ((point (struct (x y)))
         (make-point (lambda (x y) (point x y)))
         (distance (lambda (p1 p2)
                     (let ((dx (integer- (struct-get p1 'x) (struct-get p2 'x)))
                           (dy (integer- (struct-get p1 'y) (struct-get p2 'y))))
                       (integer+ (integer* dx dx) (integer* dy dy)))))
  (dict "point" point "make-point" make-point "distance" distance))
```

### Syntax

```menai
(letrec ((name1 expr1) (name2 expr2) ...) body)
```

Use `letrec` when you need functions that reference themselves or each other.

## No sequencing form

Menai has no `begin` or sequencing form. All binding forms (`let`, `let*`,
`letrec`) and `lambda` accept exactly one body expression. Because Menai is
pure, there are no side effects to sequence — if an intermediate result is
unused, it is dead code and should be removed. Use `let*` for multi-step
computations where each step depends on the previous one.

## lambda — anonymous functions

`lambda` creates an anonymous function (a closure). Functions are first-class values
with lexical scoping.

```menai
((lambda (x) (integer* x x)) 5)
→ 25
```

### Fixed-arity parameters

```menai
(lambda (a b) (integer+ a b))
```

Takes exactly 2 arguments.

### Variadic parameters

A dot `.` in the parameter list collects remaining arguments into a list:

```menai
(lambda (a b . rest) ...)
```

`a` and `b` bind to the first two arguments; `rest` binds to a list of any remaining
arguments (possibly empty).

With nothing before the dot, all arguments are collected:

```menai
(lambda (. args) (fold-list integer+ 0 args))
```

`args` receives all arguments as a list.

```menai
((lambda (. args) (fold-list integer+ 0 args)) 1 2 3 4 5)
→ 15
```

With a single fixed parameter and a rest parameter:

```menai
((lambda (x . rest) (list-prepend (list-reverse rest) x)) 1 2 3)
→ (1 3 2)
```

### Closures

Functions capture their enclosing scope:

```menai
(let ((n 10))
  (let ((add-n (lambda (x) (integer+ x n))))
    (add-n 5)))
→ 15
```

### Tail call optimisation

Recursive functions in tail position do not consume stack space. This means
recursion can be used as a loop without risk of stack overflow:

```menai
(letrec ((loop (lambda (i acc)
                 (if (integer>=? i 1000000)
                     acc
                     (loop (integer+ i 1) (integer+ acc i))))))
  (loop 0 0))
→ 499999500000
```

## if — conditional

`if` evaluates a condition and returns one of two branches. The condition must be a
boolean — there is no "truthiness".

```menai
(if (integer>? 5 3) "yes" "no")
→ "yes"
```

Only the selected branch is evaluated (lazy evaluation):

```menai
(if #t 42 (error "never reached"))
→ 42
```

The condition must evaluate to `#t` or `#f`. Using a non-boolean value is a type
error:

```menai
(if 1 "yes" "no")     ; ERROR: 1 is not a boolean
(if #none "yes" "no") ; ERROR: #none is not a boolean
```

### Syntax

```menai
(if condition then-branch else-branch)
```

The `else-branch` is required — `if` always has two branches.

## and / or — short-circuit boolean

`and` and `or` are special forms (not functions) that short-circuit. They evaluate
arguments left to right and stop as soon as the result is determined.

```menai
(and #t #t #f)   → #f   ; stops at #f
(or #f #f #t)    → #t   ; stops at #t
(and #t #t)      → #t
(or #f #f)       → #f
```

`and` returns `#f` as soon as it encounters a `#f` argument. If all arguments are
`#t`, it returns `#t`.

`or` returns `#t` as soon as it encounters a `#t` argument. If all arguments are
`#f`, it returns `#f`.

All arguments must be booleans.

## match — pattern matching

`match` evaluates an expression and tries to match it against a series of patterns.
The first matching pattern's body is evaluated and returned. See
[Pattern matching](pattern_matching.md) for full details.

```menai
(match x
  (42 "the answer")
  ((? integer? n) (integer* n 2))
  ((? string? s) (string-upcase s))
  (_ "unknown"))
```

## quote — data literals

`quote` returns an expression as data without evaluating it. The `'` prefix is
shorthand for `(quote ...)`.

```menai
'foo              → foo              ; symbol
'(integer+ 1 2)   → (integer+ 1 2)   ; list (not evaluated)
'(a b c)          → (a b c)          ; list of symbols
```

`quote` enables homoiconicity — code and data have the same representation:

```menai
(list-first '(integer+ 1 2))
→ integer+   ; the symbol integer+, not the function
```

### Syntax

```menai
(quote expr)
'expr          ; shorthand
```

## error — raise a runtime error

`error` raises a runtime error immediately. It never returns a value. The argument
must be a string expression.

```menai
(error "something went wrong")
```

`error` can appear in any expression position:

```menai
(if (integer<? n 0)
    (error "n must be non-negative")
    (float-sqrt (integer->float n)))
```

The message can be a computed string:

```menai
(error (string-concat "invalid value: " (integer->string x)))
```

### Syntax

```menai
(error string-expr)
```

## import — load a module

`import` loads and returns a module. It is a compile-time operation — the module is
compiled once and cached. See [Modules](modules.md) for full details.

```menai
(let ((math (import "math_utils")))
  ((dict-get math "square") 5))
→ 25
```

### Syntax

```menai
(import "module-name")
```