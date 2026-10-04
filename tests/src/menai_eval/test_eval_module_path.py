"""Tests for the menai-eval module search path construction.

Tests cover:
- Explicit --module-path directories taking precedence
- The standard library being present on the evaluator's path
- The source file's directory being present
"""

from menai import Menai
from menai_eval.eval import build_module_path


class TestEvalModulePath:
    """Test the evaluator's module search path construction."""

    def test_stdlib_is_present(self):
        """The standard library is on the evaluator's path."""
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert str(stdlib_dir) in build_module_path(None, [])

    def test_explicit_directories_come_first(self, tmp_path):
        """Explicit --module-path directories precede the rest of the path."""
        path = build_module_path(None, [str(tmp_path)])
        assert path[0] == str(tmp_path)

    def test_source_file_directory_is_present(self, tmp_path):
        """The source file's directory is on the path."""
        source = tmp_path / "program.menai"
        path = build_module_path(source, [])
        assert str(tmp_path) in path

    def test_duplicate_directories_are_removed(self, tmp_path):
        """A directory supplied both explicitly and as the source dir appears once."""
        source = tmp_path / "program.menai"
        path = build_module_path(source, [str(tmp_path)])
        assert path.count(str(tmp_path)) == 1


class TestEvalImportsStdlib:
    """Test that the evaluator can import a standard library module."""

    def test_evaluates_stdlib_import(self):
        """A program importing a standard library module evaluates."""
        menai = Menai(module_path=build_module_path(None, []))
        result = menai.evaluate('''
(let ((j (import "json-decode")))
  ((:: j decode) "{\\"a\\": 1}"))
''')
        assert result == {"a": 1}
