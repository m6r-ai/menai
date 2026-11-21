"""Tests for the menai-format command-line interface.

These tests specify the CLI behaviour: files are formatted in place by default,
several files may be given, and --check reports rather than rewrites.
"""

import io
import os
import sys

import pytest

from menai_format.format import main


def run_cli(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    """Run the formatter with the given arguments and return (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, 'argv', ['menai-format', *argv])
    with pytest.raises(SystemExit) as exit_info:
        main()

    captured = capsys.readouterr()
    return exit_info.value.code, captured.out, captured.err


UNFORMATTED = '(foo   a  b)'
FORMATTED = '(foo a b)\n'


class TestInPlace:
    """A file is formatted in place by default."""

    def test_file_is_rewritten(self, tmp_path, monkeypatch, capsys):
        """An unformatted file is rewritten to the canonical style."""
        path = tmp_path / 'a.menai'
        path.write_text(UNFORMATTED, encoding='utf-8')

        code, out, err = run_cli(monkeypatch, capsys, str(path))

        assert code == 0
        assert path.read_text(encoding='utf-8') == FORMATTED
        assert str(path) in out
        assert err == ''

    def test_several_files_are_rewritten(self, tmp_path, monkeypatch, capsys):
        """Every file given on the command line is formatted."""
        first = tmp_path / 'a.menai'
        second = tmp_path / 'b.menai'
        first.write_text('(foo  a)', encoding='utf-8')
        second.write_text('(bar  b)', encoding='utf-8')

        code, _, _ = run_cli(monkeypatch, capsys, str(first), str(second))

        assert code == 0
        assert first.read_text(encoding='utf-8') == '(foo a)\n'
        assert second.read_text(encoding='utf-8') == '(bar b)\n'

    def test_formatted_file_is_not_rewritten(self, tmp_path, monkeypatch, capsys):
        """A file already in the canonical style is left untouched."""
        path = tmp_path / 'a.menai'
        path.write_text(FORMATTED, encoding='utf-8')
        os.utime(path, (1_000_000, 1_000_000))
        before = path.stat().st_mtime_ns

        code, out, _ = run_cli(monkeypatch, capsys, str(path))

        assert code == 0
        assert path.stat().st_mtime_ns == before
        assert out == ''


class TestOutput:
    """--output writes the result elsewhere instead of in place."""

    def test_output_file(self, tmp_path, monkeypatch, capsys):
        """The formatted result goes to the output file and the input is unchanged."""
        source = tmp_path / 'a.menai'
        target = tmp_path / 'b.menai'
        source.write_text(UNFORMATTED, encoding='utf-8')

        code, _, _ = run_cli(monkeypatch, capsys, str(source), '-o', str(target))

        assert code == 0
        assert source.read_text(encoding='utf-8') == UNFORMATTED
        assert target.read_text(encoding='utf-8') == FORMATTED

    def test_output_dash_is_stdout(self, tmp_path, monkeypatch, capsys):
        """--output - prints the formatted result to stdout."""
        source = tmp_path / 'a.menai'
        source.write_text(UNFORMATTED, encoding='utf-8')

        code, out, _ = run_cli(monkeypatch, capsys, str(source), '-o', '-')

        assert code == 0
        assert out == FORMATTED
        assert source.read_text(encoding='utf-8') == UNFORMATTED

    def test_output_with_several_inputs_is_rejected(self, tmp_path, monkeypatch, capsys):
        """--output cannot be used with more than one input file."""
        code, _, err = run_cli(
            monkeypatch, capsys, str(tmp_path / 'a.menai'), str(tmp_path / 'b.menai'), '-o', 'out.menai'
        )

        assert code == 2
        assert '--output requires a single input file' in err


class TestStdin:
    """- reads stdin and writes stdout."""

    def test_stdin_formats_to_stdout(self, monkeypatch, capsys):
        """Stdin is formatted and printed, with no file written."""
        monkeypatch.setattr(sys, 'stdin', io.StringIO(UNFORMATTED))

        code, out, err = run_cli(monkeypatch, capsys, '-')

        assert code == 0
        assert out == FORMATTED
        assert err == ''

    def test_stdin_cannot_be_combined_with_files(self, tmp_path, monkeypatch, capsys):
        """- cannot be mixed with other inputs."""
        code, _, err = run_cli(monkeypatch, capsys, '-', str(tmp_path / 'a.menai'))

        assert code == 2
        assert 'cannot be combined with other input files' in err


class TestCheck:
    """--check reports formatting state and changes nothing."""

    def test_formatted_file_exits_zero(self, tmp_path, monkeypatch, capsys):
        """A formatted file is reported as formatted and exits 0."""
        path = tmp_path / 'a.menai'
        path.write_text(FORMATTED, encoding='utf-8')

        code, out, _ = run_cli(monkeypatch, capsys, '--check', str(path))

        assert code == 0
        assert f'{path} is already formatted' in out

    def test_unformatted_file_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """An unformatted file is reported and exits 1 without being rewritten."""
        path = tmp_path / 'a.menai'
        path.write_text(UNFORMATTED, encoding='utf-8')

        code, out, _ = run_cli(monkeypatch, capsys, '--check', str(path))

        assert code == 1
        assert f'{path} needs formatting' in out
        assert path.read_text(encoding='utf-8') == UNFORMATTED

    def test_every_file_is_reported(self, tmp_path, monkeypatch, capsys):
        """Each input is reported, and any unformatted one fails the run."""
        good = tmp_path / 'a.menai'
        bad = tmp_path / 'b.menai'
        good.write_text(FORMATTED, encoding='utf-8')
        bad.write_text(UNFORMATTED, encoding='utf-8')

        code, out, _ = run_cli(monkeypatch, capsys, '--check', str(good), str(bad))

        assert code == 1
        assert f'{good} is already formatted' in out
        assert f'{bad} needs formatting' in out

    def test_check_with_output_is_rejected(self, tmp_path, monkeypatch, capsys):
        """--check and --output are mutually exclusive."""
        code, _, err = run_cli(monkeypatch, capsys, '--check', str(tmp_path / 'a.menai'), '-o', 'x.menai')

        assert code == 2
        assert '--check cannot be combined with --output' in err

    def test_stdin_already_formatted(self, monkeypatch, capsys):
        """--check can check stdin."""
        monkeypatch.setattr(sys, 'stdin', io.StringIO(FORMATTED))

        code, out, _ = run_cli(monkeypatch, capsys, '--check', '-')

        assert code == 0
        assert '- is already formatted' in out


class TestFailures:
    """A failing input is reported without abandoning the others."""

    def test_missing_file_is_reported_and_the_rest_still_format(self, tmp_path, monkeypatch, capsys):
        """A missing input fails the run but other inputs are still formatted."""
        good = tmp_path / 'a.menai'
        good.write_text(UNFORMATTED, encoding='utf-8')

        code, _, err = run_cli(monkeypatch, capsys, str(tmp_path / 'missing.menai'), str(good))

        assert code == 1
        assert 'Error reading' in err
        assert good.read_text(encoding='utf-8') == FORMATTED

    def test_unparsable_file_is_reported_and_the_rest_still_format(self, tmp_path, monkeypatch, capsys):
        """A file that cannot be formatted fails the run but others are still formatted."""
        bad = tmp_path / 'bad.menai'
        good = tmp_path / 'a.menai'
        bad.write_text('"unterminated', encoding='utf-8')
        good.write_text(UNFORMATTED, encoding='utf-8')

        code, _, err = run_cli(monkeypatch, capsys, str(bad), str(good))

        assert code == 1
        assert f'Error formatting {bad}' in err
        assert good.read_text(encoding='utf-8') == FORMATTED
