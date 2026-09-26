from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram
from menai_benchmark.suites.rubiks_cube.suite import _SCRAMBLES

_SUITE_DIR = Path(__file__).resolve().parent

# The scramble sequences are shared with the list-based rubiks_cube suite so the
# two suites measure exactly the same work: only the face representation
# differs.


def _moves_to_menai(moves: list[str]) -> str:
    """Convert a list of move names into a Menai list literal."""
    return "(list " + " ".join(f'"{m}"' for m in moves) + ")"


def _expr(scramble_moves: list[str]) -> str:
    """Build the solver-driving expression for a scramble sequence."""
    moves_literal = _moves_to_menai(scramble_moves)
    return (
        '(let ((rubiks (import "rubiks-cube-vector")))'
        '  (let ((solved-cube-fn (:: rubiks solved-cube))'
        '        (apply-moves-fn (:: rubiks apply-moves))'
        '        (ida-star-fn (:: rubiks ida-star)))'
        f'    (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_literal})))'
        '      (ida-star-fn scrambled 20))))'
    )


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

    def menai_program(self) -> MenaiProgram:
        """Return the solver expression, built from the scramble sequence."""
        return MenaiProgram(expression=_expr)
