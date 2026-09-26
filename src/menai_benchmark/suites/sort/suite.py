from __future__ import annotations

import random
from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_SIZES = [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
_ITERATIONS = 5
_SUITE_DIR = Path(__file__).resolve().parent

_rng = random.Random(42)
_INPUTS: dict[int, list[int]] = {
    size: _rng.sample(range(size * 10), size) for size in _SIZES
}

_SORT_EXPR = (_SUITE_DIR / "list-sort.menai").read_text(encoding="utf-8").strip()


def _expr(lst: list[int]) -> str:
    """Wrap a list of integers in a Menai sort call."""
    items = " ".join(str(n) for n in lst)
    return f"({_SORT_EXPR} (list {items}))"


class Suite(BenchmarkSuite):
    """Benchmark suite for sorting a list of random integers."""

    name = "sort"
    description = "Sort a list of random integers at various sizes."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per input size."""
        return [
            BenchmarkCase(
                name=f"n={size}",
                input=_INPUTS[size],
                iterations=_ITERATIONS,
            )
            for size in _SIZES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the sort expression, built from the case input list."""
        return MenaiProgram(expression=_expr)
