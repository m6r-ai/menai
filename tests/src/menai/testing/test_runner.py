"""Tests for the library form of the Menai test runner."""

import textwrap
from pathlib import Path

import pytest

import menai


_SUITE = textwrap.dedent(
    """
    (let ((t (import "menai-test"))
          (assert-equal (:: t assert-equal))
          (expect-pass (:: t expect-pass))
          (expect-fail (:: t expect-fail))

          (test-alpha (lambda () (assert-equal 1 1)))
          (test-beta (lambda () (assert-equal 2 2)))
          (test-fails (lambda () (assert-equal 3 4)))
          (test-raises (lambda () (error "expected trouble"))))

      (let ((tests
             (list
               (list "group one"
                     (list
                       (expect-pass "alpha" test-alpha)
                       (expect-pass "beta" test-beta)))
               (list "group two"
                     (list
                       (expect-pass "fails" test-fails)
                       (expect-fail "raises" test-raises "trouble"))))))
        (export tests)))
    """
)


def _write_suite(tmp_path: Path, source: str) -> Path:
    """Write a suite to a temporary file and return its path."""
    path = tmp_path / "runner-probe.test.menai"
    path.write_text(source)
    return path


def test_runs_every_leaf_and_reports_paths(tmp_path: Path) -> None:
    """Every leaf runs and reports its full path in node-list order."""
    outcomes = menai.run_test_suite(_write_suite(tmp_path, _SUITE))

    assert [outcome.path for outcome in outcomes] == [
        ["group one", "alpha"],
        ["group one", "beta"],
        ["group two", "fails"],
        ["group two", "raises"],
    ]


def test_reports_positive_and_negative_outcomes(tmp_path: Path) -> None:
    """A passing leaf passes, an assertion failure fails, and a raise passes a negative test."""
    outcomes = menai.run_test_suite(_write_suite(tmp_path, _SUITE))
    by_path = {tuple(outcome.path): outcome for outcome in outcomes}

    assert by_path[("group one", "alpha")].passed
    assert by_path[("group one", "beta")].passed
    assert by_path[("group two", "raises")].passed
    assert not by_path[("group two", "fails")].passed


def test_assertion_failure_reports_expected_and_actual(tmp_path: Path) -> None:
    """An assertion failure reports the value mismatch."""
    outcomes = menai.run_test_suite(_write_suite(tmp_path, _SUITE))
    failed = next(outcome for outcome in outcomes if "fails" in outcome.path)

    assert failed.error is not None
    assert "assert-equal failed" in failed.error


def test_negative_test_fails_when_the_thunk_returns(tmp_path: Path) -> None:
    """A negative test fails when its thunk does not raise."""
    source = textwrap.dedent(
        """
        (let ((t (import "menai-test"))
              (expect-fail (:: t expect-fail))
              (tests
               (list
                 (expect-fail "returns" (lambda () 42)))))
          (export tests))
        """
    )

    outcomes = menai.run_test_suite(_write_suite(tmp_path, source))

    assert len(outcomes) == 1
    assert not outcomes[0].passed
    assert outcomes[0].error == "expected an error but the test returned normally"


def test_negative_test_requires_the_expected_substring(tmp_path: Path) -> None:
    """A negative test fails when the raised message lacks the expected substring."""
    source = textwrap.dedent(
        """
        (let ((t (import "menai-test"))
              (expect-fail (:: t expect-fail))
              (tests
               (list
                 (expect-fail "different error" (lambda () (error "actual")) "wanted"))))
          (export tests))
        """
    )

    outcomes = menai.run_test_suite(_write_suite(tmp_path, source))

    assert len(outcomes) == 1
    assert not outcomes[0].passed
    assert outcomes[0].error is not None
    assert "wanted" in outcomes[0].error


def test_invalid_node_structure_raises(tmp_path: Path) -> None:
    """A `tests` value that is not a node-list is rejected."""
    source = textwrap.dedent(
        """
        (let ((tests (list "not a node")))
          (export tests))
        """
    )

    with pytest.raises(ValueError):
        menai.run_test_suite(_write_suite(tmp_path, source))


def test_explicit_module_path_is_searched_first(tmp_path: Path) -> None:
    """A suite resolves the module under test from the supplied module path."""
    library = tmp_path / "library"
    library.mkdir()
    (library / "my-mod.menai").write_text(
        "(let ((double (lambda (x) (integer* x 2)))) (export double))"
    )

    source = textwrap.dedent(
        """
        (let ((m (import "my-mod"))
              (t (import "menai-test"))
              (assert-equal (:: t assert-equal))
              (expect-pass (:: t expect-pass))
              (double (:: m double))
              (tests
               (list
                 (expect-pass "doubles" (lambda () (assert-equal (double 3) 6))))))
          (export tests))
        """
    )
    suite = _write_suite(tmp_path, source)

    outcomes = menai.run_test_suite(suite, [str(library)])

    assert len(outcomes) == 1
    assert outcomes[0].passed


def test_shadowed_suite_is_rejected(tmp_path: Path) -> None:
    """A suite that the module path would resolve to a different file is rejected."""
    library = tmp_path / "library"
    sub = library / "sub"
    sub.mkdir(parents=True)
    minimal = '(let ((tests (list))) (export tests))'
    (library / "shared.test.menai").write_text(minimal)
    requested = sub / "shared.test.menai"
    requested.write_text(minimal)

    with pytest.raises(ValueError, match="resolves to"):
        menai.run_test_suite(requested, [str(library)])


def test_suite_directory_ahead_of_shadow_runs_the_requested_file(tmp_path: Path) -> None:
    """Putting the suite's own directory first resolves the requested file."""
    library = tmp_path / "library"
    sub = library / "sub"
    sub.mkdir(parents=True)
    minimal = '(let ((tests (list))) (export tests))'
    (library / "shared.test.menai").write_text(minimal)
    requested = sub / "shared.test.menai"
    requested.write_text(minimal)

    assert menai.run_test_suite(requested, [str(sub), str(library)]) == []
