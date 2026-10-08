from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram
from menai_benchmark.suites.rubiks_list.suite import _SCRAMBLES, move_to_menai

_SUITE_DIR = Path(__file__).resolve().parent

# The scramble sequences are shared with the list-based rubiks_list suite so the
# two suites measure exactly the same work: only the face representation
# differs.


def _expr(scramble_moves: list[str]) -> str:
    """Build the solver-driving expression for a scramble sequence."""
    moves_literal = "(list " + " ".join(move_to_menai(m) for m in scramble_moves) + ")"
    return (
        '(let ((rubiks (import "rubiks-cube-vector")))'
        '  (let ((move (:: rubiks move))'
        '        (face (:: rubiks face))'
        '        (turn (:: rubiks turn))'
        '        (solved-cube-fn (:: rubiks solved-cube))'
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
