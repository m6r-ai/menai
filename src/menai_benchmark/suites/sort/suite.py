from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai import Menai

_SIZES = [10, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
_ITERATIONS = 5
_SUITE_DIR = Path(__file__).resolve().parent

_rng = random.Random(42)
_INPUTS: dict[int, list[int]] = {
    size: _rng.sample(range(size * 10), size) for size in _SIZES
}


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

    def implementation(self, menai: Menai) -> Implementation:
        """Return the Menai sort implementation."""
        sort_expr = (_SUITE_DIR / "list-sort.menai").read_text(encoding="utf-8").strip()

        def prepare_menai(lst: list[int]) -> Any:
            """Build the expression string and compile to bytecode (untimed)."""
            items = " ".join(str(n) for n in lst)
            expr = f"({sort_expr} (list {items}))"
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        return Implementation(run=run_menai, prepare=prepare_menai)
