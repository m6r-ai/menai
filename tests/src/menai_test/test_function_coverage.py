"""
Tests for the runner's function coverage reporting.

The report is per file and per module under test, so these tests pin the two
decisions the runner makes: which module a test file is responsible for, and
what the report prints for it.
"""

import sys
import textwrap
from pathlib import Path

import pytest

from menai_test.test_run import _coverage_source, _print_function_coverage, main, run_file
from menai_trace.menai_trace_coverage import CoverageAccumulator

_SUBJECT = textwrap.dedent(
    """
    (let ((alpha (lambda (n) (integer+ n 1)))
          (beta (lambda (n) (if (integer>? n 0) "positive" "non-positive")))
          (gamma (lambda (n) (integer* n 2))))
      (export alpha beta gamma))
    """
)

_SUITE = textwrap.dedent(
    """
    (let ((t (import "menai-test"))
          (m (import "probe")))
      (let ((assert-equal (:: t assert-equal))
            (expect-pass (:: t expect-pass))
            (alpha (:: m alpha))
            (beta (:: m beta))
            (gamma (:: m gamma)))
        (let ((tests
               (list
                 (list "values"
                       (list
                         (expect-pass "alpha" (lambda () (assert-equal (alpha 1) 2)))
                         (expect-pass "beta" (lambda () (assert-equal (beta 1) "positive")))
                         (expect-pass "gamma" (lambda () (assert-equal (gamma 2) 4))))))))
          (export tests))))
    """
)


@pytest.fixture
def probe_dir(tmp_path: Path) -> Path:
    """Write a module under test and its suite, returning the directory."""
    (tmp_path / "probe.menai").write_text(_SUBJECT)
    (tmp_path / "probe.test.menai").write_text(_SUITE)
    return tmp_path


def _accumulated(probe_dir: Path, name_filter: str | None = None) -> CoverageAccumulator:
    """Run the probe suite and return its populated coverage accumulator."""
    accumulator = CoverageAccumulator()
    run_file(probe_dir / "probe.test.menai", name_filter, coverage=accumulator)
    return accumulator


class TestCoverageSource:
    """The module a suite is responsible for is derived from its file name."""

    def test_names_the_module_the_suite_tests(self, probe_dir: Path):
        assert _coverage_source(probe_dir / "probe.test.menai") == "probe.menai"

    def test_round_trip_suites_have_no_single_subject(self, tmp_path: Path):
        suite = tmp_path / "json-round-trip.test.menai"
        suite.write_text(_SUITE)

        assert _coverage_source(suite) is None

    def test_missing_module_has_no_subject(self, tmp_path: Path):
        suite = tmp_path / "orphan.test.menai"
        suite.write_text(_SUITE)

        assert _coverage_source(suite) is None


class TestReportOutput:
    """The printed report summarises the module and separates the two populations."""

    def test_reports_the_module_under_test(self, probe_dir: Path, capsys: pytest.CaptureFixture):
        _print_function_coverage(
            probe_dir / "probe.test.menai", _accumulated(probe_dir), None, None,
        )
        text = capsys.readouterr().out

        assert "FUNCTION COVERAGE \u2014 probe.menai" in text
        assert "Functions:" in text
        assert "Reachable:" in text
        assert "Unreachable:" in text

    def test_lists_full_and_partial_functions(self, probe_dir: Path, capsys: pytest.CaptureFixture):
        _print_function_coverage(
            probe_dir / "probe.test.menai", _accumulated(probe_dir), None, None,
        )
        text = capsys.readouterr().out

        assert "alpha" in text
        assert "beta" in text
        assert "full" in text
        assert "gap" in text

    def test_names_the_unexecuted_reachable_instruction(self, probe_dir: Path, capsys: pytest.CaptureFixture):
        _print_function_coverage(
            probe_dir / "probe.test.menai", _accumulated(probe_dir), None, None,
        )
        text = capsys.readouterr().out

        assert "Reachable instructions not executed" in text
        assert "Dead bytecode (unreachable from entry)" in text
        assert "  None." in text

    def test_skips_suites_with_no_single_subject(self, probe_dir: Path, capsys: pytest.CaptureFixture):
        suite = probe_dir / "probe-round-trip.test.menai"
        suite.write_text(_SUITE)

        _print_function_coverage(suite, _accumulated(probe_dir), None, None)
        text = capsys.readouterr().out

        assert "no single module under test" in text

    def test_notes_that_a_filter_narrows_the_measurement(self, probe_dir: Path, capsys: pytest.CaptureFixture):
        _print_function_coverage(
            probe_dir / "probe.test.menai", _accumulated(probe_dir, "alpha"), "alpha", None,
        )
        text = capsys.readouterr().out

        assert "only the tests matching the filter" in text


class TestCommandLine:
    """The flag is wired into the runner and rejects isolate mode."""

    def test_prints_function_coverage(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        monkeypatch.setattr(
            sys, "argv", ["menai-test", str(probe_dir / "probe.test.menai"), "--function-coverage"],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code == 0
        assert "FUNCTION COVERAGE \u2014 probe.menai" in capsys.readouterr().out

    def test_rejects_function_coverage_in_isolate_mode(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        monkeypatch.setattr(
            sys,
            "argv",
            ["menai-test", str(probe_dir / "probe.test.menai"), "--function-coverage", "--isolate"],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code != 0
        assert "isolate" in capsys.readouterr().err

_OTHER_SUBJECT = textwrap.dedent(
    """
    (let ((delta (lambda (n) (integer+ n 2))))
      (export delta))
    """
)

_OTHER_SUITE = textwrap.dedent(
    """
    (let ((t (import "menai-test"))
          (m (import "other")))
      (let ((assert-equal (:: t assert-equal))
            (expect-pass (:: t expect-pass))
            (delta (:: m delta)))
        (let ((tests
               (list
                 (list "values"
                       (list
                         (expect-pass "delta" (lambda () (assert-equal (delta 1) 3))))))))
          (export tests))))
    """
)


class TestCumulativeReport:
    """A directory run ends with one report covering every module it measured."""

    def test_directory_run_prints_a_cumulative_report(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        (probe_dir / "other.menai").write_text(_OTHER_SUBJECT)
        (probe_dir / "other.test.menai").write_text(_OTHER_SUITE)
        monkeypatch.setattr(sys, "argv", ["menai-test", str(probe_dir), "--function-coverage"])

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code == 0
        text = capsys.readouterr().out
        assert "CUMULATIVE FUNCTION COVERAGE" in text
        assert "probe" in text
        assert "other" in text

    def test_single_file_run_omits_the_cumulative_report(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        monkeypatch.setattr(
            sys, "argv", ["menai-test", str(probe_dir / "probe.test.menai"), "--function-coverage"],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code == 0
        assert "CUMULATIVE FUNCTION COVERAGE" not in capsys.readouterr().out
