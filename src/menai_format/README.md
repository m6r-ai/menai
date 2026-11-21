# Menai Formatter

`menai-format` formats Menai source code into the canonical style defined by
[ADR-0051](../../docs/adr/0051-menai-source-formatting-style.md).  It preserves
comments, blank lines, and the exact text of every literal, so formatting only
changes whitespace.

## Usage

Formatting happens **in place**, and the positional argument takes one or more
files:

```bash
# Format files in place
menai-format myfile.menai
menai-format src/menai/prelude.menai src/menai/stdlib/*.menai

# Write the result to a new file
menai-format myfile.menai -o formatted.menai

# Print the formatted result to stdout
menai-format myfile.menai -o -

# Format from stdin
echo "(let ((x 5)(y 10)) (+ x y))" | menai-format -
```

A file is only rewritten when its content changes, so an already-formatted file
keeps its modification time.  Every file is processed even if an earlier one
fails; the exit code is non-zero if any input could not be read, formatted or
written.

`-o` requires a single input, and `-` cannot be combined with other inputs.

## Check mode

`--check` reports each input as formatted or needing formatting and exits 0 if
they all match the canonical style, 1 if any do not.  It changes nothing, so it
is the form to use in CI:

```bash
menai-format --check src/menai/prelude.menai src/menai/stdlib/*.menai
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `FILE...` | - | Files to format in place (use `-` for stdin) |
| `-o, --output` | in place | Write the result to this file (use `-` for stdout) |
| `--check` | false | Report files needing formatting; exit non-zero if any |
| `--indent` | 2 | Spaces per indentation level |
| `--max-width` | 120 | Column at which a form is laid out multi-line |
| `--comment-spacing` | 2 | Spaces before an end-of-line comment |

## Library usage

```python
from menai_format.menai_formatter import MenaiFormatter, FormatOptions

formatted = MenaiFormatter().format("(let ((x 5)(y 10)) (+ x y))")

options = FormatOptions(indent_size=4, max_width=80, comment_spacing=2)
formatted = MenaiFormatter(options).format(code)
```

## Guarantees

Formatting is **idempotent** (formatting twice equals formatting once) and
**content-preserving**: the non-comment token stream, every comment, and the exact
source text of every literal are unchanged.  Only whitespace differs.
