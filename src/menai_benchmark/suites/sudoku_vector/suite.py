from __future__ import annotations

from pathlib import Path
from typing import Any

from menai import Menai

from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai_benchmark.suites.sudoku.suite import (
    PUZZLES,
    _ITERATIONS,
    _is_valid_board,
    _solve_python_functional,
    _solve_python_idiomatic,
)

_SUITE_DIR = Path(__file__).resolve().parent

# The puzzles, iteration counts, Python reference solvers, and validator are
# shared with the list-based sudoku suite so the two suites measure exactly
# the same work: only the board representation differs.


def _board_to_menai(flat: list[int]) -> str:
    """Convert a flat 81-element board into a Menai nested-vector literal."""
    rows = []
    for r in range(9):
        cells = " ".join(str(flat[r * 9 + c]) for c in range(9))
        rows.append(f"(vector {cells})")

    return "(vector\n  " + "\n  ".join(rows) + ")"


class Suite(BenchmarkSuite):
    """Benchmark suite for a sudoku solver whose board is a vector of row vectors."""

    name = "sudoku_vector"
    description = "Solve sudoku puzzles of varying difficulty using a vector board."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per puzzle, with iteration counts scaled by difficulty."""
        return [
            BenchmarkCase(
                name=label,
                input=flat,
                iterations=_ITERATIONS[difficulty],
            )
            for label, difficulty, flat in PUZZLES
        ]

    def implementations(self, menai: Menai) -> list[Implementation]:
        """Return the vector-board Menai solver plus the shared Python references."""
        def prepare_menai(flat: list[int]) -> Any:
            """Build expression string and compile to bytecode (untimed)."""
            board_expr = _board_to_menai(flat)
            expr = (
                '(let ((sudoku (import "sudoku-vector-solver")))'
                ' (let ((solve-fn (dict-get sudoku "solve")))'
                f' (solve-fn {board_expr})))'
            )
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        def run_python_idiomatic(flat: list[int]) -> list[list[int]]:
            """Solve the puzzle using mutable backtracking."""
            return _solve_python_idiomatic(flat)

        def run_python_functional(flat: list[int]) -> list[list[int]]:
            """Solve the puzzle using pure-functional backtracking."""
            return _solve_python_functional(flat)

        return [
            Implementation(name="Menai (vector)", run=run_menai, prepare=prepare_menai),
            Implementation(name="Python (idiomatic)", run=run_python_idiomatic),
            Implementation(name="Python (functional)", run=run_python_functional),
        ]

    def results_equal(self, a: Any, b: Any) -> bool:
        """Return True if both *a* and *b* are independently valid solved sudoku boards."""
        return _is_valid_board(a) and _is_valid_board(b)
