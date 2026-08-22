# Builtins

This section documents all builtin functions in Menai, organised by category.

## Naming conventions

Menai follows a consistent naming convention for collection operations:

- **Direct operations** are named `collection-X` with the collection as the first
  argument: `(list-ref lst 0)`, `(dict-get d "key")`, `(set-member? s x)`
- **Higher-order operations** are named `X-collection` with the function/predicate
  first and the collection last: `(map-list f lst)`, `(filter-dict pred d)`,
  `(fold-set f init s)`

The same convention applies to bytes (`bytes-X` / `X-bytes`).

---

## None

| Function | Description |
|----------|-------------|
| `(none? x)` | `→ #t` if x is `#none`, `→ #f` otherwise |

```menai
(none? #none)  → #t
(none? #f)     → #f
(none? 0)      → #f
```

---

## Boolean

| Function | Description |
|----------|-------------|
| `(boolean? x)` | Type predicate |
| `(boolean=? a b)` | Equality |
| `(boolean!=? a b)` | Inequality |
| `(boolean-not x)` | Logical negation (the only boolean negation function) |

```menai
(boolean? #t)        → #t
(boolean=? #t #t)    → #t
(boolean!=? #t #f)   → #t
(boolean-not #t)     → #f
```

---

## Integer

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(integer? x)` | Type predicate |
| `(integer=? a b)` | Equality |
| `(integer!=? a b)` | Inequality |
| `(integer<? a b)` | Less than |
| `(integer>? a b)` | Greater than |
| `(integer<=? a b)` | Less than or equal |
| `(integer>=? a b)` | Greater than or equal |

### Arithmetic

| Function | Description |
|----------|-------------|
| `(integer+ a b ...)` | Addition; `(integer+)` → 0 |
| `(integer- a b)` | Subtraction (requires 2+ args) |
| `(integer* a b ...)` | Multiplication; `(integer*)` → 1 |
| `(integer/ a b)` | Floor division: `(integer/ 7 3)` → 2, `(integer/ -7 2)` → -4 |
| `(integer-neg a)` | Unary negation: `(integer-neg 5)` → -5 |
| `(integer% a b)` | Modulo; result takes sign of divisor: `(integer% -7 2)` → 1, `(integer% 7 -3)` → -2 |
| `(integer-abs a)` | Absolute value |
| `(integer-expn base exp)` | Exponentiation; exp must be non-negative; arbitrary precision |
| `(integer-min a b)` | Minimum |
| `(integer-max a b)` | Maximum |

```menai
(integer+ 1 2 3)    → 6
(integer+)          → 0
(integer* 2 3 4)    → 24
(integer*)          → 1
(integer/ 7 3)      → 2
(integer/ -7 2)     → -4
(integer% 7 3)      → 1
(integer% -7 2)     → 1
(integer% 7 -3)     → -2
(integer-expn 2 10) → 1024
(integer-expn 3 0)  → 1
(integer-expn 0 0)  → 1
```

### Bitwise

| Function | Description |
|----------|-------------|
| `(integer-bit-or a b)` | Bitwise OR |
| `(integer-bit-and a b)` | Bitwise AND |
| `(integer-bit-xor a b)` | Bitwise XOR |
| `(integer-bit-not a)` | Bitwise NOT |
| `(integer-bit-shift-left a n)` | Left shift |
| `(integer-bit-shift-right a n)` | Right shift |

### Conversions

| Function | Description |
|----------|-------------|
| `(integer->float x)` | Integer to float |
| `(integer->complex real [imag])` | Integer(s) to complex; `(integer->complex 3)` → 3+0j, `(integer->complex 1 -2)` → 1-2j |
| `(integer->string x [radix])` | Integer to string; radix: 2, 8, 10, or 16 (default 10) |
| `(integer-codepoint->string n)` | Unicode codepoint to single-character string |

```menai
(integer->float 42)          → 42.0
(integer->string 255 16)     → "ff"
(integer->string 255 2)      → "11111111"
(integer-codepoint->string 65)    → "A"
(integer-codepoint->string 960)   → "π"
(integer-codepoint->string 128512) → "😀"
```

`integer-codepoint->string` raises an error for surrogates (0xD800–0xDFFF) or values
outside 0–0x10FFFF.

---

## Float

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(float? x)` | Type predicate |
| `(float=? a b)` | Equality |
| `(float!=? a b)` | Inequality |
| `(float<? a b)` | Less than |
| `(float>? a b)` | Greater than |
| `(float<=? a b)` | Less than or equal |
| `(float>=? a b)` | Greater than or equal |

