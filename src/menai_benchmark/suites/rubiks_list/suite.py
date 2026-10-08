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

_TURN_SUFFIXES: dict[str, str] = {"": "cw", "'": "ccw", "2": "half"}


def move_to_menai(move: str) -> str:
    """Render a standard-notation move (e.g. "R'") as a Menai move value."""
    face, suffix = move[0], move[1:]
    turn = _TURN_SUFFIXES[suffix]
    return f"(move (face '{face.lower()}) (turn '{turn}))"


def _expr(scramble_moves: list[str]) -> str:
    """Build the solver-driving expression for a scramble sequence."""
    moves_literal = "(list " + " ".join(move_to_menai(m) for m in scramble_moves) + ")"
    return (
        '(let ((rubiks (import "rubiks_cube")))'
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
