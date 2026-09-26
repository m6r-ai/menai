"""Pipeline execution engine."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from menai import Menai, MenaiError
from menai.bytecode.menai_bytecode import CodeObject
from menai.menai_value import (
    MenaiBoolean, MenaiBytes, MenaiDict, MenaiFloat, MenaiInteger, MenaiList,
    MenaiNone, MenaiString, MenaiValue,
)
from menai_trace.menai_trace_data import TraceResult, resolve_trace

from menai_pipeline.pipeline_step import MenaiStep, Pipeline, ToolStep, resolve_step_expression
from menai_pipeline.pipeline_tools import (
    ClockTool, ConsoleTool, FilesystemTool,
    PipelineAuthorizationDenied, PipelineToolError,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class StepResult:
    """Result from executing a single pipeline step."""
    step_id: str
    success: bool
    value: str = ""
    error: str = ""
    elapsed_s: float = 0.0


@dataclass
class StepProfile:
    """
    VM instrumentation collected from a single Menai step.

    Only Menai steps produce a profile; tool steps have no VM execution to
    measure and are absent from the profile list.

    opcode_counts holds the per-opcode execution histogram for the step's
    execute() call, keyed by opcode name, with the special key "__total__"
    mapping to the step's total instruction count.  trace holds the resolved
    per-instruction and per-function counts, or None when tracing was not
    requested.
    """
    step_id: str
    opcode_counts: dict[str, int] = field(default_factory=dict)
    trace: TraceResult | None = None


@dataclass
class PipelineResult:
    """Result from executing a complete pipeline."""
    success: bool
    step_results: list[StepResult] = field(default_factory=list)
    step_profiles: list[StepProfile] = field(default_factory=list)
    error: str = ""


class PipelineExecutionError(Exception):
    """Raised when pipeline execution fails."""


def _menai_value_to_python(value: MenaiValue) -> Any:
    """
    Convert a MenaiValue to a plain Python value for use in tool arguments.

    Args:
        value: Menai value to convert

    Returns:
        Equivalent Python value

    Raises:
        PipelineExecutionError: If the value type cannot be converted
    """
    if isinstance(value, MenaiString):
        return value.value

    if isinstance(value, MenaiBytes):
        return value.value

    if isinstance(value, MenaiInteger):
        return value.value

    if isinstance(value, MenaiFloat):
        return value.value

    if isinstance(value, MenaiBoolean):
        return value.value

    if isinstance(value, MenaiList):
        return [_menai_value_to_python(item) for item in value.elements]

    if isinstance(value, MenaiDict):
        return {
            _menai_value_to_python(k): _menai_value_to_python(v)
            for k, v in value.pairs
        }

    raise PipelineExecutionError(
        f"Cannot convert Menai value of type '{type(value).__name__}' to a tool argument"
    )


def _python_to_menai_value(value: Any) -> MenaiValue:
    """
    Convert a plain Python value to a MenaiValue.

    Strings, numbers, booleans, bytes, lists, and dicts are converted
    recursively.  The value is handed to the engine directly rather than being
    rendered into source text.

    Args:
        value: Python value to convert

    Returns:
        Equivalent MenaiValue

    Raises:
        PipelineExecutionError: If the value type cannot be converted
    """
    if value is None:
        return MenaiNone()

    if isinstance(value, bool):
        return MenaiBoolean(value)

    if isinstance(value, int):
        return MenaiInteger(value)

    if isinstance(value, float):
        return MenaiFloat(value)

    if isinstance(value, str):
        return MenaiString(value)

    if isinstance(value, bytes):
        return MenaiBytes(value)

    if isinstance(value, list):
        return MenaiList(tuple(_python_to_menai_value(item) for item in value))

    if isinstance(value, dict):
        return MenaiDict(tuple(
            (_python_to_menai_value(k), _python_to_menai_value(v))
            for k, v in value.items()
        ))

    raise PipelineExecutionError(
        f"Cannot convert Python value of type '{type(value).__name__}' to a Menai value"
    )


def _format_step_value(value: str | bytes) -> str:
    """
    Format a step output value for display in step results.

    String values are returned unchanged.  Bytes are summarised by length so
    that large binary outputs do not flood the console.

    Args:
        value: Step output value

    Returns:
        Display string for the value
    """
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"

    return value


def _resolve_step_inputs(step: MenaiStep, step_outputs: dict[str, str | bytes]) -> MenaiDict:
    """
    Build the inputs dict for a step from named upstream step outputs.

    For tool step sources, the output is stored directly under the step ID.
    For Menai step sources, the output is stored as 'step_id.input_name',
    so we try both forms.

    Args:
        step: The Menai step to resolve inputs for
        step_outputs: Map of step_id or step_id.key -> string output from prior steps

    Returns:
        MenaiDict mapping each input name to its value

    Raises:
        PipelineExecutionError: If a required input step output is missing
    """
    pairs: list[tuple[MenaiValue, MenaiValue]] = []
    for input_name, source_step_id in step.inputs.items():
        composite_key = f"{source_step_id}.{input_name}"
        if composite_key in step_outputs:
            value = step_outputs[composite_key]

        elif source_step_id in step_outputs:
            value = step_outputs[source_step_id]

        else:
            raise PipelineExecutionError(
                f"Menai step '{step.step_id}': input '{input_name}' requires output "
                f"from step '{source_step_id}' which has not been executed"
            )

        pairs.append((MenaiString(input_name), _python_to_menai_value(value)))

    return MenaiDict(tuple(pairs))


def _execute_menai_step(
    step: MenaiStep,
    step_outputs: dict[str, str | bytes],
    menai: Menai,
) -> tuple[dict[str, str | bytes], CodeObject]:
    """
    Execute a Menai step and return its outputs and compiled code object.

    The step expression must evaluate to a Menai dict.  Each key in the
    dict that appears in step.outputs is extracted and stored.  Keys with
    a #none value are skipped (treated as absent outputs).

    Args:
        step: The Menai step to execute
        step_outputs: Map of step_id -> raw string or bytes output from prior steps
        menai: Menai evaluator instance

    Returns:
        A pair of (outputs, code) where outputs maps each output key to its
        string or bytes value for all non-none outputs, and code is the
        compiled code object that was executed.

    Raises:
        PipelineExecutionError: If evaluation fails or result is not a dict
    """
    expression = resolve_step_expression(step)
    inputs = _resolve_step_inputs(step, step_outputs)

    try:
        code = menai.compile(expression, inject=("inputs", inputs))
        result = menai.execute_raw(code)

    except MenaiError as e:
        raise PipelineExecutionError(
            f"Menai step '{step.step_id}' evaluation failed: {e}"
        ) from e

    if not isinstance(result, MenaiDict):
        raise PipelineExecutionError(
            f"Menai step '{step.step_id}' must return a dict, "
            f"got '{type(result).__name__}'"
        )

    outputs: dict[str, str | bytes] = {}
    for key_value, val in result.pairs:
        if not isinstance(key_value, MenaiString):
            raise PipelineExecutionError(
                f"Menai step '{step.step_id}': output dict keys must be strings"
            )

        key = key_value.value

        if isinstance(val, MenaiNone):
            continue

        if isinstance(val, MenaiString):
            outputs[key] = val.value

        elif isinstance(val, MenaiBytes):
            outputs[key] = val.value

        else:
            raise PipelineExecutionError(
                f"Menai step '{step.step_id}': output dict value for key '{key}' "
                f"must be a string, bytes, or #none, got '{type(val).__name__}'"
            )

    return outputs, code


def _get_tool(tool_name: str) -> Any:
    """
    Return the tool instance for the given tool name.

    Args:
        tool_name: Name of the tool

    Returns:
        Tool instance

    Raises:
        PipelineExecutionError: If the tool name is unknown
    """
    tools = {
        "filesystem": FilesystemTool(),
        "clock": ClockTool(),
        "console": ConsoleTool(),
    }

    tool = tools.get(tool_name)
    if tool is None:
        raise PipelineExecutionError(f"Unknown tool: '{tool_name}'")

    return tool


def _resolve_value_from(
    value_from: str | None,
    step_outputs: dict[str, str | bytes],
    step_id: str,
) -> str | bytes | None:
    """
    Resolve a value_from reference to a string or bytes value.

    Args:
        value_from: 'step_id.key' reference string, or None
        step_outputs: Map of step_id -> raw string or bytes output from prior steps
        step_id: Current step ID (for error messages)

    Returns:
        Resolved string or bytes value, or None if value_from is None

    Raises:
        PipelineExecutionError: If the reference cannot be resolved
    """
    if value_from is None:
        return None

    ref_step_id, key = value_from.split(".", 1)

    composite_key = f"{ref_step_id}.{key}"
    if composite_key in step_outputs:
        return step_outputs[composite_key]

    raise PipelineExecutionError(
        f"Step '{step_id}': 'value_from' references '{value_from}' "
        f"but key '{key}' was not found in output of step '{ref_step_id}'"
    )


def _execute_tool_step(
    step: ToolStep,
    step_outputs: dict[str, str | bytes],
) -> str | bytes:
    """
    Execute a tool step and return its string or bytes output.

    If the step has a value_from, the resolved value is injected into
    the step arguments as 'content' (the standard write parameter).

    Args:
        step: The tool step to execute
        step_outputs: Map of step_id -> raw string or bytes output and 'step.key' entries

    Returns:
        String or bytes output from the tool

    Raises:
        PipelineExecutionError: If execution fails
        PipelineAuthorizationDenied: If the user denies authorization
    """
    arguments = dict(step.arguments)

    if step.value_from is not None:
        resolved = _resolve_value_from(step.value_from, step_outputs, step.step_id)
        if resolved is not None:
            arguments["content"] = resolved

    tool = _get_tool(step.tool)

    try:
        return tool.execute(step.operation, arguments)

    except PipelineToolError as e:
        raise PipelineExecutionError(
            f"Step '{step.step_id}' ({step.tool}.{step.operation}) failed: {e}"
        ) from e


def execute_pipeline(
    pipeline: Pipeline,
    on_step_start: Callable[[str], None] | None = None,
    on_step_done: Callable[['StepResult'], None] | None = None,
    instrument: bool = False,
) -> PipelineResult:
    """
    Execute a pipeline, running each step in order.

    Tool step outputs are stored by step ID.  Menai step outputs are stored
    by 'step_id.key' for each key in the output dict, allowing downstream
    tool steps to reference them via value_from.

    Args:
        pipeline: The pipeline to execute
        on_step_start: Optional callback invoked with the step ID before each step runs
        on_step_done: Optional callback invoked with the StepResult after each step
        instrument: When True, enable VM profiling so that each Menai step's
            opcode histogram and instruction/call trace are collected and
            returned in PipelineResult.step_profiles.  Instrumentation does
            not change the pipeline's results.

    Returns:
        PipelineResult with per-step results, per-step VM profiles (when
        instrumenting), and overall success/failure
    """
    menai = Menai(module_path=[
        str(pipeline.directory),
        str(_REPO_ROOT / "menai_modules"),
    ])

    step_outputs: dict[str, str | bytes] = {}
    step_results: list[StepResult] = []
    step_profiles: list[StepProfile] = []

    for step in pipeline.steps:
        if on_step_start is not None:
            on_step_start(step.step_id)

        step_start = time.monotonic()
        try:
            if isinstance(step, MenaiStep):
                if instrument:
                    menai.vm.enable_profiling()

                outputs, code = _execute_menai_step(step, step_outputs, menai)
                for key, value in outputs.items():
                    step_outputs[f"{step.step_id}.{key}"] = value

                if instrument:
                    step_profiles.append(_collect_step_profile(step.step_id, code, menai))

                step_results.append(StepResult(
                    step_id=step.step_id,
                    success=True,
                    value=str(outputs),
                    elapsed_s=time.monotonic() - step_start,
                ))
                if on_step_done is not None:
                    on_step_done(step_results[-1])

            elif isinstance(step, ToolStep):
                output = _execute_tool_step(step, step_outputs)
                step_outputs[step.step_id] = output
                step_results.append(StepResult(
                    step_id=step.step_id,
                    success=True,
                    value=_format_step_value(output),
                    elapsed_s=time.monotonic() - step_start,
                ))
                if on_step_done is not None:
                    on_step_done(step_results[-1])

        except PipelineAuthorizationDenied as e:
            step_results.append(StepResult(
                step_id=step.step_id,
                success=False,
                error=f"Authorization denied: {e}",
                elapsed_s=time.monotonic() - step_start,
            ))
            if on_step_done is not None:
                on_step_done(step_results[-1])

            return PipelineResult(
                success=False,
                step_results=step_results,
                step_profiles=step_profiles,
                error=f"Pipeline stopped: authorization denied at step '{step.step_id}'"
            )

        except PipelineExecutionError as e:
            step_results.append(StepResult(
                step_id=step.step_id,
                success=False,
                error=str(e),
                elapsed_s=time.monotonic() - step_start,
            ))
            if on_step_done is not None:
                on_step_done(step_results[-1])

            return PipelineResult(
                success=False,
                step_results=step_results,
                step_profiles=step_profiles,
                error=f"Pipeline stopped at step '{step.step_id}': {e}"
            )

    return PipelineResult(success=True, step_results=step_results, step_profiles=step_profiles)


def _collect_step_profile(step_id: str, code: CodeObject, menai: Menai) -> StepProfile:
    """
    Collect the opcode histogram and instruction/call trace for one Menai step.

    Called immediately after the step's execute() call, while the VM's
    profiling counters and trace arrays still hold that step's data.  The raw
    trace arrays are resolved against the step's code object.
    """
    opcode_counts = menai.vm.get_profile_data()
    instr_counts, call_counts = menai.vm.get_trace_data()
    trace = resolve_trace(code, instr_counts, call_counts)
    return StepProfile(step_id=step_id, opcode_counts=opcode_counts, trace=trace)