### Arithmetic

| Function | Description |
|----------|-------------|
| `(float+ a b ...)` | Addition; `(float+)` → 0.0 |
| `(float- a b)` | Subtraction (requires 2+ args) |
| `(float* a b ...)` | Multiplication; `(float*)` → 1.0 |
| `(float/ a b)` | Division |
| `(float// a b)` | Floor division: `(float// 7.0 2.0)` → 3.0 |
| `(float-neg a)` | Unary negation |
| `(float% a b)` | Modulo |
| `(float-abs a)` | Absolute value |
| `(float-expn base exp)` | Exponentiation: `(float-expn 2.0 10.0)` → 1024.0 |
| `(float-min a b)` | Minimum |
| `(float-max a b)` | Maximum |
| `(float-copysign a b)` | Magnitude of `a` with the sign of `b` |
| `(float-hypot a b)` | `sqrt(a²+b²)` without intermediate overflow/underflow |

### Rounding

| Function | Description |
|----------|-------------|
| `(float-floor x)` | Floor → float |
| `(float-ceil x)` | Ceiling → float |
| `(float-round x)` | Round → float |
| `(float-trunc x)` | Truncate toward zero → float |

All rounding functions return `float`, not `integer`. Use
`(float->integer (float-round x))` to get an integer.

### Transcendental and logarithmic

| Function | Description |
|----------|-------------|
| `(float-exp x)` | e^x |
| `(float-exp2 x)` | 2^x |
| `(float-expm1 x)` | e^x − 1 (numerically stable for small x) |
| `(float-log x)` | Natural log; negative arg is error; 0.0 → -inf |
| `(float-log2 x)` | Log base 2 (correctly rounded) |
| `(float-log10 x)` | Log base 10 |
| `(float-logn x base)` | Log base n; base must be positive and ≠ 1 |
| `(float-log1p x)` | log(1+x), numerically stable for small x; x < -1 is error; -1.0 → -inf |
| `(float-sqrt x)` | Square root; negative arg is error (use `complex-sqrt`) |
| `(float-cbrt x)` | Cube root (works for negative x) |
| `(float-sin x)` | Sine |
| `(float-cos x)` | Cosine |
| `(float-tan x)` | Tangent |
| `(float-asin x)` | Inverse sine; arg must be in [-1, 1] |
| `(float-acos x)` | Inverse cosine; arg must be in [-1, 1] |
| `(float-atan x)` | Inverse tangent |
| `(float-atan2 y x)` | Four-quadrant inverse tangent of y/x |
| `(float-sinh x)` | Hyperbolic sine |
| `(float-cosh x)` | Hyperbolic cosine |
| `(float-tanh x)` | Hyperbolic tangent |

### Conversions

| Function | Description |
|----------|-------------|
| `(float->integer x)` | Float to integer (truncates toward zero): `(float->integer 3.7)` → 3, `(float->integer -2.9)` → -2 |
| `(float->complex real [imag])` | Float(s) to complex |
| `(float->string x)` | Float to string |

The constants `pi` and `e` are available as float values (not function calls):

```menai
pi  → 3.141592653589793
e   → 2.718281828459045
```

---

