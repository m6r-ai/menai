"""Run the standard library's *_test.menai suites under pytest."""

from pathlib import Path

import pytest

from menai_test.test_run import run_file

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MODULES_DIR = _REPO_ROOT / "menai_modules"

_TEST_FILES = sorted(_MODULES_DIR.glob("*_test.menai"))


@pytest.mark.parametrize("test_file", _TEST_FILES, ids=lambda p: p.stem)
def test_module_suite(test_file: Path) -> None:
    """Every standard library module test suite passes."""
    results = run_file(test_file, None)

    assert results, f"{test_file.name} contained no tests"

    failures = [result for result in results if not result.passed]
    if failures:
        detail = "\n".join(
            f"  {' > '.join(result.path)}: {result.error}"
            for result in failures
        )
        pytest.fail(f"{len(failures)} of {len(results)} tests failed:\n{detail}")
