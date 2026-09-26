# Menai Pipeline Runner

A pipeline engine that connects Menai expressions with filesystem, clock, and
console tools.  Pipelines are defined as JSON files and executed from the command line.

## Concepts

### Steps

A pipeline is a sequence of steps.  Each step has a unique `id` and belongs to one of two
categories:

**Tool steps** invoke a tool directly:

```json
{
  "id": "read-input",
  "tool": "filesystem",
  "operation": "read_file",
  "path": "data/input.txt"
}
```

**Menai steps** evaluate a pure functional expression:

```json
{
  "id": "transform",
  "tool": "menai",
  "inputs": { "content": "read-input" },
  "expression": "(dict \"result\" (string-upcase (dict-get inputs \"content\")))",
  "outputs": {}
}
```

Or reference a `.menai` module file via `module` (mutually exclusive with `expression`):

```json
{
  "id": "transform",
  "tool": "menai",
  "inputs": { "content": "read-input" },
  "module": "my-transform",
  "outputs": {}
}
```

### Data flow

Tool step outputs are identified by `step_id`.  Most are plain strings; the
`filesystem.read_bytes` operation produces a Menai `bytes` value instead.

Menai steps receive a dict called `inputs` constructed from named upstream step outputs,
and must return a dict.  Each key in the returned dict becomes available to downstream
steps as `step_id.key`.

Tool steps consume Menai output via `value_from`:

```json
{
  "id": "write-output",
  "tool": "console",
  "operation": "write_stdout",
  "value_from": "transform.result"
}
```

Output dict values must be strings or `bytes`.  A `#none` value means the output is
absent and any downstream step referencing it will not receive a `content` argument.

### Binary data

Binary files are read with the `filesystem` tool's `read_bytes` operation, which
delivers the file's contents to a Menai step as a real `bytes` value (not a hex
string).  A Menai step can then parse it with the bytes builtins:

```json
{
  "id": "read-bmp",
  "tool": "filesystem",
  "operation": "read_bytes",
  "path": "image.bmp"
}
```

Bytes values flow between steps without conversion.  When a bytes value is
rendered into a downstream step, it is handled natively; when a step needs to
emit text (for example for `console.write_stdout`), it converts the parsed
structure to a string itself.

### Menai step contract

Every Menai step (whether using `expression` or `module`) must:

- Accept a dict bound to `inputs` (constructed automatically from the `inputs` map)
- Return a dict whose values are strings, `bytes`, or `#none`

### Module files

A module referenced by `module` must export a dict containing a `"run"` key whose value
is a function that accepts `inputs` and returns a dict:

```menai
(dict
  "run" (lambda (inputs)
    (let ((content (dict-get inputs "content")))
      (dict "result" (string-upcase content)))))
```

Module names are resolved relative to the pipeline file's directory first, then the
global `menai_modules/` directory.  Subdirectory paths like `"lib/helpers"` are
supported.  Absolute paths and `../` navigation are not permitted.

### Optimizer

Adjacent Menai steps are automatically collapsed into a single step before execution.
Because Menai is pure, this is always semantically equivalent.  Use `--no-optimize` to
disable this.

## Tools

### `filesystem`

Read operations require no
authorization.  Write operations prompt for confirmation on stdin/stdout.

Supported operations: `read_file`, `read_bytes`, `read_file_lines`, `write_file`,
`append_to_file`, `delete_file`, `copy_file`, `list_directory`, `create_directory`,
`remove_directory`, `move`, `get_info`.

`read_file` decodes to text using the `encoding` argument (default `utf-8`) and
fails on invalid encodings.  `read_bytes` returns the raw file contents as a
Menai `bytes` value and is the operation to use for binary formats.

### `clock`

Provides time queries, sleep, and alarm operations.

Supported operations: `get_time`, `sleep`, `alarm`.

Parameters: `format` (`iso` or `timestamp`), `timezone` (e.g. `UTC`, `America/New_York`).

### `console`

Writes pipeline output to stdout or stderr without requiring a filesystem write.

Supported operations: `write_stdout`, `write_stderr`.

## Usage

Run from the repository root with the virtual environment active:

```bash
python -m menai_pipeline.run <pipeline.json> [options]
```

Options:

| Flag | Description |
|------|-------------|
| `--no-optimize` | Disable adjacent Menai step collapsing |
| `--dry-run` | Validate and display the pipeline without executing it |
| `-v` / `--verbose` | Show pipeline summary and per-step status |
| `-vv` | Also show truncated output values for each step |
| `--timings` / `-t` | Show per-step elapsed time and timing bar (implies `-v`) |
| `--cprofile` | Run the pipeline under Python's cProfile and print the top hotspots |
| `--cprofile-lines N` | Number of functions to show in cProfile output (default: 30) |
| `--cprofile-sort` | Sort key for cProfile output: `cumulative` (default), `tottime`, `calls`, `filename` |
| `--profile` | Profile VM execution per Menai step, ranking functions by instructions executed |
| `--opcodes` | Profile VM execution per Menai step with per-opcode frequency counting |
| `--annotate` | Annotate the disassembly of every function in each Menai step with per-instruction execution shares |
| `--top N` | Show top N entries in VM profile output (default: 40) |
| `--no-color` | Disable ANSI colour output |

