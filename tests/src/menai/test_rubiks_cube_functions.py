"""
Isolated function tests for the Rubik's Cube benchmark.

Each exported function from rubiks_cube.menai is tested independently so
that a compiler bug can be pinpointed to the specific function that is
miscompiled.

The tests use the same scrambles as the benchmark suite and verify
correctness by checking invariants of the cube state and by comparing
against known-good results.
"""

import pytest

from menai import Menai

# Same scrambles used by the benchmark suite.
_SCRAMBLES: list[tuple[str, list[str]]] = [
    ("1-move", ["R"]),
    ("2-move", ["R", "U"]),
    ("3-move", ["R", "U", "F"]),
    ("4-move", ["R", "U", "R'", "D"]),
    ("5-move", ["R", "U", "R'", "D", "F"]),
    ("6-move", ["R", "U", "R'", "D", "F", "R"]),
    ("7-move", ["R", "U", "R'", "D", "F", "R", "U"]),
]

_SUITE_DIR = "src/menai_benchmark/suites/rubiks_cube"
_MODULES_DIR = "menai_modules"


def _menai_with_rubiks() -> Menai:
    """Create a Menai instance with the rubiks_cube module on the path."""
    return Menai(module_path=[_SUITE_DIR, _MODULES_DIR])


def _rubiks_expr(body: str) -> str:
    """Wrap a body expression in an import of the rubiks_cube module."""
    return (
        '(let ((rubiks (import "rubiks_cube")))'
        f'  {body})'
    )


@pytest.fixture
def menai():
    """Menai instance with rubiks_cube module available."""
    return _menai_with_rubiks()


def _moves_literal(moves: list[str]) -> str:
    """Build a Menai list literal from a list of move strings."""
    return "(list " + " ".join(f'"{m}"' for m in moves) + ")"


class TestSolvedCube:
    """solved-cube returns a cube where every face is uniform."""

    def test_solved_cube_face_values(self, menai):
        """Each face should be a list of 9 identical stickers (0-5)."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (list (struct-get cube \'U)'
            '        (struct-get cube \'D)'
            '        (struct-get cube \'F)'
            '        (struct-get cube \'B)'
            '        (struct-get cube \'L)'
            '        (struct-get cube \'R)))'
        )
        result = menai.evaluate(expr)
        assert result == [
            [0] * 9, [1] * 9, [2] * 9, [3] * 9, [4] * 9, [5] * 9,
        ]

    def test_solved_cube_is_solved(self, menai):
        """A freshly created solved cube should pass cube-solved?."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  ((dict-get rubiks "cube-solved?") cube))'
        )
        assert menai.evaluate(expr) is True


