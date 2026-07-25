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

Tool step outputs are plain strings identified by `step_id`.

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

Output dict values must be strings.  A `#none` value means the output is absent and any
downstream step referencing it will not receive a `content` argument.

### Menai step contract

Every Menai step (whether using `expression` or `module`) must:

- Accept a dict bound to `inputs` (constructed automatically from the `inputs` map)
- Return a dict whose values are strings or `#none`

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

Supported operations: `read_file`, `read_file_lines`, `write_file`, `append_to_file`,
`delete_file`, `copy_file`, `list_directory`, `create_directory`, `remove_directory`,
`move`, `get_info`.

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
python -m menai_pipeline_runner.run <pipeline.json> [options]
```

Options:

| Flag | Description |
|------|-------------|
| `--no-optimize` | Disable adjacent Menai step collapsing |
| `--dry-run` | Validate and display the pipeline without executing it |
| `-v` / `--verbose` | Show pipeline summary and per-step status |
| `-vv` | Also show truncated output values for each step |
| `--timings` / `-t` | Show per-step elapsed time and timing bar (implies `-v`) |
| `--profile` / `-p` | Run under cProfile and print the top hotspots |
| `--profile-lines N` | Number of functions to show in profile output (default: 30) |
| `--profile-sort` | Sort key for profile output: `cumulative` (default), `tottime`, `calls`, `filename` |
| `--no-color` | Disable ANSI colour output |

## Examples

All examples are in `src/menai_pipeline_runner/examples/` and use paths relative to the repository root.  Run them
from the repository root.

### `hello-timestamp`

The simplest possible pipeline.  Gets the current time, formats a greeting in Menai,
and writes it to stdout.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/hello-timestamp/pipeline.json
```

### `file-transform`

Reads a list of fruit names, sorts and uppercases them in Menai, writes to stdout.
Demonstrates single-input single-output Menai transformation.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/file-transform/pipeline.json
```

### `multi-input`

Reads two files (a header and a body) and concatenates them in a single Menai step.
Demonstrates multiple named inputs feeding one Menai step.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/multi-input/pipeline.json
```

### `multi-output`

Reads a file containing mixed-case lines, splits them into lowercase-only and
uppercase-only groups in Menai, and writes each group to stdout separately.
Demonstrates a Menai step producing multiple outputs consumed by different downstream steps.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/multi-output/pipeline.json
```

### `adjacent-collapse`

Two adjacent Menai steps (trim whitespace, then upcase) that the optimizer collapses
into one.  Run with and without `--no-optimize` to observe identical results.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/adjacent-collapse/pipeline.json
python -m menai_pipeline_runner.run --no-optimize src/menai_pipeline_runner/examples/adjacent-collapse/pipeline.json
```

### `clock-and-file`

Reads a template file and the current timestamp simultaneously, then uses Menai to
substitute the timestamp into the template.  Demonstrates mixed tool types feeding a
single Menai step.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/clock-and-file/pipeline.json
```

### `module-step`

The same sort-and-upcase transformation as `file-transform`, but with the Menai logic
extracted into a standalone `sort-and-upcase.menai` module file.  Demonstrates the
`module` shorthand and shows how logic can be versioned and tested independently of
the pipeline that uses it.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/module-step/pipeline.json
```

### `json-parse`

Reads a JSON file and renders it back as a string using a Menai-based JSON parser
(`json_parser` from the standard library).  Demonstrates module-to-module imports
within a pipeline step and recursive value rendering.

```bash
python -m menai_pipeline_runner.run src/menai_pipeline_runner/examples/json-parse/pipeline.json
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
