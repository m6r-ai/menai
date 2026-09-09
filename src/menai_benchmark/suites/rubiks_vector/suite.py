
from __future__ import annotations

from pathlib import Path
from typing import Any

from menai_benchmark import BenchmarkCase, BenchmarkSuite, Implementation
from menai import Menai
from menai_benchmark.suites.rubiks_cube.suite import (
    _SCRAMBLES,
    _apply_moves_idiomatic,
    _apply_moves_functional,
    _cube_solved_idiomatic,
    _ida_star_functional,
    _ida_star_idiomatic,
    _solved_cube_functional,
    _solved_cube_idiomatic,
)

_SUITE_DIR = Path(__file__).resolve().parent

# The scramble sequences, iteration counts, Python reference solvers, and
# validation helpers are shared with the list-based rubiks_cube suite so the
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

    def implementations(self, menai: Menai) -> list[Implementation]:
        """Return the vector-face Menai solver plus the shared Python references."""
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
            code = menai.compile(expr)
            return (scramble_moves, code)

        def run_menai(prepared: tuple[list[str], Any]) -> tuple[list[str], list[str]]:
            """Execute pre-compiled bytecode and extract the solution (timed)."""
            scramble_moves, code = prepared
            raw = menai.execute_raw(code).to_python()
            if not isinstance(raw, dict):
                raise ValueError(f"Unexpected Menai result type: {type(raw)}")

            found = raw.get("found", False)
            value = raw.get("value")
            if not found or not isinstance(value, (list, tuple)):
                raise ValueError(f"Menai solver found no solution for {scramble_moves}")

            return (scramble_moves, list(value))

        def run_python_idiomatic(scramble_moves: list[str]) -> tuple[list[str], list[str]]:
            """Scramble the cube and solve it using the idiomatic Python IDA* solver."""
            scrambled = _apply_moves_idiomatic(_solved_cube_idiomatic(), scramble_moves)
            solution = _ida_star_idiomatic(scrambled, 20)
            if solution is None:
                raise ValueError(f"Idiomatic solver found no solution for {scramble_moves}")

            return (scramble_moves, solution)

        def run_python_functional(scramble_moves: list[str]) -> tuple[list[str], list[str]]:
            """Scramble the cube and solve it using the functional Python IDA* solver."""
            scrambled = _apply_moves_functional(_solved_cube_functional(), scramble_moves)
            solution = _ida_star_functional(scrambled, 20)
            if solution is None:
                raise ValueError(f"Functional solver found no solution for {scramble_moves}")

            return (scramble_moves, solution)

        return [
            Implementation(name="Menai (vector)", run=run_menai, prepare=prepare_menai),
            Implementation(name="Python (idiomatic)", run=run_python_idiomatic),
            Implementation(name="Python (functional)", run=run_python_functional),
        ]

    def results_equal(self, a: Any, b: Any) -> bool:
        """
        Return True if both results solve the same scramble.

        Each result is a ``(scramble_moves, solution_moves)`` tuple.  The
        scramble is applied to a solved cube and then the solution from each
        result is applied independently; both must yield a solved cube.
        """
        scramble_a, solution_a = a
        scramble_b, solution_b = b

        cube_a = _apply_moves_idiomatic(_solved_cube_idiomatic(), scramble_a)
        cube_a = _apply_moves_idiomatic(cube_a, solution_a)
        if not _cube_solved_idiomatic(cube_a):
            return False

        cube_b = _apply_moves_idiomatic(_solved_cube_idiomatic(), scramble_b)
        cube_b = _apply_moves_idiomatic(cube_b, solution_b)
        return _cube_solved_idiomatic(cube_b)
