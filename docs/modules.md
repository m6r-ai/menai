# Modules

Menai has a module system that lets you write reusable code in `.menai` files and
import them into other programs. Modules are a compile-time feature — they are
resolved, compiled, and cached before optimization passes run, which enables
cross-module optimizations.

## What is a module?

A module is a `.menai` file containing a single expression that evaluates to a
value — typically a dict of exported functions. The file name (without the `.menai`
extension) is the module name.

### Example module: `math_utils.menai`

```menai
(let ((square (lambda (x) (integer* x x)))
      (cube   (lambda (x) (integer* x (integer* x x)))))
  (dict "square" square "cube" cube))
```

This module exports two functions: `square` and `cube`.

## Importing a module

`(import "module-name")` loads a module and returns its value. Import is a
compile-time operation.

```menai
(let ((math (import "math_utils")))
  ((dict-get math "square") 5))
→ 25
```

The returned value is whatever the module's expression evaluates to — in this case,
a dict. You access individual exports with `dict-get`.

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
used. This means module-level side effects — if there were any — would only execute
once. Since Menai is pure, this is purely a performance consideration.

## Circular imports

Circular imports are detected and prevented with clear error messages. If module A
imports module B, and module B imports module A, the compiler will report the cycle
and refuse to compile.

## Transitive imports

Modules can import other modules. If module A imports module B, and module B
imports module C, then module A transitively depends on module C. All transitive
dependencies are resolved and compiled before the importing module is optimized.

## Private functions

A module is just an expression that evaluates to a value (typically a dict).
Functions that are not included in the exported dict are private to the module —
they are in scope during the module's own evaluation but are not accessible to
importers.

```menai
; secret.menai
(let ((secret-key 42)                          ; private
      (validate (lambda (x) (integer=? x secret-key))))  ; private
  (dict "validate" validate))                  ; only validate is exported
```

The importer cannot access `secret-key`:

```menai
(let ((secret (import "secret")))
  (dict-get secret "validate"))   ; works — returns the validate function
  ; (dict-get secret "secret-key") would return #none — it's not exported
```

## Struct types in modules

When a module exports a struct type, importers can use it for construction, pattern
matching, and type checks. Use `letrec` to define the struct type alongside its
associated functions:

```menai
; shapes.menai
(letrec ((point (struct (x y)))
         (make-point (lambda (x y) (point x y)))
         (point-distance (lambda (p1 p2)
                           (let ((dx (integer- (struct-get p1 'x) (struct-get p2 'x)))
                                 (dy (integer- (struct-get p1 'y) (struct-get p2 'y))))
                             (integer+ (integer* dx dx) (integer* dy dy)))))
  (dict "point" point "make-point" make-point "point-distance" point-distance))
```

Using it:

```menai
(let ((shapes (import "shapes")))
  (let ((point (dict-get shapes "point"))
        (make-point (dict-get shapes "make-point"))
        (distance (dict-get shapes "point-distance")))
    (let ((p1 (make-point 0 0))
          (p2 (make-point 3 4)))
      (distance p1 p2))))
→ 25
```

## Standard library modules

The `menai_modules/` directory contains standard library modules. Currently this
includes:

- `json_parser.menai` — a JSON parser that converts JSON strings to Menai values

See [Examples](examples.md) for a walkthrough of the JSON parser.