"""Tests for standard library location and module search path composition.

Tests cover:
- Standard library location through importlib.resources
- Reading a standard library module's source
- Composition of the default module search path
- MENAI_PATH environment variable handling
- Precedence and shadowing of application libraries over the standard library
"""

from pathlib import Path

import pytest

from menai import Menai


class TestStdlibLocation:
    """Test that the standard library is located and readable."""

    def test_stdlib_path_is_a_directory(self):
        """The standard library path is an existing directory."""
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert stdlib_dir.is_dir()

    def test_stdlib_path_contains_modules(self):
        """The standard library directory contains .menai modules."""
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert sorted(stdlib_dir.glob("*.menai"))

    def test_stdlib_source_returns_module_text(self):
        """The source of a standard library module is its text."""
        source = Menai.stdlib_source("json-decode")
        assert "(export decode)" in source

    def test_stdlib_source_matches_file_on_disk(self):
        """The source read through importlib.resources matches the file on disk."""
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        on_disk = (stdlib_dir / "json-decode.menai").read_text()
        assert Menai.stdlib_source("json-decode") == on_disk


class TestBuildModulePath:
    """Test composition of the default module search path."""

    def test_stdlib_is_last(self):
        """The standard library is the last entry on the composed path."""
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert Menai.build_module_path("/some/source/dir")[-1] == str(stdlib_dir)

    def test_source_dir_precedes_stdlib(self):
        """The source file's directory precedes the standard library."""
        path = Menai.build_module_path("/some/source/dir")
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert path.index("/some/source/dir") < path.index(str(stdlib_dir))

    def test_no_source_dir_yields_only_stdlib(self, monkeypatch):
        """With no source directory and no MENAI_PATH, only the stdlib remains."""
        monkeypatch.delenv("MENAI_PATH", raising=False)
        stdlib_dir = Menai.stdlib_path()
        assert stdlib_dir is not None
        assert Menai.build_module_path() == [str(stdlib_dir)]

    def test_menai_path_precedes_source_dir(self, monkeypatch):
        """MENAI_PATH directories precede the source file's directory."""
        monkeypatch.setenv("MENAI_PATH", "/app/one:/app/two")
        path = Menai.build_module_path("/some/source/dir")
        assert path[0] == "/app/one"
        assert path[1] == "/app/two"
        assert path[2] == "/some/source/dir"

    def test_empty_menai_path_segments_are_ignored(self, monkeypatch):
        """Empty MENAI_PATH segments are omitted from the path."""
        monkeypatch.setenv("MENAI_PATH", "/app/one::/app/two:")
        path = Menai.build_module_path()
        assert "/app/one" in path
        assert "/app/two" in path
        assert "" not in path

    def test_duplicate_directories_are_removed(self, monkeypatch):
        """A directory appearing twice is kept only once, at its first position."""
        monkeypatch.setenv("MENAI_PATH", "/app/one")
        path = Menai.build_module_path("/app/one")
        assert path.count("/app/one") == 1
        assert path[0] == "/app/one"


class TestShadowing:
    """Test that an application library shadows a standard library module."""

    def test_application_library_shadows_stdlib(self, tmp_path, monkeypatch):
        """An application library module of the same name overrides the stdlib one."""
        shadow = tmp_path / "json-decode.menai"
        shadow.write_text("""
(let ((decode (lambda (s) "shadowed")))
  (export decode))
""")

        monkeypatch.setenv("MENAI_PATH", str(tmp_path))
        menai = Menai(module_path=Menai.build_module_path())

        result = menai.evaluate('''
(let ((j (import "json-decode")))
  ((:: j decode) "{}"))
''')
        assert result == "shadowed"

    def test_stdlib_used_when_not_shadowed(self, tmp_path, monkeypatch):
        """The standard library module is used when no application library shadows it."""
        monkeypatch.setenv("MENAI_PATH", str(tmp_path))
        menai = Menai(module_path=Menai.build_module_path())

        result = menai.evaluate('''
(let ((j (import "json-decode")))
  ((:: j decode) "{\\"a\\": 1}"))
''')
        assert result == {"a": 1}


class TestExplicitModulePathIsVerbatim:
    """Test that an explicit module_path is used without composition."""

    def test_explicit_path_is_not_composed(self):
        """An explicit module_path is used verbatim, with no stdlib appended."""
        menai = Menai(module_path=["/only/this"])
        assert menai.module_path() == ["/only/this"]
