"""Tests for the menai-test runner's leaf execution.

The runner compiles the leaf program once per file and rebinds the path before
each execution.  These tests exist to make sure that rebinding is real: if the
path were not rebound, every leaf would run with the same path and the
distinguishable leaves below would fail.
"""

import textwrap
from pathlib import Path

import pytest

from menai_test.test_run import run_file

# A minimal test suite whose leaves are distinguishable by their path.  Each
# leaf asserts something that is only true for its own path, so a runner that
# reused one path for every leaf would fail at least one of them.
_SUITE = textwrap.dedent(
    """
    (let ((t (import "menai-test"))
          (assert-equal (:: t assert-equal))
          (expect-pass (:: t expect-pass))
          (expect-fail (:: t expect-fail))

          (test-alpha
           (lambda ()
             (assert-equal 1 1)))

          (test-beta
           (lambda ()
             (assert-equal 2 2)))

          (test-gamma
           (lambda ()
             (assert-equal 3 3)))

          (test-delta-raises
           (lambda ()
             (error "delta always fails")))

          (test-epsilon-raises
           (lambda ()
             (error "epsilon always fails"))))

      (let ((tests
             (list
               (list "group one"
                     (list
                       (expect-pass "alpha" test-alpha)
                       (expect-pass "beta" test-beta)))
               (list "group two"
                     (list
                       (expect-pass "gamma" test-gamma)
                       (expect-fail "delta raises" test-delta-raises "delta always fails")
                       (expect-fail "epsilon raises" test-epsilon-raises "epsilon always fails"))))))
        (export tests)))
    """
)


@pytest.fixture
def suite_file(tmp_path: Path) -> Path:
    """Write the test suite to a temporary file and return its path."""
    path = tmp_path / "runner-probe.test.menai"
    path.write_text(_SUITE)
    return path


def test_all_leaves_pass(suite_file: Path) -> None:
    """Every leaf passes when the runner rebinds the path per leaf."""
    results = run_file(suite_file, None)

    assert len(results) == 5
    failures = [r for r in results if not r.passed]
    assert not failures, "\n".join(
        f"{' > '.join(r.path)}: {r.error}" for r in failures
    )


def test_results_are_per_leaf(suite_file: Path) -> None:
    """Each leaf reports its own path, so results are not collapsed."""
    results = run_file(suite_file, None)

    paths = {" > ".join(r.path) for r in results}
    assert paths == {
        "group one > alpha",
        "group one > beta",
        "group two > gamma",
        "group two > delta raises",
        "group two > epsilon raises",
    }


def test_isolate_agrees_with_reuse(suite_file: Path) -> None:
    """Isolate mode produces the same outcomes as the reuse path."""
    reused = run_file(suite_file, None)
    isolated = run_file(suite_file, None, isolate=True)

    assert [(r.path, r.passed) for r in reused] == [
        (r.path, r.passed) for r in isolated
    ]


def test_filter_selects_one_leaf(suite_file: Path) -> None:
    """The name filter still selects individual leaves under reuse."""
    results = run_file(suite_file, "beta")

    assert len(results) == 1
    assert results[0].path == ["group one", "beta"]
    assert results[0].passed


def test_expect_error_contains_is_enforced(suite_file: Path) -> None:
    """An expect-error leaf fails if the error message does not match."""
    results = run_file(suite_file, "delta")
    assert len(results) == 1
    assert results[0].passed

    # Rewrite the suite so the expected substring cannot match.
    text = suite_file.read_text().replace(
        '(expect-fail "delta raises" test-delta-raises "delta always fails")',
        '(expect-fail "delta raises" test-delta-raises "a different message")',
    )
    suite_file.write_text(text)

    results = run_file(suite_file, "delta")
    assert len(results) == 1
    assert not results[0].passed
    assert "a different message" in (results[0].error or "")
