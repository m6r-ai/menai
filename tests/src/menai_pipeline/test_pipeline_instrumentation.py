"""Tests for VM instrumentation of pipeline steps.

When a pipeline is executed with instrumentation enabled, each Menai step
contributes a StepProfile holding that step's opcode histogram and resolved
instruction/call trace.  Tool steps execute no VM code and so contribute no
profile.  Instrumentation must not change the pipeline's results.
"""

from pathlib import Path

from menai_pipeline.pipeline_engine import execute_pipeline
from menai_pipeline.pipeline_parser import parse_pipeline


def _single_menai_step_pipeline() -> object:
    """Build a one-step pipeline whose Menai step needs no tools or inputs."""
    pipeline = parse_pipeline({
        "steps": [
            {
                "id": "compute",
                "tool": "menai",
                "inputs": {},
                "expression": (
                    '(dict "result" '
                    '(integer->string (fold-list integer+ 0 (list 1 2 3 4 5))))'
                ),
                "outputs": {},
            },
        ],
    })
    pipeline.directory = Path.cwd()
    return pipeline


class TestInstrumentationDisabled:
    """Without instrumentation no profiles are collected."""

    def test_no_profiles_collected(self):
        result = execute_pipeline(_single_menai_step_pipeline())
        assert result.success
        assert result.step_profiles == []


class TestInstrumentationEnabled:
    """With instrumentation each Menai step contributes a profile."""

    def test_one_profile_per_menai_step(self):
        result = execute_pipeline(_single_menai_step_pipeline(), instrument=True)
        assert result.success
        assert [p.step_id for p in result.step_profiles] == ["compute"]

    def test_profile_has_opcode_counts(self):
        result = execute_pipeline(_single_menai_step_pipeline(), instrument=True)
        profile = result.step_profiles[0]
        assert profile.opcode_counts.get("__total__", 0) > 0

    def test_profile_has_resolved_trace(self):
        result = execute_pipeline(_single_menai_step_pipeline(), instrument=True)
        profile = result.step_profiles[0]
        assert profile.trace is not None
        assert profile.trace.total_instructions() > 0

    def test_instrumentation_does_not_change_results(self):
        plain = execute_pipeline(_single_menai_step_pipeline())
        instrumented = execute_pipeline(_single_menai_step_pipeline(), instrument=True)
        assert plain.step_results[0].value == instrumented.step_results[0].value


class TestToolStepsHaveNoProfile:
    """Tool steps execute no VM code and contribute no profile."""

    def test_only_menai_step_profiled(self):
        pipeline = parse_pipeline({
            "steps": [
                {
                    "id": "greet",
                    "tool": "console",
                    "operation": "write_stdout",
                    "content": "hello",
                },
                {
                    "id": "compute",
                    "tool": "menai",
                    "inputs": {},
                    "expression": '(dict "result" "hello")',
                    "outputs": {},
                },
            ],
        })
        pipeline.directory = Path.cwd()
        result = execute_pipeline(pipeline, instrument=True)
        assert [p.step_id for p in result.step_profiles] == ["compute"]