## Complex

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(complex? x)` | Type predicate |
| `(complex=? a b)` | Equality |
| `(complex!=? a b)` | Inequality |

Complex numbers have no ordering. Use `complex-abs` to compare magnitudes as floats.

### Arithmetic

| Function | Description |
|----------|-------------|
| `(complex+ a b ...)` | Addition; `(complex+)` → 0+0j |
| `(complex- a b)` | Subtraction |
| `(complex* a b ...)` | Multiplication; `(complex*)` → 1+0j |
| `(complex/ a b)` | Division |
| `(complex-neg a)` | Unary negation |
| `(complex-abs z)` | Magnitude as float: `(complex-abs 3+4j)` → 5.0 |
| `(complex-real z)` | Real part as float |
| `(complex-imag z)` | Imaginary part as float |
| `(complex-exp z)` | e^z |
| `(complex-expn base exp)` | base^exp |
| `(complex-log z)` | Natural log |
| `(complex-log10 z)` | Log base 10 |
| `(complex-logn z base)` | Log base n (both args complex) |
| `(complex-sin z)` | Sine |
| `(complex-cos z)` | Cosine |
| `(complex-tan z)` | Tangent |
| `(complex-sqrt z)` | Square root |

### Conversion

| Function | Description |
|----------|-------------|
| `(complex->string z)` | Complex to string: `(complex->string 3+4j)` → "3+4j" |

---

## String

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(string? x)` | Type predicate |
| `(string=? a b)` | Equality |
| `(string!=? a b)` | Inequality |
| `(string<? a b)` | Less than (Unicode codepoint order) |
| `(string>? a b)` | Greater than |
| `(string<=? a b)` | Less than or equal |
| `(string>=? a b)` | Greater than or equal |

### Basic operations

| Function | Description |
|----------|-------------|
| `(string-concat a b ...)` | Concatenate strings |
| `(string-length s)` | Length (character count) |
| `(string-ref s i)` | Character at 0-based index → single-character string |
| `(string-slice s start [end])` | Substring; end is exclusive; `(string-slice "hello" 2)` → "llo" |
| `(string-upcase s)` | Uppercase |
| `(string-downcase s)` | Lowercase |
| `(string-trim s)` | Remove leading and trailing whitespace |
| `(string-trim-left s)` | Remove leading whitespace |
| `(string-trim-right s)` | Remove trailing whitespace |
| `(string-replace s old new)` | Replace all occurrences of old with new |

### Search

| Function | Description |
|----------|-------------|
| `(string-prefix? s prefix)` | `→ #t` if s starts with prefix |
| `(string-suffix? s suffix)` | `→ #t` if s ends with suffix |
| `(string-index s sub)` | 0-based index of first occurrence, or `#none` |

```menai
(string-index "hello" "l")  → 2
(string-index "hello" "z")  → #none
```

### Conversions

| Function | Description |
|----------|-------------|
| `(string->number s)` | Parse to number (integer, float, or complex); `→ #none` if unparseable |
| `(string->integer s [radix])` | Parse to integer; radix: 2, 8, 10, 16 (default 10); `→ #none` if unparseable |
| `(string->list s [delimiter])` | Split into list of strings |
| `(integer-codepoint->string n)` | (See integer section) |
| `(string->integer-codepoint s)` | Single-character string to Unicode codepoint integer |

```menai
(string->number "42")      → 42
(string->number "3.14")    → 3.14
(string->number "1+2j")    → 1+2j
(string->number "hello")   → #none

(string->integer "ff" 16)  → 255
(string->integer "FF" 16)  → 255   ; case-insensitive
(string->integer "42")     → 42

(string->list "hello")         → ("h" "e" "l" "l" "o")
(string->list "a,b,c" ",")    → ("a" "b" "c")
(string->list "a,,b" ",")     → ("a" "" "b")   ; consecutive delimiters → empty strings
(string->list "one::two" "::") → ("one" "two") ; multi-character delimiter

(string->integer-codepoint "A")    → 65
(string->integer-codepoint "π")    → 960
(string->integer-codepoint "😀")   → 128512
```

`string->integer-codepoint` raises an error if the string is not exactly one character.

---

## List

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(list? x)` | Type predicate |
| `(list=? a b)` | Equality |
| `(list!=? a b)` | Inequality |

### Construction

| Function | Description |
|----------|-------------|
| `(list a b ...)` | Create a list |
| `(list-prepend lst item)` | Prepend item to front of list |
| `(list-append lst item)` | Append item to end of list |
| `(list-concat a b ...)` | Concatenate lists; `(list-concat)` → () |

```menai
(list-prepend (list 2 3) 1)  → (1 2 3)
(list-append (list 1 2) 3)   → (1 2 3)
(list-concat (list 1 2) (list 3 4))  → (1 2 3 4)
```

### Access

| Function | Description |
|----------|-------------|
| `(list-first lst)` | First element |
| `(list-rest lst)` | All elements after the first |
| `(list-last lst)` | Last element |
| `(list-ref lst i)` | Element at 0-based index |

### Properties

| Function | Description |
|----------|-------------|
| `(list-length lst)` | Number of elements |
| `(list-null? lst)` | `→ #t` if empty |
| `(list-member? lst item)` | `→ #t` if item is in list |

