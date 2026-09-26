from __future__ import annotations

from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram
from menai_benchmark.suites.sudoku_list.suite import (
    PUZZLES,
    _ITERATIONS,
)

_SUITE_DIR = Path(__file__).resolve().parent

# The puzzles and iteration counts are shared with the list-based sudoku_list suite
# so the two suites measure exactly the same work: only the board
# representation differs.


def _board_to_menai(flat: list[int]) -> str:
    """Convert a flat 81-element board into a Menai nested-vector literal."""
    rows = []
    for r in range(9):
        cells = " ".join(str(flat[r * 9 + c]) for c in range(9))
        rows.append(f"(vector {cells})")

    return "(vector\n  " + "\n  ".join(rows) + ")"


def _expr(flat: list[int]) -> str:
    """Build the solver-driving expression for a sudoku board."""
    board_expr = _board_to_menai(flat)
    return (
        '(let ((sudoku (import "sudoku-vector-solver")))'
        ' (let ((solve-fn (:: sudoku solve)))'
        f' (solve-fn {board_expr})))'
    )


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

    def menai_program(self) -> MenaiProgram:
        """Return the solver expression, built from the case input board."""
        return MenaiProgram(expression=_expr)
