"""
Tests for the Menai parenthesis balance checker.

The checker validates parenthesis balance and detects bindings that are
missing a close paren.  A binding missing its close paren can leave the
file's overall parenthesis count even, so balance alone is not sufficient:
the checker must also report the structural error and not claim the file
is valid.
"""

from pathlib import Path

from menai_check.check import ParenChecker


def _check(tmp_path: Path, source: str) -> ParenChecker:
    """
    Write *source* to a temporary .menai file and run the checker on it.

    Returns the checker so tests can inspect its errors and summary.
    """
    path = tmp_path / "sample.menai"
    path.write_text(source, encoding="utf-8")

    checker = ParenChecker(str(path))
    checker.check_balance()
    return checker


def _binding_errors(checker: ParenChecker) -> list[str]:
    """Return the messages of all binding_not_closed errors."""
    return [e.message for e in checker.errors if e.error_type == "binding_not_closed"]


class TestBalancedFiles:
    """A well-formed file reports no errors."""

    def test_simple_balanced_file(self, tmp_path: Path) -> None:
        """A simple let with two valid bindings has no errors."""
        checker = _check(tmp_path, "(let ((x 5) (y 10)) (integer+ x y))")

        assert checker.errors == []
        assert "✓ Parentheses balanced" in checker.format_summary()

    def test_binding_with_atom_value(self, tmp_path: Path) -> None:
        """A binding whose value is an atom is valid and not flagged."""
        checker = _check(tmp_path, "(let ((x 5)) x)")

        assert checker.errors == []

    def test_binding_with_call_value(self, tmp_path: Path) -> None:
        """A binding whose value is a call is valid and not flagged."""
        checker = _check(tmp_path, "(let ((x (integer+ 1 2))) x)")

        assert checker.errors == []


class TestBodyReadAsBinding:
    """
    A bindings list missing its close paren can leave the file balanced.

    The form's body is then misread as a binding.  The checker must report
    the structural error and must not claim the file is balanced and valid.
    """

    def test_balanced_but_body_read_as_binding(self, tmp_path: Path) -> None:
        """Balanced parens with a missing bindings-list close is reported."""
        source = (
            "(letrec\n"
            "  ((a 1)\n"
            "   (b 2)\n"
            "  (dict \"x\" x)))\n"
        )
        checker = _check(tmp_path, source)

        assert checker.total_opens == checker.total_closes
        errors = _binding_errors(checker)
        assert len(errors) == 1
        assert "binding 'dict'" in errors[0]

    def test_summary_reports_structure_invalid_not_unbalanced(self, tmp_path: Path) -> None:
        """The summary distinguishes balanced-but-invalid from unbalanced."""
        source = (
            "(letrec\n"
            "  ((a 1)\n"
            "   (b 2)\n"
            "  (dict \"x\" x)))\n"
        )
        checker = _check(tmp_path, source)
        summary = checker.format_summary()

        assert "balanced but structure invalid" in summary
        assert "UNBALANCED" not in summary

    def test_check_balance_returns_false(self, tmp_path: Path) -> None:
        """A balanced-but-invalid file fails the balance check."""
        source = (
            "(letrec\n"
            "  ((a 1)\n"
            "   (b 2)\n"
            "  (dict \"x\" x)))\n"
        )
        path = tmp_path / "sample.menai"
        path.write_text(source, encoding="utf-8")

        checker = ParenChecker(str(path))
        assert checker.check_balance() is False


class TestParenFormExcess:
    """A binding whose missing close makes a paren form an extra child."""

    def test_extra_paren_form_child(self, tmp_path: Path) -> None:
        """A sibling binding read as a child of the previous binding is reported."""
        source = (
            "(let ((x 5)\n"
            "      (y (lambda (n)\n"
            "           (if (integer=? n 0)\n"
            "               0\n"
            "               (y (integer- n 1))))\n"
            "      (z 10))\n"
            "  (integer+ x z))\n"
        )
        checker = _check(tmp_path, source)

        errors = _binding_errors(checker)
        assert errors
        assert any("binding 'y'" in msg for msg in errors)


class TestUnbalancedFiles:
    """Genuinely unbalanced files keep their existing behaviour."""

    def test_missing_close(self, tmp_path: Path) -> None:
        """A file missing a close paren is reported as unbalanced."""
        checker = _check(tmp_path, "(let ((x 5) (y 10)) (integer+ x y)")

        assert checker.errors
        summary = checker.format_summary()
        assert "UNBALANCED" in summary
        assert "Missing 1 closing parenthesis" in summary

    def test_extra_close(self, tmp_path: Path) -> None:
        """A file with an extra close paren is reported as unbalanced."""
        checker = _check(tmp_path, "(let ((x 5)) x))")

        assert checker.errors
        summary = checker.format_summary()
        assert "UNBALANCED" in summary
        assert "Extra 1 closing parenthesis" in summary