### Utilities

| Function | Description |
|----------|-------------|
| `(list-reverse lst)` | Reverse the list |
| `(list-remove lst item)` | Remove all occurrences of item |
| `(list-index lst item)` | 0-based index of first occurrence, or `#none` |
| `(list-slice lst start [end])` | Sublist; end is exclusive |
| `(list->string lst [separator])` | Concatenate list of strings |
| `(list->set lst)` | Convert to set (deduplicates, retains first occurrence order) |

```menai
(list-slice (list 1 2 3 4 5) 2)     → (3 4 5)
(list-slice (list 1 2 3 4 5) 1 3)   → (2 3)
(list->string (list "h" "e" "l" "l" "o"))  → "hello"
(list->string (list "a" "b" "c") ",")      → "a,b,c"
```

### Range

| Function | Description |
|----------|-------------|
| `(range start end [step])` | Generate a range of integers; step defaults to 1 |

```menai
(range 1 5)    → (1 2 3 4)
(range 0 10 2) → (0 2 4 6 8)
```

### Higher-order

| Function | Description |
|----------|-------------|
| `(map-list f lst)` | Apply f to each element |
| `(filter-list pred lst)` | Keep elements where pred returns `#t` |
| `(fold-list f init lst)` | Left fold; f is `(lambda (acc item) result)` |
| `(find-list pred lst)` | First element satisfying pred, or `#none` |
| `(any-list? pred lst)` | `→ #t` if any element satisfies pred; `(any-list? pred ())` → #f |
| `(all-list? pred lst)` | `→ #t` if all elements satisfy pred; `(all-list? pred ())` → #t |
| `(sort-list comparator lst)` | Stable sort; comparator is `(lambda (a b) ...)` returning `#t` if a should come before b |
| `(list-zip lst1 lst2)` | Pair corresponding elements; stops at shorter list |

```menai
(map-list (lambda (x) (integer* x 2)) (list 1 2 3))
→ (2 4 6)

(filter-list (lambda (x) (integer>? x 0)) (list -1 2 -3 4))
→ (2 4)

(fold-list integer+ 0 (list 1 2 3 4))
→ 10

(fold-list (lambda (acc item) (list-append acc item)) (list) (list 1 2 3))
→ (1 2 3)

(find-list (lambda (x) (integer>? x 3)) (list 1 2 3 4 5))
→ 4

(any-list? (lambda (x) (integer>? x 3)) (list 1 2 3 4 5))
→ #t

(all-list? (lambda (x) (integer>? x 0)) (list 1 2 3 4 5))
→ #t

(sort-list integer<? (list 3 1 4 1 5))
→ (1 1 3 4 5)

(list-zip (list 1 2 3) (list 4 5 6))
→ ((1 4) (2 5) (3 6))

(list-zip (list 1 2 3) (list 4 5))
→ ((1 4) (2 5))
```

---

## Dict

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(dict? x)` | Type predicate |
| `(dict=? a b)` | Equality |
| `(dict!=? a b)` | Inequality |

### Construction and access

| Function | Description |
|----------|-------------|
| `(dict key1 val1 key2 val2 ...)` | Create a dict |
| `(dict-get d key [default])` | Get value; `→ #none` if missing and no default |
| `(dict-set d key val)` | Return new dict with key set to val |
| `(dict-remove d key)` | Return new dict with key removed |

```menai
(dict-get (dict "a" 1 "b" 2) "a")         → 1
(dict-get (dict "a" 1) "missing")         → #none
(dict-get (dict "a" 1) "missing" "none")  → "none"
```

### Queries

| Function | Description |
|----------|-------------|
| `(dict-has? d key)` | `→ #t` if key exists |
| `(dict-keys d)` | List of keys (insertion order) |
| `(dict-values d)` | List of values (insertion order) |
| `(dict-length d)` | Number of entries |

### Merging

