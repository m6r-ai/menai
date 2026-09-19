"""Tests for the console pipeline tool.

Console output must end on a complete line.  When the content does not already
end with a newline, the tool adds one so that the shell prompt does not run on
from the last line of output.  Content that already ends with a newline is
written unchanged, and empty content writes nothing.
"""

import pytest

from menai_pipeline.pipeline_tools import ConsoleTool, PipelineToolError


class TestWriteStdout:
    """write_stdout terminates output on a complete line."""

    def test_adds_newline_when_absent(self, capsys):
        ConsoleTool().execute("write_stdout", {"content": "hello"})
        assert capsys.readouterr().out == "hello\n"

    def test_preserves_existing_newline(self, capsys):
        ConsoleTool().execute("write_stdout", {"content": "hello\n"})
        assert capsys.readouterr().out == "hello\n"

    def test_preserves_multiple_trailing_newlines(self, capsys):
        ConsoleTool().execute("write_stdout", {"content": "hello\n\n"})
        assert capsys.readouterr().out == "hello\n\n"

    def test_empty_content_writes_nothing(self, capsys):
        ConsoleTool().execute("write_stdout", {"content": ""})
        assert capsys.readouterr().out == ""

    def test_rejects_non_string_content(self):
        with pytest.raises(PipelineToolError):
            ConsoleTool().execute("write_stdout", {"content": 42})


class TestWriteStderr:
    """write_stderr terminates output on a complete line."""

    def test_adds_newline_when_absent(self, capsys):
        ConsoleTool().execute("write_stderr", {"content": "hello"})
        assert capsys.readouterr().err == "hello\n"

    def test_preserves_existing_newline(self, capsys):
        ConsoleTool().execute("write_stderr", {"content": "hello\n"})
        assert capsys.readouterr().err == "hello\n"

    def test_empty_content_writes_nothing(self, capsys):
        ConsoleTool().execute("write_stderr", {"content": ""})
        assert capsys.readouterr().err == ""

    def test_rejects_non_string_content(self):
        with pytest.raises(PipelineToolError):
            ConsoleTool().execute("write_stderr", {"content": 42})


class TestUnknownOperation:
    """An unrecognised console operation is an error."""

    def test_unknown_operation(self):
        with pytest.raises(PipelineToolError):
            ConsoleTool().execute("write_nowhere", {"content": "hello"})
