# Types

Menai has the following types. All values are immutable — no type's value can be
modified in place. Every operation that appears to "modify" a value actually returns
a new value.

## integer

Signed arbitrary-precision integers.

```menai
42          ; decimal
#xff        ; hex (255)
#o755       ; octal (493)
#b1010      ; binary (10)
#d42        ; explicit decimal prefix (42)
-7          ; negative
```

There is no fixed width — integers grow as large as needed:

```menai
(integer-expn 2 100)
→ 1267650600228229401496703205376
```

Type predicate:

```menai
(integer? 42)    → #t
(integer? 3.14)  → #f
```

## float

Double-precision floating-point numbers (IEEE 754 64-bit).

```menai
3.14
1.0e10
-9.28
43.0e9
```

Type predicate:

```menai
(float? 3.14)  → #t
(float? 42)    → #f
```

The constants `pi` and `e` are available as float values:

```menai
pi  → 3.141592653589793
e   → 2.718281828459045
```

## complex

Complex numbers with float real and imaginary parts.

```menai
3+4j      ; real 3, imaginary 4
5j        ; real 0, imaginary 5
1j        ; the imaginary unit
1.5e2j    ; real 0, imaginary 150.0
0j        ; zero complex
```

Complex numbers are constructed with literals or via conversion from integers or
floats:

```menai
(integer->complex 3 4)  → 3+4j
(float->complex 3.0 4.0) → 3+4j
```

Complex numbers have no ordering — you cannot use `<` or `>` on them. Use
`complex-abs` to get the magnitude as a float for comparison.

Type predicate:

```menai
(complex? 3+4j)  → #t
(complex? 42)    → #f
```

## string

Immutable sequences of Unicode characters.

```menai
"hello"
"with\nnewline"
"tab\there"
"quote: \""
"backslash: \\"
"unicode: \u03c0"   ; π
```

String ordering is Unicode codepoint order (same as Python `str`), not locale-aware
collation.

Type predicate:

```menai
(string? "hello")  → #t
(string? 42)       → #f
```

## boolean

Two values: `#t` (true) and `#f` (false).

```menai
#t
#f
```

There is no concept of "truthiness" — only actual boolean values can be used in
conditional positions. `(if 1 ...)` is a type error, not a shortcut for `(if #t ...)`.

Type predicate:

```menai
(boolean? #t)  → #t
(boolean? 1)   → #f
```

## none

Represents the absence of a value. It is distinct from `#f` — `#none` is not a
boolean and cannot be used as a condition.

```menai
#none
```

`#none` is returned by operations that may not find a result:

```menai
(dict-get (dict "a" 1) "missing")     → #none
(find-list (lambda (x) (integer>? x 5)) (list 1 2 3))  → #none
(string->number "not a number")       → #none
```

Type predicate:

```menai
(none? #none)  → #t
(none? #f)     → #f
```

To test for absence, use `none?`:

```menai
(if (none? (dict-get d "key"))
    "missing"
    "present")
```

## symbol

Symbols are values produced by `quote`. They exist to support homoiconicity —
representing code as data. A symbol is a name that has not been evaluated.

```menai
'foo          → foo       ; the symbol foo
'(a b c)      → (a b c)   ; a list of three symbols
```

Symbols are not strings. They have their own type and their own equality:

```menai
(symbol? 'foo)           → #t
(string? 'foo)           → #f
(symbol=? 'foo 'foo)     → #t
(symbol=? 'foo 'bar)     → #f
```

Convert a symbol to its name as a string:

```menai
(symbol->string 'foo)  → "foo"
```

Symbols are hashable and can be used as set members or dict keys.

## list

Immutable proper lists — sequences of values. There is no `cons` operation and
no improper lists in the surface language. Lists can contain mixed types.

```menai
()                  ; empty list
(list 1 2 3)        ; (1 2 3)
(list "a" 42 #t)    ; ("a" 42 #t) — mixed types
```

Type predicate:

```menai
(list? (list 1 2))  → #t
(list? ())          → #t
```

See [Builtins — list operations](builtins.md#list-operations) for the full list of
list functions.

## dict

Immutable key-value mappings with O(1) lookup. Dicts maintain insertion order.

```menai
(dict "name" "Alice" "age" 30)
→ {("name" "Alice") ("age" 30)}
```

The display format with curly braces is for display only — construction always uses
`(dict ...)` with alternating keys and values.

Type predicate:

```menai
(dict? (dict "a" 1))  → #t
```

See [Builtins — dict operations](builtins.md#dict-operations) for the full list of
dict functions.

## set

Immutable unordered collections of unique values with O(1) membership testing.

```menai
(set 1 2 3)
→ #{1 2 3}
```

The display format `#{...}` is for display only — construction always uses
`(set ...)`.

Valid element types (hashable): `string`, `integer`, `float`, `complex`, `boolean`,
`symbol`, `bytes`, `structtype`, `struct` (if all fields are hashable scalars).
Lists, dicts, functions, and `#none` are not hashable and cannot be set members.

Type predicate:

```menai
(set? (set 1 2))  → #t
```

See [Builtins — set operations](builtins.md#set-operations) for the full list of
set functions.

## bytes

Immutable sequences of bytes (octets, values 0–255). There is no literal syntax for
bytes — they are created via construction functions:

```menai
(string-hex->bytes "504b")     → #bytes"504b"
(string->bytes "hello")        → UTF-8 encoded bytes
(list->bytes (list 80 75))     → bytes from integer list
```

The display format `#bytes"hex"` is for display only.

Type predicate:

```menai
(bytes? (string->bytes "x"))  → #t
```

See [Builtins — bytes operations](builtins.md#bytes-operations) for the full list of
bytes functions, including multi-byte integer read/write and LEB128 encoding.

## structtype

A structtype is a type descriptor produced by `(struct (field1 field2 ...))`. It is
not a struct instance — it is the blueprint that describes a struct type. A
structtype value is callable: calling it with field values creates a struct
instance of that type.

```menai
(let ((point (struct (x y))))
  point)                       ; <structtype point (x y)>
  (structtype? point)          ; #t
  (structtype-name point)      ; "point"
  (structtype-fields point)    ; (x y)
```

A structtype value is distinct from a struct instance. `struct?` returns `#f` for a
structtype value and `#t` for an instance:

```menai
(let ((point (struct (x y))))
  (list (struct? point) (struct? (point 1 2))))
→ (#f #t)
```

Struct-type values are hashable and can be used as set members or dict keys.

See [Builtins — structtype operations](builtins.md#structtype-operations) for
introspection functions.

## struct

A struct is an instance of a structtype. Instances are created by calling the
structtype value with positional field values. Structs are nominal — two struct
types with the same fields are distinct types:

```menai
(let ((point (struct (x y)))
      (vec   (struct (x y))))
  (struct=? (point 1 2) (vec 1 2)))   → #f
```

Type predicate:

```menai
(struct? (point 1 2))  → #t
(struct? point)        → #f   ; structtype value, not an instance
```

Structs are hashable (usable as set members or dict keys) if all their fields are
hashable scalars.

See [Builtins — struct operations](builtins.md#struct-operations) for the full list
of struct instance functions.

## function

Functions are first-class values with lexical scoping and closures. They are created
with `lambda` or referenced as builtins.

```menai
(lambda (x) (integer* x x))    ; anonymous function
integer+                       ; builtin function (also a first-class value)
```

Type predicate:

```menai
(function? (lambda (x) x))  → #t
(function? integer+)        → #t
```

See [Core forms — lambda](core_forms.md#lambda) for defining functions, and
[Builtins — function operations](builtins.md#function-operations) for function
introspection.
