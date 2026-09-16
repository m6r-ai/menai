from pathlib import Path
from typing import Any

from menai import Menai
from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai_benchmark.suites.rubiks_cube.suite import _SCRAMBLES

_SUITE_DIR = Path(__file__).resolve().parent

# The scramble sequences are shared with the list-based rubiks_cube suite so the
# two suites measure exactly the same work: only the face representation
# differs.


def _moves_to_menai(moves: list[str]) -> str:
    """Convert a list of move names into a Menai list literal."""
    return "(list " + " ".join(f'"{m}"' for m in moves) + ")"


class Suite(BenchmarkSuite):
    """Benchmark suite for a Rubik's cube IDA* solver whose faces are vectors."""

    name = "rubiks_vector"
    description = "Solve scrambled Rubik's cubes of increasing depth using IDA* on a vector cube."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per scramble sequence."""
        return [
            BenchmarkCase(name=name, input=moves, iterations=3)
            for name, moves in _SCRAMBLES
        ]

    def implementation(self, menai: Menai) -> Implementation:
        """Return the vector-face Menai solver implementation."""
        def prepare_menai(scramble_moves: list[str]) -> Any:
            """Build the expression string and compile to bytecode (untimed)."""
            moves_literal = _moves_to_menai(scramble_moves)
            expr = (
                '(let ((rubiks (import "rubiks-cube-vector")))'
                '  (let ((solved-cube-fn (dict-get rubiks "solved-cube"))'
                '        (apply-moves-fn (dict-get rubiks "apply-moves"))'
                '        (ida-star-fn (dict-get rubiks "ida-star")))'
                f'    (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_literal})))'
                '      (ida-star-fn scrambled 20))))'
            )
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        return Implementation(run=run_menai, prepare=prepare_menai)
