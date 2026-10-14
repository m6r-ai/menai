# Menai Formatter

`menai-format` formats Menai source code into the canonical style defined by
[ADR-0051](../../docs/adr/0051-menai-source-formatting-style.md).  It preserves
comments, blank lines, and the exact text of every literal, so formatting only
changes whitespace.

## Usage

```bash
# Format a file and print to stdout
menai-format myfile.menai

# Format and save to a new file
menai-format myfile.menai -o formatted.menai

# Format in-place
menai-format myfile.menai --in-place

# Format from stdin
echo "(let ((x 5)(y 10)) (+ x y))" | menai-format -
```

## Check mode

`--check` exits 0 if the file already matches the canonical style and 1 if it
does not.  This is the form to use in CI:

```bash
menai-format --check src/menai/prelude.menai src/menai/stdlib/*.menai
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `input` | - | Input file (use `-` for stdin) |
| `-o, --output` | stdout | Output file |
| `-i, --in-place` | false | Format the input file in place |
| `--indent` | 2 | Spaces per indentation level |
| `--max-width` | 100 | Column at which a form is laid out multi-line |
| `--comment-spacing` | 2 | Spaces before an end-of-line comment |
| `--check` | false | Exit non-zero if the file needs formatting |

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
