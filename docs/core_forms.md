# Core forms

Core forms are the fundamental building blocks of Menai programs. Unlike function
calls, which evaluate all arguments and pass them to the function, core forms have
special evaluation rules — some arguments are evaluated lazily, some are not
evaluated at all.

## let — local bindings

`let` is Menai's only binding form. Every binding name is in scope for every
binding value and for the body, so a binding may reference any sibling — including
itself and the other bindings in the form.

```menai
(let ((x 5) (y 10))
  (integer+ x y))
→ 15
```

Because all names are in scope throughout, a binding can reference a sibling
written before it *or* after it. Source order carries no meaning:

```menai
(let ((x 5) (y (integer* x 2)))
  (integer+ x y))
→ 15
```

```menai
(let ((y (integer* x 2)) (x 5))
  (integer+ x y))
→ 15
```

This covers the case other Lisps spell `let*`, but it is not the same thing. A
`let*` evaluates its bindings in source order; Menai evaluates them in dependency
order, computed from which bindings reference which. A chain of dependent
bindings is therefore written as one flat `let`, and the desugarer reintroduces
whatever nesting the dependencies require. Any binding that does not actually
participate in a cycle is hoisted to a parallel group internally, so writing a
sequential chain costs nothing at runtime.

### Recursion

Self-recursion:

```menai
(let ((fact (lambda (n)
              (if (integer<=? n 1)
                  1
                  (integer* n (fact (integer- n 1)))))))
  (fact 5))
→ 120
```

Mutual recursion:

```menai
(let ((even? (lambda (n) (if (integer=? n 0) #t (odd? (integer- n 1)))))
      (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
  (even? 10))
→ #t
```

### Struct definitions in let

Struct type definitions can appear alongside function definitions. Use this when
defining a module that exports a struct type together with its associated
functions:

```menai
(let ((point (struct (x y)))
      (make-point (lambda (x y) (point x y)))
      (distance (lambda (p1 p2)
                  (let ((dx (integer- (struct-get p1 'x) (struct-get p2 'x)))
                        (dy (integer- (struct-get p1 'y) (struct-get p2 'y))))
                    (integer+ (integer* dx dx) (integer* dy dy))))))
  (dict "point" point "make-point" make-point "distance" distance))
```

### Enum definitions in let

Enum type definitions can appear alongside function definitions for the same
reason:

```menai
(let ((state (enum (idle running stopped)))
      (describe (lambda (s)
                  (match s
                    ((: state 'idle) "idle")
                    ((: state 'running) "running")
                    ((: state 'stopped) "stopped")))))
  (dict "state" state "describe" describe))
```

### Syntax

```menai
(let ((name1 expr1) (name2 expr2) ...) body)
```

The body is a single expression.

Binding names may not be reserved words — see
[Syntax — Reserved words](syntax.md#reserved-words).

### Binding names must be unique

A repeated name is ambiguous rather than a shadow, because the form defines each
name once:

```menai
(let ((x 1) (x (integer+ x 10))) x)   ; ERROR: duplicate binding name
```

To shadow, nest a `let`. A nested `let` may shadow an enclosing binding, but the
inner binding's own value cannot reference the outer one of the same name — the
inner name wins, so the reference becomes a self-reference. Bind the outer value
under a different name to pass it down:

```menai
(let ((outer 1))
  (let ((inner (integer+ outer 10)))
    inner))
→ 11
```

### Self-reference

A non-lambda binding may not depend on another non-lambda binding in the same
group, since neither has a value when the other is evaluated. This covers both a
direct self-reference and a cycle between two computed bindings:

```menai
(let ((x (integer+ x 1))) x)   ; ERROR: cannot refer to itself
(let ((a (integer* b 2)) (b (integer* a 3))) a)   ; ERROR: no base case
(let ((x (list (lambda () x)))) x)   ; OK: reference is deferred
```

Reading a *lambda* sibling is always fine, because the closure exists before any
non-lambda value in the group is computed. A reference shielded by a lambda is
also fine, because the lambda body runs later. That is the boundary between a
recursive group, which is meaningful, and a cyclic one, which is not.

This restriction is what makes the dependency ordering well-defined: a binding
that depends on itself has no topological position. See
[ADR-0054](adr/0054-let-is-the-only-binding-form.md).

## No sequencing form

Menai has no `begin` or sequencing form. `let` and `lambda` accept exactly one
body expression. Because Menai is pure, there are no side effects to sequence —
if an intermediate result is unused, it is dead code and should be removed. Use
`let` for multi-step computations where each step depends on the previous one.

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
(let ((loop (lambda (i acc)
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
can be any Menai value. When the argument is a string, it becomes the error
message directly. When it is any other value, its string representation becomes
the error message, and the structured value is also attached to the resulting
exception as `error_value` for callers that need richer context.

```menai
(error "something went wrong")
```

Structured error values are useful for providing machine-readable context:

```menai
(error (dict "type" "arity-error"
             "function" "integer+"
             "expected" 2
             "received" n))
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
(error value-expr)
```

## import — load a module

`import` loads a module as a namespace. It is a compile-time operation — the module
is compiled once and cached. It is only valid as the value of a `let` binding;
access an export with member access. See [Modules](modules.md) for full details.

```menai
(let ((math (import "math_utils")))
  ((:: math square) 5))
→ 25
```

### Syntax

```menai
(import "module-name")
```

## :: — namespace member access

`::` accesses a member of a namespace bound by an `import`. The first argument
must be a namespace in scope and the second an unquoted member name. The member
resolves at compile time to the declaration that produced it. A namespace is
second-class: it may appear only as the first argument of `::`, never as a call
head or an ordinary value. See [Modules](modules.md) for full details.

```menai
(let ((math (import "math_utils")))
  ((:: math square) 5))
→ 25
```

### Syntax

```menai
(:: namespace member)
```
