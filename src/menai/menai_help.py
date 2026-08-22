"""
Menai language reference for AI agents.

This module is the canonical source of the Menai help text shown to AI agents
via the `help` tool.  It is the single source of truth for the AI-facing
language reference: the Menai AI tool (e.g. Humbug's ``menai_ai_tool.py``)
fetches this text rather than maintaining its own copy, so the help can never
drift out of sync with the language.

The human-readable language manual lives in ``docs/`` and is kept consistent
with this text.
"""


def get_help() -> str:
    """Return the Menai language reference text for AI agents."""
    return _HELP_TEXT


_HELP_TEXT = """# Menai Tool
Syntax: (operator arg1 arg2 ...)

## Introduction

- Menai (AI Functional Programming Language) is a pure functional programming language designed for efficient expression evaluation.
- It is designed for AIs to use, human users are secondary.
- Operations are runtime typed but most operations will strictly only work on one specific type.
- Pure functional: no side effects, immutable data
- Syntax is similar to Scheme, but it is NOT Scheme.
- Homoiconic: code and data use same representation (s-expressions)
- Tail call optimization prevents stack overflow
- Strict type system: no implicit coercion between numeric types; use typed operators (integer+, float*, complex/) to enforce types explicitly
- Mixed-type lists supported: (list 1 "hi" #t)
- Comments: use semicolon (;) for single-line comments, e.g., ; This is a comment

## None type:

- Represents the absence of a value — distinct from #f (boolean false)
- Literal: #none
- Type predicate: (none? x) → #t if x is #none, #f otherwise
- Returned by: dict-get (missing key, no default), find-list (not found), list-index (not found), string-index (not found), string->number (unparseable), string->integer (unparseable)
- Pattern matching: (match x (#none "absent") (_ "present"))
- #none is not a boolean; (if #none ...) is a type error
- Can be stored in lists and dicts as a value

## Boolean operations

- Boolean literals: #t, #f
- Type predicate: (boolean? #t) → #t
- Equality: (boolean=? #t #t), (boolean!=? #t #f)
- (boolean-not #t) → #f (builtin function; the only boolean negation function)

## Integer operations

- All args must be integers; type error otherwise
- Literals: 42 (decimal), #xff (hex), #o755 (octal), #b1010 (binary), #d42 (explicit decimal prefix)
- Type predicate: (integer? 42) → #t, (integer? 3.14) → #f
- Inequality: (integer=? 1 1), (integer!=? 1 2)
- Ordered comparison: (integer<? 1 2), (integer>? 3 2), (integer<=? 1 1), (integer>=? 2 1)
- (integer+ 1 2 3) → 6
- (integer+) → 0
- (integer- 10 3) → 7
- (integer* 2 3 4) → 24
- (integer*) → 1 (zero-arg identities)
- (integer/ 7 3) → 2 (floor division), (integer/ -7 2) → -4
- (integer-neg 5) → -5 (unary negation); (integer- 5) is an error (requires 2+ args)
- (integer% 7 3) → 1 (modulo), (integer% -7 2) → 1, (integer% 7 -3) → -2 (result takes sign of divisor)
- (integer-abs -5) → 5 (absolute value)
- (integer-expn base exp) requires non-negative exponent; raises error for negative exponent (result would not be an integer)
- (integer-expn 2 10) → 1024, (integer-expn 3 0) → 1, (integer-expn 0 0) → 1 (exact arbitrary-precision integer exponentiation)
- Bitwise: (integer-bit-or 5 3), (integer-bit-and 7 3), (integer-bit-xor 5 3), (integer-bit-not 5)
- Bit shifts: (integer-bit-shift-left 1 3), (integer-bit-shift-right 8 2)
- (integer-min 1 2) → 1
- (integer-max 1 2) → 2
- (integer->float x) → convert integer to float: (integer->float 42) → 42.0, (integer->float 3) → 3.0
- (integer->complex real [imag]) → construct complex from one or two integers: (integer->complex 3) → 3+0j, (integer->complex 1 -2) → 1-2j
- (integer->string 42) → "42", (integer->string 255 16) → "ff", (integer->string 255 2) → "11111111", (integer->string 255 8) → "377" (optional radix: 2, 8, 10, or 16; defaults to 10)
- (integer-codepoint->string 65) → "A", (integer-codepoint->string 960) → "π", (integer-codepoint->string 128512) → "😀" (converts a Unicode scalar value to a single-character string; raises an error for surrogates 0xD800–0xDFFF or values outside 0–0x10FFFF)

## Floating point operations

- All args must be floats; type error otherwise
- Constants: pi, e
- Literals: 2.03, 43.0e9, -9.28353
- Type predicate: (float? 3.14) → #t, (float? 42) → #f, (float? (float/ 1.0 2.0)) → #t
- Equality: (float=? 1.0 1.0), (float!=? 1.0 2.0)
- Ordered comparison: (float<? 1.0 2.0), (float>? 3.0 2.0), (float<=? 1.0 1.0), (float>=? 2.0 1.0)
- (float+ 1.0 2.0 3.0) → 6.0
- (float- 10.0 3.0) → 7.0
- (float+) → 0.0
- (float* 2.0 3.0) → 6.0
- (float*) → 1.0 (zero-arg identities)
- (float/ 10.0 4.0) → 2.5
- (float// 7.0 2.0) → 3.0 (floor division)
- (float% 7.0 3.0) → 1.0 (modulo)
- (float-neg 3.0) → -3.0; (float- 3.0) and (float/ 4.0) are errors (require 2+ args)
- (float-abs -3.0) → 3.0 (absolute value)
- (float-floor 3.7) → 3.0
- (float-ceil 3.2) → 4.0
- (float-round 3.5) → 4.0 (all return float)
- (float-trunc -3.7) → -3.0 (truncate toward zero, returns float)
- (float-exp 2.0) → e^2.0
- (float-expn base exp) → base^exp: (float-expn 2.0 10.0) → 1024.0
- (float-exp2 3.0) → 8.0 (2^x)
- (float-expm1 0.0) → 0.0 (e^x − 1, numerically stable for small x)
- (float-log 1.0) → 0.0 (log base e)
- (float-log2 8.0) → 3.0 (log base 2, correctly rounded via math.log2)
- (float-log10 100.0) → 2.0 (log base 10)
- (float-logn 8.0 2.0) → 3.0 (log base n; general case, slightly less precise than float-log2/float-log10)
- (float-log1p 0.0) → 0.0 (log(1+x), numerically stable for small x; x < -1 is a runtime error, -1.0 → -inf)
- float-log/float-log10/float-log2/float-logn of zero → -inf; negative arg is a runtime error
- float-logn requires a positive base not equal to 1; invalid base is a runtime error
- (float-min 1.0 2.0) → 1.0
- (float-max 1.0 2.0) → 2.0
- (float-copysign 3.0 -1.0) → -3.0 (magnitude of first arg, sign of second)
- (float-hypot 3.0 4.0) → 5.0 (sqrt(a²+b²) without intermediate overflow/underflow)
- (float-sqrt 4.0) → 2.0
- float-sqrt of negative → runtime error (use complex-sqrt instead)
- (float-cbrt 27.0) → 3.0 (cube root; works for negative x)
- Transcendentals: (float-sin 0.0) → 0.0, (float-cos 0.0) → 1.0, (float-tan 0.0) → 0.0
- Inverse trig: (float-asin 1.0) → 1.5707963267948966, (float-acos 1.0) → 0.0, (float-atan 1.0) → 0.7853981633974483
- float-asin/float-acos require args in [-1, 1]; out-of-range is a runtime error
- (float-atan2 y x) → four-quadrant inverse tangent: (float-atan2 1.0 1.0) → 0.7853981633974483
- Hyperbolic: (float-sinh 1.0), (float-cosh 1.0), (float-tanh 1.0)
- (float->integer x) → convert float to integer (truncates toward zero): (float->integer 3.7) → 3, (float->integer -2.9) → -2
- (float->complex real [imag]) → construct complex from one or two floats: (float->complex 3.0 4.0) → 3+4j, (float->complex 3.0) → 3+0j
- (float->string 3.14) → "3.14"

## Complex number operations

- All args must be complex; type error otherwise
- Literals: 3+4j, 5j, 1j, 1.5e2j, 0j
- Type predicate: (complex? (float->complex 1.0 1.0)) → #t, (complex? 42) → #f
- Equality: (complex=? 1+2j 1+2j), (complex!=? 1+2j 1+3j)
- Complex numbers have no ordering; use (complex-abs z) to compare magnitudes as floats
- (complex+ (float->complex 1.0 2.0) 3+4j) → 4+6j
- (complex+) → 0+0j
- (complex- (float->complex 5.0 3.0) (float->complex 2.0 1.0)) → 3+2j
- (complex* 1+2j (float->complex 3.0 4.0)) → -5+10j
- (complex*) → 1+0j (zero-arg identities)
- (complex/ (float->complex 4.0 2.0) (float->complex 1.0 1.0)) → 3-1j
- (complex-real z) → float real part, (complex-imag z) → float imaginary part (complex args only)
- (complex-abs (float->complex 3.0 4.0)) → 5.0 (returns magnitude as float, not complex)
- (complex-neg 3.0+4.0j) → -3-4j
- (complex-exp z) → e^z
- (complex-expn base exp) → base^exp: (complex-expn 2+0j 10+0j) → 1024+0j
- (complex-log z) → log base e of z
- (complex-log10 z) → log base 10 of z
- (complex-logn z base) → log base n of complex z (both args must be complex)
- Transcendentals: complex-sin, complex-cos, complex-tan, complex-sqrt
- (complex->string 3+4j) → "3+4j"

## String operations

- String literals: "hello" (string)
- String literals support escapes: \\n, \\t, \\", \\\\, \\uXXXX
- Type predicate: (string? "hello") → #t
- Equality: (string=? "hi" "hi"), (string!=? "hi" "bye")
- Ordered comparison: (string<? "apple" "banana"), (string>? "b" "a"), (string<=? "a" "a"), (string>=? "b" "a")
- String ordering is Unicode codepoint order (same as Python str), not locale-aware collation
- Basic: (string-concat "hello" " " "world"), (string-length "hello")
- Access: (string-ref "hello" 1) → "e" (character at 0-based index)
- Manipulation: (string-slice "hello" 1 4), (string-slice "hello" 2) → "llo", (string-upcase "hello"), (string-downcase "HELLO")
- Utilities: (string-trim "  hello  ") → "hello", (string-trim-left "  hello  ") → "hello  ", (string-trim-right "  hello  ") → "  hello", (string-replace "banana" "a" "o") → "bonono" (replaces all occurrences)
- Search predicates: (string-prefix? "hello" "he"), (string-suffix? "hello" "lo")
- Search index: (string-index "hello" "l") → 2, (string-index "hello" "z") → #none (not found)
- Conversion: (string->number "42") → 42, (string->number "3.14") → 3.14, (string->number "1+2j") → 1+2j, (string->number "hello") → #none (returns #none for any unparseable string; raises a type error if argument is not a string)
- (string->integer "ff" 16) → 255, (string->integer "1010" 2) → 10, (string->integer "377" 8) → 255, (string->integer "42") → 42 (optional radix: 2, 8, 10, or 16; defaults to 10; returns #none for unparseable strings; invalid radix raises an error)
- Note: (string->integer "ff" 16) and (string->integer "FF" 16) both → 255 (case-insensitive); surrounding whitespace is accepted
- (string->list "hello") → ("h" "e" "l" "l" "o") (no delimiter: splits into individual characters)
- (string->list "a,b,c" ",") → ("a" "b" "c") (delimiter splits on every occurrence; consecutive delimiters produce empty strings: (string->list "a,,b" ",") → ("a" "" "b"))
- (string->list "one::two" "::") → ("one" "two") (delimiter may be multi-character)
- (string->integer-codepoint "A") → 65, (string->integer-codepoint "π") → 960, (string->integer-codepoint "😀") → 128512 (converts a single-character string to its Unicode codepoint as an integer; raises an error if the string is not exactly one character)

## List operations:

- Uses proper lists only, not cons cells
- List literals: () (empty list)
- Type predicate: (list? (list 1 2)) → #t
- Equality: (list=? (list 1 2) (list 1 2)), (list!=? (list 1 2) (list 1 3))
- Construction: (list 1 2 3), (list-prepend lst item), (list-append lst item), (list-concat lst1 lst2), (list-concat) → ()
- (list-prepend (list 2 3) 1) → (1 2 3), (list-append (list 1 2) 3) → (1 2 3)
- (list-concat (list 1 2) (list 3 4)) → (1 2 3 4), (list-concat) → () (zero-arg identity)
- Access: (list-first (list 1 2 3)) → 1
- Access: (list-rest (list 1 2 3)) → (2 3)
- Access: (list-last (list 1 2 3)) → 3
- Indexed access: (list-ref (list "a" "b" "c") 1) → "b" (0-based index)
- Properties: (list-length (list 1 2 3)), (list-null? (list)), (list-member? (list 1 2 3) 2)
- Utilities: (list-reverse (list 1 2 3)), (list-remove (list 1 2 3 2 4) 2), (list-index (list 1 2 3) 2) → 1, (list-index (list 1 2 3) 42) → #none (not found)
- Slicing: (list-slice lst start) → from start to end, (list-slice lst start end) → from start to end (exclusive)
- (list-slice (list 1 2 3 4 5) 2) → (3 4 5), (list-slice (list 1 2 3 4 5) 1 3) → (2 3)
- (list->string (list "h" "e" "l" "l" "o")) → "hello" (no separator: concatenates directly; all elements must be strings)
- (list->string (list "a" "b" "c") ",") → "a,b,c" (separator inserted between elements; separator may be multi-character)
- (list->set lst) → convert list to set (deduplicates, retains first occurrence order)
- (range start end [step]) → (range 1 5) → (1 2 3 4), integers only
- Step may be negative: (range 10 1 -2) → (10 8 6 4 2); step of 0 is an error
- If start > end and step is positive (or start < end and step is negative), the result is an empty list ()
- Higher-order: (map-list func list) → (map-list (lambda (x) (integer* x 2)) (list 1 2 3)) → (2 4 6)
- Higher-order: (filter-list predicate list) → (filter-list (lambda (x) (integer>? x 0)) (list -1 2 -3 4)) → (2 4)
- Higher-order: (fold-list func init list) → left fold (tail-recursive); processes list left-to-right, accumulating into init; func signature is (lambda (acc item) result) where acc is the current accumulator and item is the current list element: (fold-list integer+ 0 (list 1 2 3 4)) → 10, (fold-list (lambda (acc item) (list-append acc item)) (list) (list 1 2 3)) → (1 2 3)
- Higher-order: (find-list predicate list) → first element satisfying predicate, or #none if none found: (find-list (lambda (x) (integer>? x 3)) (list 1 2 3 4 5)) → 4, note: (find-list predicate ()) → #none
- Higher-order: (any-list? predicate list) → #t if at least one element satisfies predicate, #f otherwise: (any-list? (lambda (x) (integer>? x 3)) (list 1 2 3 4 5)) → #t, note: (any-list? predicate ()) → #f
- Higher-order: (all-list? predicate list) → #t if all elements satisfy predicate, #f otherwise: (all-list? (lambda (x) (integer>? x 0)) (list 1 2 3 4 5)) → #t, note: (all-list? predicate ()) → #t (vacuously true)
- (list-zip lst1 lst2) → pairs corresponding elements: (list-zip (list 1 2 3) (list 4 5 6)) → ((1 4) (2 5) (3 6)), (list-zip lst1 lst2) stops at the shorter list: (list-zip (list 1 2 3) (list 4 5)) → ((1 4) (2 5))
- Higher-order: (sort-list comparator lst) → returns a new list sorted by comparator; comparator is a two-argument function returning #t if first arg should come before second: (sort-list integer<? (list 3 1 4 1 5)) → (1 1 3 4 5), (sort-list string<? (list "b" "a" "c")) → ("a" "b" "c"); sort is stable and preserves insertion order of equal elements

## Dictionaries (dicts):

- Immutable key-value mappings with O(1) lookup performance
- Type predicate: (dict? (dict ...)) → #t
- Equality: (dict=? a1 a2), (dict!=? a1 a2)
- Output format: dicts display with curly braces: {("name" "Alice") ("age" 30)} — this is display-only; construction always uses (dict ...)
- Construction: (dict "name" "Alice" "age" 30)
- Access: (dict-get my-dict "key") → value or #none if missing, (dict-get my-dict "key" "default") → value or "default" if missing
- Note: if the default is #none, a missing key is indistinguishable from a key whose value is #none; use (dict-has? my-dict "key") to differentiate
- Modification: (dict-set my-dict "key" value), (dict-remove my-dict "key")
- Queries: (dict-has? my-dict "key"), (dict-keys my-dict), (dict-values my-dict), (dict-length my-dict)
- Merging: (dict-merge dict1 dict2) - second wins on conflicts
- Type checking: (dict? value)
- Nested dicts: (dict "user" (dict "name" "Bob" "id" 123))
- Pattern matching: (match data ((? dict? a) ...) (_ ...))
- Maintains insertion order, optimized for data processing workflows
- Higher-order: (map-dict func dict) → applies func to each (key value) pair, returning a new dict with transformed values; func receives key and value as separate arguments: (map-dict (lambda (k v) (integer* v 2)) (dict "a" 1 "b" 2)) → {("a" 2) ("b" 4)}
- Higher-order: (filter-dict pred dict) → returns a new dict containing only entries where pred returns #t; pred receives key and value as separate arguments: (filter-dict (lambda (k v) (integer>? v 1)) (dict "a" 1 "b" 2)) → {("b" 2)}

## Sets:

- Immutable unordered collections of unique hashable values with O(1) membership testing
- Valid element types: string, integer, float, complex, boolean, symbol, bytes (lists, dicts, functions, #none are not hashable)
- Type predicate: (set? x) → #t
- Equality: (set=? s1 s2), (set!=? s1 s2) — order-insensitive; two sets are equal if they contain the same elements
- Output format: sets display as #{1 2 3} — this is display-only; construction always uses (set ...)
- Construction: (set 1 2 3), (set) → empty set #{}, duplicates are silently dropped
- Membership: (set-member? s x) → #t if x is in s
- Query: (set-length s) → number of elements
- Functional update (returns new set — pure): (set-add s x) → new set with x added (no-op if already present), (set-remove s x) → new set with x removed (no-op if absent)
- Set algebra: (set-union s1 s2), (set-intersection s1 s2), (set-difference s1 s2) → s1 minus s2
- Subset test: (set-subset? s1 s2) → #t if every element of s1 is in s2
- Conversion: (set->list s) → list of elements (insertion order), (list->set lst) → set from list (deduplicates)
- Higher-order: (map-set func s) → apply func to each element, return new set; (filter-set pred s) → new set of elements satisfying pred; (fold-set func init s) → left fold over elements; func signature is (lambda (acc item) result) — same argument order as fold-list
- Predicates: (any-set? pred s) → #t if at least one element satisfies pred, #f otherwise; note: (any-set? pred (set)) → #f; (all-set? pred s) → #t if all elements satisfy pred, #f otherwise; note: (all-set? pred (set)) → #t (vacuously true)
- Pattern matching: (match x ((? set? s) ...) (_ ...))
- Sets have no ordering operators and no positional access; use (set->list s) then list operations for iteration

## Structs:

- Nominal typed record values — two struct types with the same fields are distinct types
- Declaration: (struct (field1 field2 ...)) — valid as the RHS of a let, let*, or letrec binding
- A struct definition produces a structtype value (a type descriptor), not a struct instance
- (let ((point (struct (x y)))) ...) → binds point to a structtype value; the binding name becomes the type name
- Construction: call the structtype value directly with positional field values: (point 1 2) → a point instance
- Type predicate (any struct): (struct? p) → #t for any struct instance
- Field access: (struct-get p 'x) → value of field x; field name must be a symbol
- Indexed field access: (struct-ref p 0) → value of field at index 0 (0-based)
- Functional update (returns new struct — pure): (struct-set p 'x 10) → new point with x=10, y unchanged
- Indexed functional update: (struct-set-ref p 0 10) → new point with field 0 set to 10
- Equality: (struct=? p1 p2) → #t if same type tag and all fields equal; (struct!=? p1 p2) → negation
- Display format: (point 1 2) — this is display-only; construction always uses (TypeName field1 field2 ...)
- Pattern matching destructuring form: (match p ((point x y) (integer+ x y)) (_ 0)) — compiler resolves field bindings at compile time
- Hashability: structs are hashable (usable as set members or dict keys) if all their fields are hashable scalars
- Structs are nominal: (let ((point (struct (x y))) (Vec (struct (x y)))) ...) — point and Vec are distinct types even with identical fields

## Structtype operations:

- A structtype is the type descriptor produced by (struct (field ...)) — distinct from struct instances
- Type predicate: (structtype? point) → #t if point is a structtype value; (structtype? p) → #f if p is a struct instance
- Equality: (structtype=? point point) → #t; (structtype=? point vec) → #f (different types); (structtype!=? point vec) → #t
- Instance check: (struct-is-instance? p point) → #t if p is a point instance; first arg must be a struct, second must be a structtype
- Name: (structtype-name point) → "point" (takes a structtype value, not an instance)
- Fields: (structtype-fields point) → ('x 'y) list of field name symbols (takes a structtype value)
- Get structtype from instance: (struct-type p) → returns the structtype value (e.g. point) for a given instance

## Bytes operations:

- Immutable sequences of bytes (octets, 0–255); no literal syntax — create via string-hex->bytes, string->bytes, or list->bytes
- Display format: #bytes"hex" (truncated at 64 bytes / 128 hex chars), e.g. (string-hex->bytes "504b") → #bytes"504b"
- Type predicate: (bytes? x) → #t
- Equality: (bytes=? a b), (bytes!=? a b) — variadic, 2+ args
- Ordered comparison: (bytes<? a b), (bytes>? a b), (bytes<=? a b), (bytes>=? a b) — lexicographic, variadic 2+ args
- Construction: (string-hex->bytes "504b0304") → bytes from hex string, (string->bytes "hello") → UTF-8 encoded bytes, (list->bytes (list 80 75)) → bytes from integer list (0–255)
- Conversion: (bytes->string-hex b) → hex string, (bytes->string b) → UTF-8 string (raises error on invalid UTF-8), (bytes->list b) → list of integers
- Access: (bytes-ref b 0) → integer 0–255 at 0-based index, (bytes-length b) → integer
- Slicing: (bytes-slice b start) → from start to end, (bytes-slice b start end) → from start to end (exclusive); clamps out-of-bounds to valid range
- Concatenation: (bytes-concat b1 b2 ...) → variadic, (bytes-concat) → empty bytes
- Append single byte: (bytes-append-u8 b 255) → new bytes with byte appended (value must be 0–255)
- Search: (bytes-index haystack needle) → integer offset or #none, (bytes-index-int b 75) → offset of first matching byte value or #none
- Bytes are hashable (usable as set members or dict keys)
- Multi-byte integer reads — all take (bytes offset) and return an integer:
  - Unsigned: bytes-read-u8, bytes-read-u16-le, bytes-read-u16-be, bytes-read-u24-le, bytes-read-u24-be, bytes-read-u32-le, bytes-read-u32-be, bytes-read-u64-le, bytes-read-u64-be
  - Signed: bytes-read-i8, bytes-read-i16-le, bytes-read-i16-be, bytes-read-i24-le, bytes-read-i24-be, bytes-read-i32-le, bytes-read-i32-be, bytes-read-i64-le, bytes-read-i64-be
  - Raises error if not enough bytes from offset
- Multi-byte integer appends — all take (bytes integer) and return new bytes; value must fit in the specified width:
  - Unsigned: bytes-append-u16-le, bytes-append-u16-be, bytes-append-u24-le, bytes-append-u24-be, bytes-append-u32-le, bytes-append-u32-be, bytes-append-u64-le, bytes-append-u64-be
  - Signed: bytes-append-i8, bytes-append-i16-le, bytes-append-i16-be, bytes-append-i24-le, bytes-append-i24-be, bytes-append-i32-le, bytes-append-i32-be, bytes-append-i64-le, bytes-append-i64-be
- Multi-byte integer writes — all take (bytes offset integer) and return new bytes (original unchanged); value must fit in the specified width:
  - Unsigned: bytes-write-u8, bytes-write-u16-le, bytes-write-u16-be, bytes-write-u24-le, bytes-write-u24-be, bytes-write-u32-le, bytes-write-u32-be, bytes-write-u64-le, bytes-write-u64-be
  - Signed: bytes-write-i8, bytes-write-i16-le, bytes-write-i16-be, bytes-write-i24-le, bytes-write-i24-be, bytes-write-i32-le, bytes-write-i32-be, bytes-write-i64-le, bytes-write-i64-be
  - Raises error if not enough bytes from offset
- LEB128 variable-length integers:
  - (bytes-read-uleb128 b offset) → (value next-offset) as a 2-element list; raises error if truncated
  - (bytes-append-uleb128 b value) → new bytes with unsigned LEB128 encoding; value must be non-negative
  - (bytes-read-sleb128 b offset) → (value next-offset) as a 2-element list; raises error if truncated
  - (bytes-append-sleb128 b value) → new bytes with signed LEB128 encoding
- Higher-order: (map-bytes f b) → new bytes with f applied to each byte; (filter-bytes pred b) → bytes of bytes satisfying pred; (fold-bytes f init b) → left fold over bytes; f signature is (lambda (acc byte) result) — same argument order as fold-list, where byte is an integer 0–255
- Predicates: (bytes-empty? b) → #t if length is 0; (bytes-prefix? b prefix) → #t if b starts with prefix; (bytes-suffix? b suffix) → #t if b ends with suffix
- Splitting: (bytes-split b delimiter) → list of bytes split on delimiter (delimiter must be non-empty); (bytes-split-int b byte) → list of bytes split on single byte value

## Symbol operations:

- Type predicate: (symbol? x) → #t if x is a symbol (produced by quote)
- Equality: (symbol=? a b) → #t, (symbol!=? a b) → #t if a and b are different symbols
- (symbol->string 'foo) → "foo" (extracts the symbol name as a string)
- Symbols are produced only by quote: 'foo, '(a b c) contains symbols a, b, c
- Example: (map-list symbol->string '(foo bar baz)) → ("foo" "bar" "baz")

## Function operations:

- (apply f args) → call function f with elements of list args as individual arguments
- (apply integer+ (list 1 2 3)) → 6, (apply list (list 1 2 3)) → (1 2 3)
- (apply f (list)) → calls f with zero arguments (f must accept zero args)
- apply respects all arity rules: fixed-arity functions are checked, variadic args are packed
- Type predicate: (function? (lambda (x) x)) → #t, (function? integer+) → #t
- Equality: (function=? f g) → #t if f and g are the same function object (identity, not structural equality), (function!=? f g) → #t if f and g are different function objects, (function=? integer+ integer+) → #t, (function=? integer+ integer*) → #f
- (function-min-arity f) → integer: minimum number of arguments f requires, (function-min-arity integer-abs) → 1, (function-min-arity integer+) → 0, (function-min-arity (lambda (x . rest) x)) → 1
- (function-variadic? f) → #t if f accepts more arguments than its minimum (has a rest parameter), (function-variadic? integer+) → #t, (function-variadic? integer-abs) → #f
- (function-accepts? f n) → #t if calling f with exactly n arguments satisfies its arity requirements, (function-accepts? integer-abs 1) → #t, (function-accepts? integer-abs 2) → #f, (function-accepts? integer+ 0) → #t, (function-accepts? integer+ 99) → #t, (function-accepts? (lambda (x . rest) x) 0) → #f, (function-accepts? (lambda (x . rest) x) 3) → #t

## Conditionals

- (if (integer>? 5 3) "yes" "no"), lazy evaluation: (if #t 42 0)

## And/or special forms

- (and #t #f) and (or #t #f) short-circuit and are optimized at compile time; they are not builtin functions
- All arguments must be booleans; any non-boolean argument is a type error: (and #t 42) → error
- Always return a boolean: (and #t #t) → #t, (and #t #f) → #f, (or #f #t) → #t, (or #f #f) → #f
- (and) → #t, (or) → #f (zero-arg identities)
- Unlike Scheme, and/or do NOT return the last evaluated value; they only return #t or #f

## Lambda functions

- (lambda (param1 param2 ...) body) → creates anonymous function
- The dot in a parameter list means "collect all remaining arguments into a list": (lambda (a b . rest) ...) binds a and b to the first two args and rest to a list of any remaining args; with nothing before the dot, (lambda (. rest) ...) collects all args
- (lambda (param1 . rest) body) → variadic: rest receives remaining args as a list (possibly empty)
- (lambda (. rest) body) → fully variadic: rest receives all args as a list
- ((lambda (x) (integer* x x)) 5) → 25
- ((lambda (. args) (fold-list integer+ 0 args)) 1 2 3 4 5) → 15 (variadic sum)
- ((lambda (x . rest) (list-prepend (list-reverse rest) x)) 1 2 3) → (1 3 2)
- Functions are first-class values with lexical scoping and closures
- Tail recursion automatically optimized
- Variadic functions accept any number of args beyond their fixed params; rest param is always a list (possibly empty)

## Local bindings

- (let ((var1 val1) (var2 val2) ...) body) → parallel binding (bindings independent)
- (let ((x 5) (y 10)) (integer+ x y)) → 15 (x and y don't reference each other)
- Bindings in let cannot reference each other; use let* for sequential bindings
- (let* ((var1 val1) (var2 val2) ...) body) → sequential binding
- (let* ((x 5) (y (integer* x 2))) (integer+ x y)) → 15 (y can reference x)
- (let* ((x 1) (x (integer+ x 10))) x) → 11 (shadowing works in let*)
- Use let for independent bindings, let* for sequential dependencies
- All binding forms (let, let*, letrec) and lambda accept exactly one body expression; there is no begin or sequencing form — nest let* for multi-step computations
- Menai has no define special form; all variable bindings use let, let*, or letrec; use letrec for self-recursive or mutually recursive functions

## Recursive bindings

- (letrec ((var1 val1) (var2 val2) ...) body) → recursive binding
- (letrec ((fact (lambda (n) (if (integer<=? n 1) 1 (integer* n (fact (integer- n 1))))))) (fact 5)) → 120
- Supports self-recursion and mutual recursion
- Struct definitions are also permitted in letrec and are hoisted to let automatically — use this when defining a module that exports a struct type alongside its associated functions
- Use only when you need functions that reference themselves or when defining a module API

## Quote - data literals and code as data

- (quote expr) → returns expr without evaluation
- 'expr → shortcut for (quote expr)
- '(integer+ 1 2 3) → (integer+ 1 2 3) (as data, not evaluated)
- (list 'hello (integer+ 1 2) 'world) → (hello 3 world)
- Enables symbolic programming: (list-first '(integer+ 1 2)) → integer+
- Code and data have identical representation (homoiconicity)

## Pattern matching

- (match expression (pattern1 result1) (pattern2 result2) (_ default)) → powerful declarative dispatch
- Literal patterns: (match x (42 "found") ("hello" "greeting") (_ "other"))
- Variable binding: (match x ((? integer? n) (integer* n 2)) ((? string? s) (string-upcase s)))
- Wildcard patterns: _ matches anything without binding
- Predicate patterns: (? pred var) — pred can be any expression, including user-defined predicates: (? integer? n), (? string? s), (? my-pred? x)
- Empty list: (match lst (() "empty") ((x) "singleton") (_ "multiple"))
- List destructuring: (match lst ((a b c) (integer+ a b c)) ((head . tail) (list-prepend tail head)))
- Nested patterns: (match data (((? integer? x) (? string? y)) (list x y)) (_ "no match"))
- First match wins: patterns are tested in order, use specific patterns before general ones
- Example: (match data (42 "answer") ((? integer? n) (integer* n 2)) ((? string? s) (string-upcase s)) ((head . tail) (list head (list-length tail))) (_ "unknown"))

## Module system

- (import "module-name") → load and return a module (compile-time operation)
- Modules are just .menai files that return a value (typically a dict of functions)
- Modules are cached after first load for performance
- Circular imports are detected and prevented with clear error messages
- Example module (math_utils.menai):
  ```menai
  (let ((square (lambda (x) (integer* x x)))
        (cube (lambda (x) (integer* x (integer* x x)))))
    (dict "square" square "cube" cube))
  ```
- Using a module:
  ```menai
  (let ((math (import "math_utils")))
    ((dict-get math "square") 5))  → 25
  ```
- Modules can import other modules (transitive dependencies)
- Private functions: functions not in the exported dict are private to the module
- Module names can include subdirectories: (e.g. import "lib/helpers")
- Available modules can be found in the module search path directories

## Raising errors

- (error msg) → raises a runtime error; the msg expression is evaluated normally and must produce a string
- msg can be a string literal, a variable, or any expression that evaluates to a string
- Raises immediately; no value is ever returned
- Valid in any expression position, including inside lambda bodies, let bindings, and match arms
- Used to signal invalid arguments or unrecoverable conditions
- Example: (if (integer<? n 0) (error "n must be non-negative") (float-sqrt (integer->float n)))
- Example: (error (string-concat "invalid value: " (integer->string x)))

## Important notes

- Strict typing: string ops need strings, boolean ops need booleans
- float-floor, float-ceil, float-round all return float, not integer; use (float->integer (float-round x)) to get an integer
- All comparison operators are type-specific: use integer=?, float<?, string>=? etc.
- Conditions must be boolean: (if #t ...) works, (if 1 ...) doesn't - there is no concept of "truthiness"
- and/or require boolean arguments and always return a boolean; unlike Scheme they do not return the last evaluated value
- #none is not a boolean and cannot be used as a condition; use (none? x) to test for absence
- The user CANNOT see Menai expressions or Menai results used with this tool directly; if you want to show either, you must format it as a message to the user.
- Naming convention: direct operations are named, say, `list-X` with the list as the first argument; higher-order operations are named `X-list` with the function/predicate first and the list last. The same convention applies to dicts (`dict-X` / `X-dict`), sets (`set-X` / `X-set`), and bytes (`bytes-X` / `X-bytes`).
"""
