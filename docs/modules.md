# Modules

Menai has a module system that lets you write reusable code in `.menai` files and
import them into other programs. Modules are a compile-time feature — they are
resolved, compiled, and cached before optimization passes run, which enables
cross-module optimizations.

## What is a module?

A module is a `.menai` file containing a single expression. The file name (without
the `.menai` extension) is the module name.

A module declares the bindings it makes available to importers with an `export`
form, which must be the final form of the module body:

```menai
(export name1 name2 ...)
```

Each name must be a binding the module defines (by an enclosing `let`, `let*`, or
`letrec`). A binding not named in the `export` form is private to the module.

### Example module: `math_utils.menai`

```menai
(let ((square (lambda (x) (integer* x x)))
      (cube   (lambda (x) (integer* x (integer* x x)))))
  (export square cube))
```

This module exports two functions: `square` and `cube`.

## Importing a module

`(import "module-name")` loads a module as a **namespace**. Import is a
compile-time operation, and it is only valid as the value of a `let`, `let*`, or
`letrec` binding:

```menai
(let ((math (import "math_utils")))
  ((math square) 5))
→ 25
```

You access an exported binding with member access: `(namespace member)`. The
member is resolved at compile time to the declaration that produced it, so the
compiler keeps full static knowledge of it.

## Namespaces are second-class

A namespace is a compile-time construct, not an ordinary value. A namespace name
may only be:

- bound directly by a `let`/`let*`/`letrec` binding whose value is an `import`, and
- used as the head of a member access, `(namespace member)`.

Using a namespace name anywhere else — passing it to a function, storing it in a
list, or returning it — is a compile-time error. This restriction is what lets the
compiler resolve every member access statically.

## Renaming on import

A module exports each binding under its own name. To use a different local name,
bind the member to a local name in the importer:

```menai
(let ((shapes (import "shapes")))
  (let ((Point (shapes point)))
    (Point 1 2)))
```

## Module search path

Modules are found by searching the module search path — a list of directories.
The default search path includes the current directory (`.`) and the `menai_modules`
directory. When embedding Menai, the search path can be configured:

```menai
; In the Python API:
; Menai(module_path=[".", "my_modules", "menai_modules"])
```

Module names can include subdirectories:

```menai
(import "lib/helpers")
```

This searches for `lib/helpers.menai` in each directory on the module search path.

## Module caching

Modules are compiled once and cached. If the same module is imported multiple times
(in the same program or transitively by different modules), the cached result is
used. Since Menai is pure, this is purely a performance consideration.

## Circular imports

Circular imports are detected and prevented with clear error messages. If module A
imports module B, and module B imports module A, the compiler will report the cycle
and refuse to compile.

## Transitive imports

Modules can import other modules. If module A imports module B, and module B
imports module C, then module A transitively depends on module C. All transitive
dependencies are resolved and compiled before the importing module is optimized.

## Private bindings

A binding that is not named in the module's `export` form is private to the module —
it is in scope during the module's own evaluation but is not accessible to importers.

```menai
; secret.menai
(let ((secret-key 42)                          ; private
      (validate (lambda (x) (integer=? x secret-key))))  ; exported
  (export validate))
```

The importer cannot access `secret-key`:

```menai
(let ((secret (import "secret")))
  (secret validate))   ; works — returns the validate function
  ; (secret secret-key) would be a compile-time error — it is not exported
```

## Struct types in modules

When a module exports a struct type, importers can bring it into scope by binding it
to a local name. Struct destructuring patterns are resolved against the struct types
declared in the enclosing lexical scope, so the local binding makes the imported
struct available as a constructor and pattern head:

```menai
; shapes.menai
(letrec ((point (struct (x y)))
         (make-point (lambda (x y) (point x y)))
         (point-distance (lambda (p1 p2)
                           (let ((dx (integer- (struct-get p1 'x) (struct-get p2 'x)))
                                 (dy (integer- (struct-get p1 'y) (struct-get p2 'y))))
                             (integer+ (integer* dx dx) (integer* dy dy))))))
  (export point make-point point-distance))
```

Using it:

```menai
(let ((shapes (import "shapes")))
  (let ((Point (shapes point))
        (make-point (shapes make-point))
        (distance (shapes point-distance)))
    (let ((p1 (make-point 0 0))
          (p2 (make-point 3 4)))
      (distance p1 p2))))
→ 25
```

Because `Point` is bound to the imported struct type, it can also be used as a
destructuring pattern head:

```menai
(let ((shapes (import "shapes")))
  (let ((Point (shapes point))
        (make-point (shapes make-point)))
    (match (make-point 3 4)
      ((Point x y) (integer+ x y)))))
→ 7
```

## Standard library modules

The `menai_modules/` directory contains standard library modules. Currently this
includes:

- `json_parser.menai` — a JSON parser that converts JSON strings to Menai values
- `bmp_parser.menai` — a BMP parser that converts uncompressed 24-bit and 32-bit
  BMP files (as bytes) to a dict containing the decoded header, a normalised
  top-down pixel grid, and parser metadata
- `deflate.menai` — a raw DEFLATE compressor (RFC 1951) that converts a bytes
  value to a raw DEFLATE stream; the optional second argument selects the block
  encoding (`"auto"`, `"stored"`, `"fixed"`, or `"dynamic"`), with `"auto"`
  choosing the smallest of the three.  It is the counterpart to `inflate.menai`
- `inflate.menai` — a raw DEFLATE decompressor (RFC 1951) that converts a bytes
  value containing a raw DEFLATE stream to the decompressed bytes; supports
  stored, fixed Huffman, and dynamic Huffman blocks
- `zip_parser.menai` — a ZIP archive parser that reads a ZIP file (as bytes) and
  returns its central directory as a list of entry dicts, with `parse` (metadata
  only) and `extract` (decompressed contents) entry points; supports stored and
  deflate entries
- `zlib_parser.menai` — a zlib stream decompressor (RFC 1950) that strips the
  2-byte header, delegates the DEFLATE data to `inflate`, and verifies the
  Adler-32 trailer
- `png_parser.menai` — a PNG parser that converts non-interlaced 8-bit PNG files
  (as bytes) to a dict containing the decoded header, a top-down pixel grid, and
  parser metadata.  All colour types are supported (greyscale, truecolour,
  palette, and the alpha variants), normalised to RGB or RGBA pixels

See [Examples](examples.md) for a walkthrough of the JSON parser.
