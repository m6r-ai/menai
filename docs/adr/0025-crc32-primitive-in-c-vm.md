# ADR-0025: CRC-32 is a native VM operation

Date: 2026-09-21  
Status: Accepted

## Context

Menai's binary-format parsers need a CRC-32 checksum: the ZIP parser verifies the
CRC-32 carried by each entry against the decompressed data, and the PNG parser
verifies the CRC-32 carried by each chunk. Both use CRC-32/ISO-HDLC — the
checksum zlib, PNG and ZIP all specify.

Menai has two categories of builtin: opcode-backed operations implemented
natively in the VM, and prelude-only functions implemented as Menai lambdas
(ADR-0009). The question is which category CRC-32 belongs to.

## Decision

CRC-32 is a primitive. `bytes-crc32` is an opcode-backed VM operation,
implemented natively in the VM core alongside the other primitive type
operations, and returns the checksum as an `integer` in the range 0–4294967295.

The return type is `integer`, not `bytes`. A checksum is a value that a parser
compares against an integer read from the file, so returning an integer avoids a
bytes↔integer round-trip at every call site.

## Alternatives considered

### Implement CRC-32 in the parsers as a pure-Menai function

Implement CRC-32 as a Menai lambda in `zip_parser.menai` and `png_parser.menai`.

Rejected because CRC-32 is a hot, integer-heavy kernel whose inner loop would be
dramatically slower through the language's own arithmetic and recursion, and
because it would duplicate the algorithm across two modules. CRC-32 is a
primitive operation, and primitives are implemented natively.

## Consequences

### Positive

- CRC-32 verification in the ZIP and PNG parsers runs at native speed.
- The operation is available to every program as an ordinary primitive, with no
  prelude cost, and is defined once rather than duplicated per parser.

### Negative

- CRC-32 is native code in the VM core, so each binding must implement it
  against the published standard rather than inheriting it from the language.
  The specification is the standard check value: `CRC32("123456789") ==
  0xCBF43926`.
