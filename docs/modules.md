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

### Compiling a module directly

A module file is also a valid program. When a module file is compiled directly
(for example by `menai-eval` or `menai-disassemble`) rather than imported, there
is no importer to consume its `export` form, so the module evaluates to a dict
mapping each export name to its value:

```menai
; menai-eval math_utils.menai
{("square" <square (x)>) ("cube" <cube (x)>)}
```

An `export` form that is not the body of a module is a compile-time error.

## Importing a module

`(import "module-name")` loads a module as a **namespace**. Import is a
compile-time operation, and it is only valid as the value of a `let`, `let*`, or
`letrec` binding:

```menai
(let ((math (import "math_utils")))
  ((:: math square) 5))
→ 25
```

You access an exported binding with member access: `(:: namespace member)`. The
member is resolved at compile time to the declaration that produced it, so the
compiler keeps full static knowledge of it.

## Namespaces are second-class

A namespace is a compile-time construct, not an ordinary value. A namespace name
may only be:

- bound directly by a `let`/`let*`/`letrec` binding whose value is an `import`, and
- used as the first argument of a member access, `(:: namespace member)`.

Using a namespace name anywhere else — passing it to a function, storing it in a
list, returning it, or calling it as a function — is a compile-time error. This
restriction is what lets the compiler resolve every member access statically.

Member access is a distinct form, `(:: namespace member)`, so the shape of the
form decides its meaning. A namespace name used as a call head is an error, not
member access:

```menai
(let ((math (import "math_utils")))
  (math square))          ; error: a namespace cannot be used as a function
  (:: math square))       ; correct
```

## Renaming on import

A module exports each binding under its own name. To use a different local name,
bind the member to a local name in the importer:

```menai
(let ((shapes (import "shapes")))
  (let ((Point (:: shapes point)))
    (Point 1 2)))
```

## Module search path

Modules are found by searching the module search path — a list of directories.
The path is composed of three layers, in precedence order:

1. **Explicit directories** — a `module_path` argument to `Menai`, or a
   `--module-path` flag on a tool. Highest precedence.
2. **Application library directories** — the directories in the `MENAI_PATH`
   environment variable (a list separated by the platform's path separator: `:`
   on POSIX, `;` on Windows, like `PATH`), then the source file's own directory.
3. **The standard library** — always searched last.

Resolution is first-match-wins in this order, so an application library may
shadow a standard library module of the same name.

When embedding Menai, the search path can be configured explicitly. An explicit
`module_path` is used verbatim, with no composition:

```menai
; In the Python API:
; Menai(module_path=[".", "my_modules"])
```

To compose the default path from its layers instead, use
`Menai.build_module_path(source_dir)`.

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
  (:: secret validate))   ; works — returns the validate function
  ; (:: secret secret-key) would be a compile-time error — it is not exported
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
  (let ((Point (:: shapes point))
        (make-point (:: shapes make-point))
        (distance (:: shapes point-distance)))
    (let ((p1 (make-point 0 0))
          (p2 (make-point 3 4)))
      (distance p1 p2))))
→ 25
```

Because `Point` is bound to the imported struct type, it can also be used as a
destructuring pattern head:

```menai
(let ((shapes (import "shapes")))
  (let ((Point (:: shapes point))
        (make-point (:: shapes make-point)))
    (match (make-point 3 4)
      ((Point x y) (integer+ x y)))))
