from __future__ import annotations

from typing import Any

from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai import Menai

_SCRAMBLES: list[tuple[str, list[str]]] = [
    ("1-move", ["R"]),
    ("2-move", ["R", "U"]),
    ("3-move", ["R", "U", "F"]),
    ("4-move", ["R", "U", "R'", "D"]),
    ("5-move", ["R", "U", "R'", "D", "F"]),
    ("6-move", ["R", "U", "R'", "D", "F", "R"]),
    ("7-move", ["R", "U", "R'", "D", "F", "R", "U"]),
]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai Rubik's cube IDA* solver."""

    name = "rubiks_cube"
    description = "Solve scrambled Rubik's cubes of increasing depth using IDA*."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per scramble sequence."""
        return [
            BenchmarkCase(name=name, input=moves, iterations=3)
            for name, moves in _SCRAMBLES
        ]

    def implementation(self, menai: Menai) -> Implementation:
        """Return the Menai Rubik's cube solver implementation."""
        def prepare_menai(scramble_moves: list[str]) -> Any:
            """Build the expression string and compile to bytecode (untimed)."""
            moves_literal = "(list " + " ".join(f'"{m}"' for m in scramble_moves) + ")"
            expr = (
                '(let ((rubiks (import "rubiks_cube")))'
                '  (let ((solved-cube-fn (:: rubiks solved-cube))'
                '        (apply-moves-fn (:: rubiks apply-moves))'
                '        (ida-star-fn (:: rubiks ida-star)))'
                f'    (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_literal})))'
                '      (ida-star-fn scrambled 20))))'
            )
            return menai.compile(expr)

        def run_menai(code: Any) -> Any:
            """Execute pre-compiled bytecode (timed)."""
            return menai.execute_raw(code)

        return Implementation(run=run_menai, prepare=prepare_menai)
