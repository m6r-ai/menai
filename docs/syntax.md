# Syntax

Menai uses S-expression syntax: expressions are either atoms (single values) or
lists (parenthesised sequences of expressions). Every list expression has the form:

```menai
(operator arg1 arg2 ...)
```

The first element determines what the list means — it may be a function call, a
special form, or a macro. The remaining elements are arguments whose treatment
depends on the operator.

## Atoms

An atom is a single value that is not a list. Menai has the following atom types:

| Atom | Example | Type |
|------|---------|------|
| Integer | `42`, `#xff`, `#o755`, `#b1010` | `integer` |
| Float | `3.14`, `1.0e10`, `-9.28` | `float` |
| Complex | `3+4j`, `5j`, `1j` | `complex` |
| String | `"hello"`, `"with\nnewline"` | `string` |
| Boolean | `#t`, `#f` | `boolean` |
| None | `#none` | `none` |
| Symbol | `foo`, `integer+` (when quoted) | `symbol` |

See [Types](types.md) for full details on each type's literal syntax.

## Integer literal prefixes

Integer literals can be written in four bases using a prefix:

| Prefix | Base | Example | Value |
|--------|------|---------|-------|
| `#x` | 16 (hex) | `#xff` | 255 |
| `#o` | 8 (octal) | `#o755` | 493 |
| `#b` | 2 (binary) | `#b1010` | 10 |
| `#d` | 10 (decimal) | `#d42` | 42 |
| *(none)* | 10 (decimal) | `42` | 42 |

The `#d` prefix is optional — plain decimal integers need no prefix.

## String escapes

String literals support the following escape sequences:

| Escape | Meaning |
|--------|---------|
| `\n` | Newline |
| `\t` | Tab |
| `\"` | Double quote |
| `\\` | Backslash |
| `\uXXXX` | Unicode codepoint (4 hex digits) |

```menai
(string-length "hello\nworld")
→ 11
```

## Comments

Comments start with a semicolon `;` and continue to the end of the line:

```menai
; This is a comment
(integer+ 1 2)  ; So is this
```

Comments can appear on their own line or at the end of a line of code. There is no
block comment syntax — use multiple `;` lines for multi-line comments.

## Lists

A list literal is written as a parenthesised sequence of expressions. When
evaluated, the first element is the operator and the rest are arguments:

```menai
(integer+ 1 2 3)
→ 6

(string-concat "hello" " " "world")
→ "hello world"
```

An empty list `()` evaluates to an empty list value:

```menai
(list-length ())
→ 0
```

To create a list as a data value (rather than calling it as a function), use `list`:

```menai
(list 1 2 3)
→ (1 2 3)

(list "mixed" 42 #t)
→ ("mixed" 42 #t)
```

Lists can contain mixed types — there is no requirement that all elements share a type.

## Quote

The `'` prefix is shorthand for `(quote expr)`. It returns the expression as data
without evaluating it:

```menai
'foo
→ foo   ; a symbol

'(integer+ 1 2)
→ (integer+ 1 2)   ; a list containing a symbol and two integers

'(a b c)
→ (a b c)   ; a list of three symbols
```

See [Core forms — quote](core_forms.md#quote) for details.

## Whitespace

Whitespace (spaces, tabs, newlines) separates tokens but has no semantic meaning.
Expressions can span multiple lines:

```menai
(let ((x 5)
      (y 10))
  (integer+ x y))
→ 15
```

Indentation is for readability only — Menai does not enforce any indentation rules.