| Function | Description |
|----------|-------------|
| `(dict-merge d1 d2)` | Merge dicts; d2 wins on key conflicts |

### Higher-order

| Function | Description |
|----------|-------------|
| `(map-dict f d)` | Apply f to each (key, value) pair; f is `(lambda (k v) result)`; returns new dict with transformed values |
| `(filter-dict pred d)` | Keep entries where pred returns `#t`; pred is `(lambda (k v) result)` |

```menai
(map-dict (lambda (k v) (integer* v 2)) (dict "a" 1 "b" 2))
→ {("a" 2) ("b" 4)}

(filter-dict (lambda (k v) (integer>? v 1)) (dict "a" 1 "b" 2))
→ {("b" 2)}
```

---

## Set

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(set? x)` | Type predicate |
| `(set=? a b)` | Equality (order-insensitive) |
| `(set!=? a b)` | Inequality |

### Construction and membership

| Function | Description |
|----------|-------------|
| `(set a b ...)` | Create a set; duplicates silently dropped |
| `(set-member? s x)` | `→ #t` if x is in s |
| `(set-length s)` | Number of elements |
| `(set-add s x)` | Return new set with x added (no-op if present) |
| `(set-remove s x)` | Return new set with x removed (no-op if absent) |

### Set algebra

| Function | Description |
|----------|-------------|
| `(set-union s1 s2)` | Union |
| `(set-intersection s1 s2)` | Intersection |
| `(set-difference s1 s2)` | s1 minus s2 |
| `(set-subset? s1 s2)` | `→ #t` if every element of s1 is in s2 |

### Conversion

| Function | Description |
|----------|-------------|
| `(set->list s)` | List of elements (insertion order) |
| `(list->set lst)` | Set from list (deduplicates) |

### Higher-order

| Function | Description |
|----------|-------------|
| `(map-set f s)` | Apply f to each element; return new set |
| `(filter-set pred s)` | New set of elements satisfying pred |
| `(fold-set f init s)` | Left fold; f is `(lambda (acc item) result)` |
| `(any-set? pred s)` | `→ #t` if any element satisfies pred; `(any-set? pred (set))` → #f |
| `(all-set? pred s)` | `→ #t` if all elements satisfy pred; `(all-set? pred (set))` → #t |

Sets have no ordering and no positional access. Use `(set->list s)` then list
operations for ordered iteration.

---

## Bytes

### Predicates and comparison

| Function | Description |
|----------|-------------|
| `(bytes? x)` | Type predicate |
| `(bytes=? a b ...)` | Equality (variadic, 2+ args) |
| `(bytes!=? a b ...)` | Inequality (variadic, 2+ args) |
| `(bytes<? a b ...)` | Lexicographic less than (variadic) |
| `(bytes>? a b ...)` | Lexicographic greater than (variadic) |
| `(bytes<=? a b ...)` | Lexicographic less than or equal (variadic) |
| `(bytes>=? a b ...)` | Lexicographic greater than or equal (variadic) |

### Construction and conversion

| Function | Description |
|----------|-------------|
| `(string-hex->bytes hex-string)` | Hex string to bytes |
| `(string->bytes s)` | UTF-8 string to bytes |
| `(list->bytes lst)` | List of integers (0–255) to bytes |
| `(bytes->string-hex b)` | Bytes to hex string |
| `(bytes->string b)` | Bytes to UTF-8 string (error on invalid UTF-8) |
| `(bytes->list b)` | Bytes to list of integers |

### Access

| Function | Description |
|----------|-------------|
| `(bytes-ref b i)` | Byte value (0–255) at 0-based index |
| `(bytes-length b)` | Number of bytes |
| `(bytes-slice b start [end])` | Sub-sequence; end is exclusive; clamps out-of-bounds |
| `(bytes-concat b1 b2 ...)` | Concatenate; `(bytes-concat)` → empty bytes |
| `(bytes-append-u8 b n)` | Append single byte (n must be 0–255) |

### Search

| Function | Description |
|----------|-------------|
| `(bytes-index haystack needle)` | Offset of first occurrence of needle bytes, or `#none` |
| `(bytes-index-int b byte-val)` | Offset of first byte matching byte-val, or `#none` |

### Predicates

