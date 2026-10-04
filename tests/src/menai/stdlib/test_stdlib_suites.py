"""Run the standard library's *.test.menai suites under pytest."""

from pathlib import Path

import pytest

from menai import Menai
from menai_test.test_run import run_file

_STDLIB_DIR = Menai.stdlib_path()
assert _STDLIB_DIR is not None

_TEST_FILES = sorted(_STDLIB_DIR.glob("*.test.menai"))


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
