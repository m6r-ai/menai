from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_SCRAMBLES: list[tuple[str, list[str]]] = [
    ("1-move", ["R"]),
    ("2-move", ["R", "U"]),
    ("3-move", ["R", "U", "F"]),
    ("4-move", ["R", "U", "R'", "D"]),
    ("5-move", ["R", "U", "R'", "D", "F"]),
    ("6-move", ["R", "U", "R'", "D", "F", "R"]),
    ("7-move", ["R", "U", "R'", "D", "F", "R", "U"]),
]


def _expr(scramble_moves: list[str]) -> str:
    """Build the solver-driving expression for a scramble sequence."""
    moves_literal = "(list " + " ".join(f'"{m}"' for m in scramble_moves) + ")"
    return (
        '(let ((rubiks (import "rubiks_cube")))'
        '  (let ((solved-cube-fn (:: rubiks solved-cube))'
        '        (apply-moves-fn (:: rubiks apply-moves))'
        '        (ida-star-fn (:: rubiks ida-star)))'
        f'    (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_literal})))'
        '      (ida-star-fn scrambled 20))))'
    )


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai Rubik's cube IDA* solver."""

    name = "rubiks_list"
    description = "Solve scrambled Rubik's cubes of increasing depth using IDA*."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per scramble sequence."""
        return [
            BenchmarkCase(name=name, input=moves, iterations=3)
            for name, moves in _SCRAMBLES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the solver expression, built from the scramble sequence."""
        return MenaiProgram(expression=_expr)