| Function | Description |
|----------|-------------|
| `(bytes-empty? b)` | `→ #t` if length is 0 |
| `(bytes-prefix? b prefix)` | `→ #t` if b starts with prefix |
| `(bytes-suffix? b suffix)` | `→ #t` if b ends with suffix |

### Splitting

| Function | Description |
|----------|-------------|
| `(bytes-split b delimiter)` | Split on delimiter bytes (delimiter must be non-empty) |
| `(bytes-split-int b byte-val)` | Split on single byte value |

### Multi-byte integer reads

All take `(bytes offset)` and return an integer. Raise error if not enough bytes from offset.

| Category | Functions |
|----------|-----------|
| Unsigned | `bytes-read-u8`, `bytes-read-u16-le`, `bytes-read-u16-be`, `bytes-read-u24-le`, `bytes-read-u24-be`, `bytes-read-u32-le`, `bytes-read-u32-be`, `bytes-read-u64-le`, `bytes-read-u64-be` |
| Signed | `bytes-read-i8`, `bytes-read-i16-le`, `bytes-read-i16-be`, `bytes-read-i24-le`, `bytes-read-i24-be`, `bytes-read-i32-le`, `bytes-read-i32-be`, `bytes-read-i64-le`, `bytes-read-i64-be` |

### Multi-byte integer appends

All take `(bytes integer)` and return new bytes. Value must fit in the specified width.

| Category | Functions |
|----------|-----------|
| Unsigned | `bytes-append-u16-le`, `bytes-append-u16-be`, `bytes-append-u24-le`, `bytes-append-u24-be`, `bytes-append-u32-le`, `bytes-append-u32-be`, `bytes-append-u64-le`, `bytes-append-u64-be` |
| Signed | `bytes-append-i8`, `bytes-append-i16-le`, `bytes-append-i16-be`, `bytes-append-i24-le`, `bytes-append-i24-be`, `bytes-append-i32-le`, `bytes-append-i32-be`, `bytes-append-i64-le`, `bytes-append-i64-be` |

### Multi-byte integer writes

All take `(bytes offset integer)` and return new bytes (original unchanged). Value must
fit in the specified width. Raise error if not enough bytes from offset.

| Category | Functions |
|----------|-----------|
| Unsigned | `bytes-write-u8`, `bytes-write-u16-le`, `bytes-write-u16-be`, `bytes-write-u24-le`, `bytes-write-u24-be`, `bytes-write-u32-le`, `bytes-write-u32-be`, `bytes-write-u64-le`, `bytes-write-u64-be` |
| Signed | `bytes-write-i8`, `bytes-write-i16-le`, `bytes-write-i16-be`, `bytes-write-i24-le`, `bytes-write-i24-be`, `bytes-write-i32-le`, `bytes-write-i32-be`, `bytes-write-i64-le`, `bytes-write-i64-be` |

### LEB128 variable-length integers

| Function | Description |
|----------|-------------|
| `(bytes-read-uleb128 b offset)` | `→ (value next-offset)` 2-element list; unsigned; error if truncated |
| `(bytes-append-uleb128 b value)` | New bytes with unsigned LEB128; value must be non-negative |
| `(bytes-read-sleb128 b offset)` | `→ (value next-offset)` 2-element list; signed; error if truncated |
| `(bytes-append-sleb128 b value)` | New bytes with signed LEB128 |

### Higher-order

| Function | Description |
|----------|-------------|
| `(map-bytes f b)` | Apply f to each byte; f receives integer 0–255; returns new bytes |
| `(filter-bytes pred b)` | Keep bytes where pred returns `#t` |
| `(fold-bytes f init b)` | Left fold; f is `(lambda (acc byte) result)` |

---

## Structtype operations

Structtype values are produced by `(struct (field1 field2 ...))` as the RHS of a
`let`, `let*`, or `letrec` binding. The binding name becomes the type name.

```menai
(let ((point (struct (x y))))
  point)                       ; <structtype point (x y)>
```

### Predicates and equality

| Function | Description |
|----------|-------------|
| `(structtype? x)` | `→ #t` if x is a structtype value |
| `(structtype=? a b)` | `→ #t` if a and b are the same struct type (same tag) |
| `(structtype!=? a b)` | Negation |