## Profiling

Two kinds of profiling are available.  They are independent and may be combined.

### `--cprofile` — Python-level cProfile

Runs the whole pipeline under Python's `cProfile` and prints the top hotspots.
This profiles the pipeline engine itself (the Python code that drives the steps),
not the Menai VM.  `--cprofile-lines N` controls how many functions are shown
(default: 30) and `--cprofile-sort` selects the sort key.

### `--profile`, `--opcodes`, `--annotate` — VM profiling

These three modes profile Menai VM execution.  Only Menai steps execute VM code,
so tool steps (`filesystem`, `clock`, `console`) contribute nothing and are
absent from the reports.  Because each Menai step compiles and executes its own
program, the per-function and annotated views are reported per step.

- `--profile` ranks functions within each Menai step by the number of
  instructions they executed, showing each function's share of that step's total
  and its call count.
- `--opcodes` reports the per-opcode execution frequency, aggregated across all
  Menai steps (opcode counts are additive), with a per-step breakdown when the
  pipeline has more than one Menai step.
- `--annotate` renders the annotated disassembly of every function in each Menai
  step, with each instruction's execution count and its share of that step's
  total.

`--top N` limits how many entries appear in the `--profile` and `--opcodes`
reports.  Percentages are of instruction count, not time: per-instruction timing
is not measurable at VM speeds.

VM profiling does not change the pipeline's results.  Profiling overhead is
included in the reported step timings.

## Examples

All examples are in `src/menai_pipeline/examples/` and use paths relative to the repository root.  Run them
from the repository root.

### `hello-timestamp`

The simplest possible pipeline.  Gets the current time, formats a greeting in Menai,
and writes it to stdout.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/hello-timestamp/pipeline.json
```

### `file-transform`

Reads a list of fruit names, sorts and uppercases them in Menai, writes to stdout.
Demonstrates single-input single-output Menai transformation.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/file-transform/pipeline.json
```

### `multi-input`

Reads two files (a header and a body) and concatenates them in a single Menai step.
Demonstrates multiple named inputs feeding one Menai step.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/multi-input/pipeline.json
```

### `multi-output`

Reads a file containing mixed-case lines, splits them into lowercase-only and
uppercase-only groups in Menai, and writes each group to stdout separately.
Demonstrates a Menai step producing multiple outputs consumed by different downstream steps.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/multi-output/pipeline.json
```

### `adjacent-collapse`

Two adjacent Menai steps (trim whitespace, then upcase) that the optimizer collapses
into one.  Run with and without `--no-optimize` to observe identical results.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/adjacent-collapse/pipeline.json
python -m menai_pipeline.run --no-optimize src/menai_pipeline/examples/adjacent-collapse/pipeline.json
```

### `clock-and-file`

Reads a template file and the current timestamp simultaneously, then uses Menai to
substitute the timestamp into the template.  Demonstrates mixed tool types feeding a
single Menai step.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/clock-and-file/pipeline.json
```

### `module-step`

The same sort-and-upcase transformation as `file-transform`, but with the Menai logic
extracted into a standalone `sort-and-upcase.menai` module file.  Demonstrates the
`module` shorthand and shows how logic can be versioned and tested independently of
the pipeline that uses it.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/module-step/pipeline.json
```

### `json-parse`

Reads a JSON file and renders it back as a string using a Menai-based JSON decoder
(`json-decode` from the standard library).  Demonstrates module-to-module imports
within a pipeline step and recursive value rendering.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/json-parse/pipeline.json
```

### `bmp-parse`

Reads a BMP image as raw bytes and decodes it with the `bmp-decode` standard
library module, rendering the decoded pixel grid as text.  Demonstrates binary
file reading via `read_bytes`, the bytes data flow through a Menai step, and
binary format decoding.

```bash
python -m menai_pipeline.run src/menai_pipeline/examples/bmp-parse/pipeline.json
```

## Pipeline JSON reference

```json
{
  "steps": [
    {
      "id": "step-id",
      "tool": "filesystem|clock|console|menai",
      "operation": "...",
      "...": "tool-specific arguments",
      "value_from": "other-step-id.key"
    },
    {
      "id": "menai-step-id",
      "tool": "menai",
      "inputs": {
        "input-name": "source-step-id"
      },
      "expression": "(dict \"key\" value ...)",
      "outputs": {
        "key": "target-step-id"
      }
    },
    {
      "id": "menai-module-step-id",
      "tool": "menai",
      "inputs": {
        "input-name": "source-step-id"
      },
      "module": "module-name",
      "outputs": {
        "key": "target-step-id"
      }
    }
  ]
}
```

The `outputs` map on a Menai step is informational — it documents which downstream steps
consume each output key.  The engine uses `value_from` on tool steps to actually route
values; the `outputs` map is not enforced at runtime.