class TestMoveU:
    """move-U (via apply-move "U") applies a clockwise U move to the cube."""

    def test_move_u_preserves_d_face(self, menai):
        """The D face should be unchanged by a U move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "U")))'
            '    (struct-get moved \'D)))'
        )
        assert menai.evaluate(expr) == [1] * 9

    def test_move_u_rotates_u_face(self, menai):
        """The U face itself should be rotated CW (still uniform on solved cube)."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "U")))'
            '    (struct-get moved \'U)))'
        )
        assert menai.evaluate(expr) == [0] * 9

    def test_move_u_four_times_identity(self, menai):
        """Applying U four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "U")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "U")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "U")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "U")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestMoveD:
    """move-D (via apply-move "D") applies a clockwise D move to the cube."""

    def test_move_d_preserves_u_face(self, menai):
        """The U face should be unchanged by a D move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "D")))'
            '    (struct-get moved \'U)))'
        )
        assert menai.evaluate(expr) == [0] * 9

    def test_move_d_four_times_identity(self, menai):
        """Applying D four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "D")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "D")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "D")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "D")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestMoveF:
    """move-F (via apply-move "F") applies a clockwise F move to the cube."""

    def test_move_f_preserves_b_face(self, menai):
        """The B face should be unchanged by an F move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "F")))'
            '    (struct-get moved \'B)))'
        )
        assert menai.evaluate(expr) == [3] * 9

    def test_move_f_four_times_identity(self, menai):
        """Applying F four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "F")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "F")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "F")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "F")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestMoveB:
    """move-B (via apply-move "B") applies a clockwise B move to the cube."""

    def test_move_b_preserves_f_face(self, menai):
        """The F face should be unchanged by a B move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "B")))'
            '    (struct-get moved \'F)))'
        )
        assert menai.evaluate(expr) == [2] * 9

    def test_move_b_four_times_identity(self, menai):
        """Applying B four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "B")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "B")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "B")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "B")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestMoveL:
    """move-L (via apply-move "L") applies a clockwise L move to the cube."""

    def test_move_l_preserves_r_face(self, menai):
        """The R face should be unchanged by an L move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "L")))'
            '    (struct-get moved \'R)))'
        )
        assert menai.evaluate(expr) == [5] * 9

    def test_move_l_four_times_identity(self, menai):
        """Applying L four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "L")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "L")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "L")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "L")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestMoveR:
    """move-R (via apply-move "R") applies a clockwise R move to the cube."""

    def test_move_r_preserves_l_face(self, menai):
        """The L face should be unchanged by an R move."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "R")))'
            '    (struct-get moved \'L)))'
        )
        assert menai.evaluate(expr) == [4] * 9

    def test_move_r_four_times_identity(self, menai):
        """Applying R four times returns to the solved state."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((m1 ((dict-get rubiks "apply-move") cube "R")))'
            '    (let ((m2 ((dict-get rubiks "apply-move") m1 "R")))'
            '      (let ((m3 ((dict-get rubiks "apply-move") m2 "R")))'
            '        (let ((m4 ((dict-get rubiks "apply-move") m3 "R")))'
            '          ((dict-get rubiks "cube-solved?") m4))))))'
        )
        assert menai.evaluate(expr) is True


class TestApplyMove:
    """apply-move dispatches move names including prime and double variants."""

    @pytest.mark.parametrize("move", ["U", "D", "F", "B", "L", "R"])
    def test_prime_is_triple(self, menai, move):
        """X' should undo X, returning to the solved state."""
        expr = _rubiks_expr(
            f'(let ((cube ((dict-get rubiks "solved-cube"))))'
            f'  (let ((scrambled ((dict-get rubiks "apply-move") cube "{move}")))'
            f'    (let ((prime ((dict-get rubiks "apply-move") scrambled "{move}\'")))'
            f'      ((dict-get rubiks "cube-solved?") prime))))'
        )
        assert menai.evaluate(expr) is True

    @pytest.mark.parametrize("move", ["U", "D", "F", "B", "L", "R"])
    def test_double_is_pair(self, menai, move):
        """X2 should equal two applications of X, and X2 twice returns to solved."""
        expr = _rubiks_expr(
            f'(let ((cube ((dict-get rubiks "solved-cube"))))'
            f'  (let ((d1 ((dict-get rubiks "apply-move") cube "{move}2")))'
            f'    (let ((d2 ((dict-get rubiks "apply-move") d1 "{move}2")))'
            f'      ((dict-get rubiks "cube-solved?") d2))))'
        )
        assert menai.evaluate(expr) is True

    def test_unknown_move_returns_cube_unchanged(self, menai):
        """An unrecognized move string should return the cube unchanged."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "XYZ")))'
            '    ((dict-get rubiks "cube-solved?") moved)))'
        )
        assert menai.evaluate(expr) is True


class TestApplyMoves:
    """apply-moves applies a sequence of moves to a cube."""

    def test_empty_moves_returns_cube(self, menai):
        """Applying an empty move list returns the cube unchanged."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-moves") cube (list))))'
            '    ((dict-get rubiks "cube-solved?") moved)))'
        )
        assert menai.evaluate(expr) is True

    @pytest.mark.parametrize("name,moves", _SCRAMBLES)
    def test_scramble_not_solved(self, menai, name, moves):
        """After scrambling, the cube should not be in the solved state."""
        moves_lit = _moves_literal(moves)
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            f'  (let ((scrambled ((dict-get rubiks "apply-moves") cube {moves_lit})))'
            '    ((dict-get rubiks "cube-solved?") scrambled)))'
        )
        assert menai.evaluate(expr) is False


class TestCubeSolved:
    """cube-solved? checks whether all faces are uniform."""

    def test_solved_cube_is_solved(self, menai):
        """A solved cube should be detected as solved."""
        expr = _rubiks_expr(
            '((dict-get rubiks "cube-solved?") ((dict-get rubiks "solved-cube")))'
        )
        assert menai.evaluate(expr) is True

    def test_scrambled_cube_not_solved(self, menai):
        """A scrambled cube should not be detected as solved."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "R")))'
            '    ((dict-get rubiks "cube-solved?") moved)))'
        )
        assert menai.evaluate(expr) is False


class TestHeuristic:
    """heuristic returns a lower-bound estimate of moves needed."""

    def test_solved_cube_heuristic_zero(self, menai):
        """A solved cube has zero mismatched stickers."""
        expr = _rubiks_expr(
            '((dict-get rubiks "heuristic") ((dict-get rubiks "solved-cube")))'
        )
        assert menai.evaluate(expr) == 0

    def test_single_move_heuristic_positive(self, menai):
        """After one move, the heuristic should be positive."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((moved ((dict-get rubiks "apply-move") cube "R")))'
            '    ((dict-get rubiks "heuristic") moved)))'
        )
        result = menai.evaluate(expr)
        assert isinstance(result, int)
        assert result > 0


