from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram, ReferenceImplementation
from menai_benchmark.suites.sudoku_list.suite import (
    PUZZLES,
    _ITERATIONS,
)
from menai_benchmark.suites.sudoku_mrv import reference_solver

# The puzzles and iteration counts are shared with the sudoku_list and
# sudoku_vector suites so all three measure exactly the same work: only the
# search strategy differs.


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
        '(let ((sudoku (import "sudoku-mrv")))'
        ' (let ((solve-fn (:: sudoku solve)))'
        f' (solve-fn {board_expr})))'
    )


class Suite(BenchmarkSuite):
    """Benchmark suite for a sudoku solver that fills the most constrained cell first."""

    name = "sudoku_mrv"
    description = "Solve sudoku puzzles of varying difficulty, filling the most constrained cell first."

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

    def reference_implementations(self) -> list[ReferenceImplementation]:
        """
        Return a Python solver doing the same search, for relative timing.

        Its preparation step asserts that it visits the same number of search
        nodes as the Menai solver, so the two columns compare equal work rather
        than two different searches.

        One asymmetry survives that assertion and is not corrected for: the Menai
        module has no import-time state, so it rebuilds its 512-entry popcount
        table and its board literal on every execution, inside the timed section.
        CPython builds the table once when the module is imported, outside it.
        Measured on this machine that fixed cost is about 28us for the table and
        12us for the board literal, so roughly 40us, which is large next to the
        Easy and Medium cases and negligible next to Expert.  Subtract it before
        reading the ratio on the short cases.
        """
        return [
            ReferenceImplementation(
                name="python",
                prepare=reference_solver.prepare,
                run=reference_solver.solve,
            ),
        ]