### Introspection

| Function | Description |
|----------|-------------|
| `(structtype-name type-val)` | Type name as string |
| `(structtype-fields type-val)` | List of field name symbols |

```menai
(let ((point (struct (x y))))
  (structtype? point))            → #t
(let ((point (struct (x y))))
  (structtype? (point 1 2)))      → #f   ; instance, not type
(let ((point (struct (x y))))
  (structtype-name point))        → "point"
(let ((point (struct (x y))))
  (structtype-fields point))      → (x y)
```

Struct-type values are hashable and can be used as set members or dict keys.

---

## Struct operations

Struct instances are created by calling a structtype value with positional field
values:

```menai
(let ((point (struct (x y))))
  (point 1 2))   ; creates a point instance with x=1, y=2
```

### Predicates

| Function | Description |
|----------|-------------|
| `(struct? x)` | `→ #t` for any struct instance |
| `(struct-is-instance? instance type-val)` | `→ #t` if instance is of the given type specifically |

```menai
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (struct? p)))                      → #t
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (struct-is-instance? p point)))    → #t
```

### Field access and update

| Function | Description |
|----------|-------------|
| `(struct-get instance 'field)` | Get field value by symbol name |
| `(struct-ref instance index)` | Get field value by integer index (0-based) |
| `(struct-set instance 'field value)` | Return new struct with field updated (by symbol name) |
| `(struct-set-ref instance index value)` | Return new struct with field updated (by integer index) |

```menai
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (struct-get p 'x)))              → 1
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (struct-ref p 0)))              → 1   ; same field, by index
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (struct-get (struct-set p 'x 10) 'x)))  → 10
```

### Equality

| Function | Description |
|----------|-------------|
| `(struct=? a b)` | `→ #t` if same type tag and all fields equal |
| `(struct!=? a b)` | Negation |

### Introspection

| Function | Description |
|----------|-------------|
| `(struct-type instance)` | Returns the structtype value for a given instance |

```menai
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (structtype? (struct-type p))))   → #t
(let ((point (struct (x y))))
  (let ((p (point 1 2)))
    (structtype-name (struct-type p))))  → "point"
```

### Nominal typing

Two struct types with identical fields are distinct types:

```menai
(let ((point (struct (x y)))
      (vec   (struct (x y))))
  (struct=? (point 1 2) (vec 1 2)))
→ #f
```

### Hashability

Structs are hashable (usable as set members or dict keys) if all their fields are
hashable scalars.

### Pattern matching

See [Pattern matching — struct destructuring](pattern_matching.md#struct-destructuring-patterns).

---

## Symbol

| Function | Description |
|----------|-------------|
| `(symbol? x)` | Type predicate |
| `(symbol=? a b)` | Equality |
| `(symbol!=? a b)` | Inequality |
| `(symbol->string s)` | Symbol name as string |

```menai
(symbol->string 'foo)  → "foo"
(map-list symbol->string '(foo bar baz))  → ("foo" "bar" "baz")
```

Symbols are produced only by `quote`. They are not strings.

---

## Function

| Function | Description |
|----------|-------------|
| `(function? x)` | Type predicate |
| `(function=? f g)` | Identity equality (same function object) |
| `(function!=? f g)` | Different function objects |
| `(function-min-arity f)` | Minimum number of arguments f requires |
| `(function-variadic? f)` | `→ #t` if f accepts more than its minimum (has a rest parameter) |
| `(function-accepts? f n)` | `→ #t` if calling f with exactly n args satisfies arity |
| `(apply f args)` | Call f with elements of list args as individual arguments |

```menai
(function=? integer+ integer+)   → #t
(function=? integer+ integer*)   → #f
(function-min-arity integer-abs) → 1
(function-min-arity integer+)    → 0
(function-variadic? integer+)    → #t
(function-variadic? integer-abs) → #f
(function-accepts? integer-abs 1) → #t
(function-accepts? integer-abs 2) → #f
(apply integer+ (list 1 2 3))    → 6
(apply list (list 1 2 3))        → (1 2 3)
(apply f (list))                 ; calls f with zero arguments
```

`apply` respects all arity rules: fixed-arity functions are checked, variadic args
are packed.