class TestIdaStar:
    """ida-star runs the full IDA* search loop."""

    def test_solved_cube_found_immediately(self, menai):
        """A solved cube should be found immediately by ida-star."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((result ((dict-get rubiks "ida-star") cube 20)))'
            '    (struct-get result \'found)))'
        )
        assert menai.evaluate(expr) is True

    def test_solved_cube_empty_solution(self, menai):
        """A solved cube should have an empty solution path."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((result ((dict-get rubiks "ida-star") cube 20)))'
            '    (struct-get result \'value)))'
        )
        assert menai.evaluate(expr) == []

    @pytest.mark.parametrize("name,moves", _SCRAMBLES)
    def test_solves_scrambled_cube(self, menai, name, moves):
        """ida-star should find a solution for each scrambled cube."""
        moves_lit = _moves_literal(moves)
        expr = _rubiks_expr(
            '(let ((solved-cube-fn (dict-get rubiks "solved-cube"))'
            '      (apply-moves-fn (dict-get rubiks "apply-moves"))'
            '      (ida-star-fn (dict-get rubiks "ida-star")))'
            f'  (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_lit})))'
            '    (let ((result (ida-star-fn scrambled 20)))'
            '      (struct-get result \'found))))'
        )
        assert menai.evaluate(expr) is True

    @pytest.mark.parametrize("name,moves", _SCRAMBLES)
    def test_solution_actually_solves(self, menai, name, moves):
        """The solution found by ida-star should actually solve the cube."""
        moves_lit = _moves_literal(moves)
        expr = _rubiks_expr(
            '(let ((solved-cube-fn (dict-get rubiks "solved-cube"))'
            '      (apply-moves-fn (dict-get rubiks "apply-moves"))'
            '      (ida-star-fn (dict-get rubiks "ida-star"))'
            '      (cube-solved-fn (dict-get rubiks "cube-solved?")))'
            f'  (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_lit})))'
            '    (let ((result (ida-star-fn scrambled 20)))'
            '      (let ((solution (struct-get result \'value)))'
            '        (let ((solved (apply-moves-fn scrambled solution)))'
            '          (cube-solved-fn solved))))))'
        )
        assert menai.evaluate(expr) is True


class TestPrintCube:
    """print-cube produces a string representation of the cube."""

    def test_solved_cube_print(self, menai):
        """Printing a solved cube should produce a non-empty string with all colours."""
        expr = _rubiks_expr(
            '((dict-get rubiks "print-cube") ((dict-get rubiks "solved-cube")))'
        )
        result = menai.evaluate(expr)
        assert isinstance(result, str)
        assert len(result) > 0
        for letter in "WYGBOR":
            assert letter in result


class TestFormatSolution:
    """format-solution formats a search result into a human-readable string."""

    def test_format_found(self, menai):
        """A found result should be formatted with the solution moves."""
        expr = _rubiks_expr(
            '(let ((solved-cube-fn (dict-get rubiks "solved-cube"))'
            '      (apply-moves-fn (dict-get rubiks "apply-moves"))'
            '      (ida-star-fn (dict-get rubiks "ida-star"))'
            '      (format-fn (dict-get rubiks "format-solution")))'
            '  (let ((scrambled (apply-moves-fn (solved-cube-fn) (list "R"))))'
            '    (let ((result (ida-star-fn scrambled 20)))'
            '      (format-fn result))))'
        )
        result = menai.evaluate(expr)
        assert isinstance(result, str)
        assert "Solution found" in result


class TestMakeCube:
    """make-cube constructs a cube from six face lists."""

    def test_make_cube_equals_solved(self, menai):
        """make-cube with uniform faces should equal solved-cube's faces."""
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "make-cube")'
            '  (list 0 0 0 0 0 0 0 0 0)'
            '  (list 1 1 1 1 1 1 1 1 1)'
            '  (list 2 2 2 2 2 2 2 2 2)'
            '  (list 3 3 3 3 3 3 3 3 3)'
            '  (list 4 4 4 4 4 4 4 4 4)'
            '  (list 5 5 5 5 5 5 5 5 5))))'
            '  ((dict-get rubiks "cube-solved?") cube))'
        )
        assert menai.evaluate(expr) is True


class TestMoveCombinations:
    """Combinations of moves that should return to solved state."""

    @pytest.mark.parametrize("move", ["U", "D", "F", "B", "L", "R"])
    def test_move_then_inverse_solves(self, menai, move):
        """Applying a move then its inverse should return to solved state."""
        inverse = move + "'" if not move.endswith("'") else move[:-1]
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            f'  (let ((m1 ((dict-get rubiks "apply-move") cube "{move}")))'
            f'    (let ((m2 ((dict-get rubiks "apply-move") m1 "{inverse}")))'
            '      ((dict-get rubiks "cube-solved?") m2))))'
        )
        assert menai.evaluate(expr) is True

    def test_u_d_commute(self, menai):
        """
        U and D operate on opposite faces and should commute.
        Applying U then D should give the same U-face as D then U.
        """
        expr = _rubiks_expr(
            '(let ((cube ((dict-get rubiks "solved-cube"))))'
            '  (let ((ud ((dict-get rubiks "apply-moves") cube (list "U" "D"))))'
            '    (let ((du ((dict-get rubiks "apply-moves") cube (list "D" "U"))))'
            '      (list (struct-get ud \'U) (struct-get du \'U)))))'
        )
        result = menai.evaluate(expr)
        assert result[0] == result[1]
