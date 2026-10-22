from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_SIZES = [100, 500, 1000, 2500, 5000, 10000, 25000]
_ITERATIONS = 5
_SUITE_DIR = Path(__file__).resolve().parent

_OPERATIONS = ["mean", "summary"]
_CONTAINERS = ["vector", "list"]

_rng = random.Random(42)
_SAMPLES: dict[int, list[float]] = {
    size: [_rng.uniform(-1000.0, 1000.0) for _ in range(size)] for size in _SIZES
}

_PROGRAMS: dict[tuple[str, str], str] = {
    (container, operation): (_SUITE_DIR / f"{container}-{operation}.menai").read_text(encoding="utf-8").strip()
    for container in _CONTAINERS
    for operation in _OPERATIONS
}


@dataclass(frozen=True)
class _Input:
    """The sample and the program variant a case benchmarks."""

    container: str
    operation: str
    sample: list[float]


def _expr(case_input: _Input) -> str:
    """Wrap the case's sample in its container literal and the benchmarked call."""
    items = " ".join(f"{value!r}" for value in case_input.sample)
    literal = f"{case_input.container} {items}"
    program = _PROGRAMS[(case_input.container, case_input.operation)]

    return f"({program} ({literal}))"


class Suite(BenchmarkSuite):
    """Benchmark suite for the descriptive statistics module."""

    name = "stats"
    description = "Summarise and average a column of random floats, as a vector and as a list."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per container, operation, and sample size."""
        return [
            BenchmarkCase(
                name=f"{container}-{operation}-n={size}",
                input=_Input(container, operation, _SAMPLES[size]),
                iterations=_ITERATIONS,
            )
            for container in _CONTAINERS
            for operation in _OPERATIONS
            for size in _SIZES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the statistics expression, built from the case's sample."""
        return MenaiProgram(expression=_expr)
