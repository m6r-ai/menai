from __future__ import annotations

from typing import Any

from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai import Menai

PUZZLES: list[tuple[str, str, list[int]]] = [
    ("Easy (36 givens)", "easy", [
        5,3,0, 0,7,0, 0,0,0,
        6,0,0, 1,9,5, 0,0,0,
        0,9,8, 0,0,0, 0,6,0,
        8,0,0, 0,6,0, 0,0,3,
        4,0,0, 8,0,3, 0,0,1,
        7,0,0, 0,2,0, 0,0,6,
        0,6,0, 0,0,0, 2,8,0,
        0,0,0, 4,1,9, 0,0,5,
        0,0,0, 0,8,0, 0,7,9,
    ]),
    ("Medium (30 givens)", "medium", [
        0,0,0, 2,6,0, 7,0,1,
        6,8,0, 0,7,0, 0,9,0,
        1,9,0, 0,0,4, 5,0,0,
        8,2,0, 1,0,0, 0,4,0,
        0,0,4, 6,0,2, 9,0,0,
        0,5,0, 0,0,3, 0,2,8,
        0,0,9, 3,0,0, 0,7,4,
        0,4,0, 0,5,0, 0,3,6,
        7,0,3, 0,1,8, 0,0,0,
    ]),
    ("Hard (25 givens)", "hard", [
        0,0,0, 6,0,0, 4,0,0,
        7,0,0, 0,0,3, 6,0,0,
        0,0,0, 0,9,1, 0,8,0,
        0,0,0, 0,0,0, 0,0,0,
        0,5,0, 1,8,0, 0,0,3,
        0,0,0, 3,0,6, 0,4,5,
        0,4,0, 2,0,0, 0,6,0,
        9,0,3, 0,0,0, 0,0,0,
        0,2,0, 0,0,0, 1,0,0,
    ]),
    ("Expert (23 givens)", "expert", [
        8,0,0, 0,0,0, 0,0,0,
        0,0,3, 6,0,0, 0,0,0,
        0,7,0, 0,9,0, 2,0,0,
        0,5,0, 0,0,7, 0,0,0,
        0,0,0, 0,4,5, 7,0,0,
        0,0,0, 1,0,0, 0,3,0,
        0,0,1, 0,0,0, 0,6,8,
        0,0,8, 5,0,0, 0,1,0,
        0,9,0, 0,0,0, 4,0,0,
    ]),
]

_ITERATIONS: dict[str, int] = {
    "easy": 3,
    "medium": 3,
    "hard": 1,
    "expert": 1,
}


def _board_to_menai(flat: list[int]) -> str:
    """Convert a flat 81-element board into a Menai nested-list literal."""
    rows = []
    for r in range(9):
        cells = " ".join(str(flat[r * 9 + c]) for c in range(9))
        rows.append(f"(list {cells})")

    return "(list\n  " + "\n  ".join(rows) + ")"


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai sudoku solver."""

    name = "sudoku"
    description = "Solve sudoku puzzles of varying difficulty."

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

    def implementation(self, menai: Menai) -> Implementation:
        """Return the Menai sudoku solver implementation."""
        def prepare_menai(flat: list[int]) -> Any:
            """Build expression string and compile to bytecode (untimed)."""
            board_expr = _board_to_menai(flat)
            expr = (
                '(let ((sudoku (import "sudoku-solver")))'
                ' (let ((solve-fn (dict-get sudoku "solve")))'
                f' (solve-fn {board_expr})))'
            )
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        return Implementation(run=run_menai, prepare=prepare_menai)
