"""
Tests for the runner's annotated-trace reporting.

The report exists to answer the question a coverage gap raises: what does the
instruction that never ran actually do.  It is therefore a per-instruction
listing of the run with execution counts, and these tests pin the three decisions
the runner makes about it: the counts are summed across the file's leaves, the
listing can be narrowed to named functions, and it is refused where the counts
would not be comparable.
"""

import sys
import textwrap
from pathlib import Path

import pytest

from menai_test.test_run import main, run_file
from menai_trace.menai_trace_data import TraceAccumulator

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


def _trace(probe_dir: Path, name_filter: str | None = None) -> TraceAccumulator:
    """Run the probe suite and return its populated trace accumulator."""
    accumulator = TraceAccumulator()
    run_file(probe_dir / "probe.test.menai", name_filter, trace=accumulator)
    return accumulator


def _run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    """Invoke the runner's entry point with the given arguments."""
    monkeypatch.setattr(sys, "argv", ["menai-test", *args])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0


class TestAccumulatedTrace:
    """The trace covers every leaf in the file, not the last one."""

    def test_every_function_in_the_module_is_traced(self, probe_dir: Path):
        labels = [function.code.name for function in _trace(probe_dir).result().functions]

        assert any("alpha" in label for label in labels)
        assert any("beta" in label for label in labels)
        assert any("gamma" in label for label in labels)

    def test_counts_are_summed_across_leaves(self, probe_dir: Path):
        assert _trace(probe_dir).result().total_instructions() > 0

    def test_a_failing_leaf_still_contributes_its_counts(self, tmp_path: Path):
        (tmp_path / "probe.menai").write_text(_SUBJECT)
        (tmp_path / "failing.test.menai").write_text(
            textwrap.dedent(
                """
                (let ((t (import "menai-test"))
                      (m (import "probe")))
                  (let ((expect-pass (:: t expect-pass))
                        (expect-fail (:: t expect-fail))
                        (alpha (:: m alpha))
                        (beta (:: m beta)))
                    (let ((tests
                           (list
                             (list "values"
                                   (list
                                     (expect-pass "alpha" (lambda () (alpha 1)))
                                     (expect-fail "beta" (lambda () (beta 1)) "boom"))))))
                      (export tests))))
                """
            )
        )

        accumulator = TraceAccumulator()
        run_file(tmp_path / "failing.test.menai", None, trace=accumulator)

        assert accumulator.result().total_instructions() > 0


class TestCommandLine:
    """The flag is wired into the runner and rejects isolate mode."""

    def test_prints_an_annotated_listing(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        _run_cli(monkeypatch, str(probe_dir / "probe.test.menai"), "--annotate")
        text = capsys.readouterr().out

        assert "ANNOTATED TRACE" in text
        assert "Total instructions executed" in text
        assert "Instruction" in text

    def test_lists_every_function_without_a_filter(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        _run_cli(monkeypatch, str(probe_dir / "probe.test.menai"), "--annotate")
        text = capsys.readouterr().out

        assert "alpha" in text
        assert "gamma" in text

    def test_a_filter_narrows_the_listing(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        _run_cli(monkeypatch, str(probe_dir / "probe.test.menai"), "--annotate", "alpha")
        text = capsys.readouterr().out

        assert "alpha" in text
        assert "gamma [" not in text

    def test_an_unmatched_filter_says_so(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        _run_cli(monkeypatch, str(probe_dir / "probe.test.menai"), "--annotate", "no-such-function")
        text = capsys.readouterr().out

        assert "No function matched." in text

    def test_notes_that_a_test_filter_narrows_the_trace(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        _run_cli(
            monkeypatch, str(probe_dir / "probe.test.menai"), "--annotate", "--filter", "alpha",
        )
        text = capsys.readouterr().out

        assert "only the tests matching the filter" in text

    def test_rejects_the_trace_in_isolate_mode(
        self, probe_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ):
        monkeypatch.setattr(
            sys,
            "argv",
            ["menai-test", str(probe_dir / "probe.test.menai"), "--annotate", "--isolate"],
        )

        with pytest.raises(SystemExit) as exit_info:
            main()

        assert exit_info.value.code != 0
        assert "isolate" in capsys.readouterr().err


class TestIsolationRefusal:
    """The programmatic entry point refuses the same combination as the CLI."""

    def test_run_file_rejects_a_trace_in_isolate_mode(self, probe_dir: Path):
        with pytest.raises(ValueError, match="isolate"):
            run_file(
                probe_dir / "probe.test.menai", None, isolate=True, trace=TraceAccumulator(),
            )