→ 7
```

## Standard library modules

The standard library ships inside the `menai` package, under `stdlib/`, and is
always the lowest-precedence directory on the module search path. It is
available in every installation, including a wheel installed with `pip install
menai`. Currently it includes:

- `bmp-decode.menai` — decodes uncompressed 24-bit and 32-bit BMP files (as
  bytes) to a dict containing the decoded header, a normalised top-down pixel
  grid, and metadata (`decode`)
- `bmp-encode.menai` — encodes a decoded BMP description (as a dict) to an
  uncompressed 24-bit or 32-bit BMP file; the inverse of `bmp-decode`, so
  `(decode (encode v))` reproduces `v` for every value `encode` accepts.  The
  derived header fields are recomputed and the descriptive fields preserved
  (`encode`)
- `csv-decode.menai` — decodes RFC 4180 CSV text (as a string) to a vector of
  rows, each row a vector of field strings, so both rows and fields are
  reachable in O(1) by position.  Quoted fields, embedded delimiters and line
  terminators, and doubled quotes are handled; the optional second argument is
  the single-character field delimiter (default `,`) (`decode`)
- `csv-encode.menai` — encodes a table of field strings (a vector of rows, each
  a vector of strings) as RFC 4180 CSV text; the inverse of `csv-decode`, so
  `(decode (encode v))` reproduces `v` for every value `encode` accepts.
  Records are separated by CRLF with no trailing terminator, and fields are
  quoted only when necessary; the optional second argument is the
  single-character field delimiter (default `,`) (`encode`)
- `deflate-compress.menai` — compresses a bytes value to a raw DEFLATE stream
  (RFC 1951); the optional second argument selects the block encoding (`"auto"`,
  `"stored"`, `"fixed"`, or `"dynamic"`), with `"auto"` choosing the smallest of
  the three (`compress`)
- `deflate-decompress.menai` — decompresses a raw DEFLATE stream (RFC 1951) to
  bytes; supports stored, fixed Huffman, and dynamic Huffman blocks
  (`decompress`)
- `deflate-tables.menai` — the constant tables defined by RFC 1951, shared by
  `deflate-compress` and `deflate-decompress`.  Not an operation module; it
  exports specification data
- `gzip-compress.menai` — compresses bytes to a gzip stream (RFC 1952), writing
  the fixed 10-byte header, delegating the DEFLATE data to `deflate-compress`,
  and appending the CRC-32 and ISIZE trailer; the optional second argument
  selects the DEFLATE block encoding (`compress`)
- `gzip-decompress.menai` — decompresses a gzip stream (RFC 1952), verifying the
  magic number and compression method, parsing and skipping the optional header
  fields, delegating the DEFLATE data to `deflate-decompress`, and verifying the
  CRC-32 and ISIZE trailer.  Only the first member of a concatenated stream is
  decompressed (`decompress`)
- `json-decode.menai` — decodes a JSON string to the equivalent Menai value
  (`decode`)
- `json-encode.menai` — encodes a Menai value as a JSON string; the inverse of
  `json-decode`, so `(decode (encode v))` reproduces `v` for every value
  `encode` accepts (`encode`)
- `png-decode.menai` — decodes non-interlaced 8-bit PNG files (as bytes) to a
  dict containing the decoded header, a top-down pixel grid, and metadata.  All
  colour types are supported (greyscale, truecolour, palette, and the alpha
  variants), normalised to RGB or RGBA pixels (`decode`)
- `png-encode.menai` — encodes a decoded PNG description (as a dict) to a
  non-interlaced 8-bit PNG file.  It emits truecolour (type 2) or truecolour
  with alpha (type 6) according to the channel count; the decoder normalises
  every source colour type to RGB or RGBA, so the other colour types cannot be
  reconstructed.  The inverse of `png-decode` for truecolour images (`encode`)
- `regexp.menai` — compiles a regular-expression pattern and searches, splits,
  or replaces within strings.  Exports the compiled-regexp type `regexp` and the
  operations `compile`, `search`, `search?`, `search-all`, `split`, and
  `replace`.  A match is reported as a half-open `(start end)` index pair, so
  `(string-slice s start end)` is the matched text.  The pattern dialect is
  documented in [ADR-0042](adr/0042-regular-expression-matching.md)
- `tar-entries.menai` — reads a tar archive (as bytes) and returns a dict with an
  `"entries"` list of entry dicts and a `"meta"` dict, without returning entry
  contents.  Both POSIX ustar and GNU tar are accepted, including GNU long names
  and PAX extended headers; entry checksums are verified (`entries`)
- `tar-extract.menai` — reads a tar archive and returns the same entry dicts with
  each entry's `"content"` filled in.  Exports three operations: `extract` (all
  entries), `extract-entry` (the single entry with a given name, raising if it is
  missing or ambiguous), and `extract-matching` (every entry with a given name,
  as a list, empty when none match)
- `tar-create.menai` — builds a POSIX ustar archive (as bytes) from a container
  dict of the shape `tar-entries` and `tar-extract` produce; the write side of
  the tar readers.  Supported entry types are regular files, directories, and
  symbolic links (`create`)
- `zip-entries.menai` — reads a ZIP file (as bytes) and returns its central
  directory as a list of entry dicts, without decompressing the contents
  (`entries`)
- `zip-extract.menai` — reads a ZIP file and returns the same entry dicts with
  each entry's contents decompressed and its CRC-32 verified (`extract`)
- `zip-create.menai` — builds a ZIP file (as bytes) from a container dict of
  the shape `zip-entries` and `zip-extract` produce, with stored or deflate
  entries; the write side of the ZIP readers (`create`)
- `zlib-compress.menai` — compresses bytes to a zlib stream (RFC 1950), writing
  the 2-byte header, delegating the DEFLATE data to `deflate-compress`, and
  appending the Adler-32 trailer; the optional second argument selects the
  DEFLATE block encoding (`compress`)
- `zlib-decompress.menai` — decompresses a zlib stream (RFC 1950), stripping the
  2-byte header, delegating the DEFLATE data to `deflate-decompress`, and
  verifying the Adler-32 trailer (`decompress`)

A standard library module is one of three kinds.  An **operation module** does
one thing and is named `<format>-<operation>`: the format names the thing the
module operates on, and the operation names what it does.  It exports its
operation under the same name as the operation in its module name, so
`json-decode` exports `decode`.  Operations that are inverses use a symmetric
pair of names (`decode`/`encode`, `compress`/`decompress`); operations that are
not inverses are named individually (`entries`, `extract`, `create`).  A
**shared-data module** holds constant data several operation modules need and is
named for what it holds (`deflate-tables`).  A **capability module** provides a
whole capability that is not a single operation; it is named for the capability
and exports its members under their bare operation names, so `regexp` exports
`compile`, `search`, and `split`.  See
[ADR-0041](adr/0041-standard-library-module-taxonomy.md) for the full taxonomy.

See [Examples](examples.md) for a walkthrough of the JSON decoder.
