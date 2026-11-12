"""
Function coverage of each standard library module, measured by its own suite.

A module is measured only by the suite that exists to test it, never by another
module's suite, so a gap reported here is a gap in that module's own tests.

Two properties are asserted: every module compiles at least one function from
its own source, and no module contains bytecode that no execution can reach.
The second is a regression guard rather than a target: dead bytecode is a
compiler fault, not a missing test, and it is currently absent everywhere.
"""

import functools
from pathlib import Path

import pytest

from menai import Menai
from menai_test.test_run import run_file
from menai_trace.menai_trace_coverage import CoverageAccumulator
from menai_trace.menai_trace_source_coverage import SourceFileCoverage, coverage_for_source

_STDLIB_DIR = Menai.stdlib_path()
assert _STDLIB_DIR is not None

_SUFFIX = ".test.menai"
_MODULE_SUFFIX = ".menai"

# Modules whose own suite exercises every function in full.  A module leaves
# this set only when a test is removed or a path stops being taken.
_FULLY_COVERED = frozenset({
    "csv-encode", "docx-decode", "docx-encode", "gzip-compress", "json-encode",
    "json-decode", "pptx-encode", "tar-entries", "xlsx-encode", "zlib-compress",
})


def _subject(test_file: Path) -> str | None:
    """Return the module a suite tests, or None when it has no single subject."""
    subject = test_file.name[:-len(_SUFFIX)]
    if not (test_file.parent / f"{subject}{_MODULE_SUFFIX}").is_file():
        return None

    return f"{subject}{_MODULE_SUFFIX}"


def _module_name(test_file: Path) -> str:
    """Return the module name of a suite, without either file extension."""
    return test_file.name[:-len(_SUFFIX)]


_SUBJECT_TEST_FILES = [
    test_file
    for test_file in sorted(_STDLIB_DIR.glob(f"*{_SUFFIX}"))
    if _subject(test_file) is not None
]

_FULLY_COVERED_TEST_FILES = [
    test_file for test_file in _SUBJECT_TEST_FILES if _module_name(test_file) in _FULLY_COVERED
]


@functools.lru_cache(maxsize=None)
def _report(test_file: Path) -> SourceFileCoverage:
    """Run a suite and return the coverage of the module it tests."""
    accumulator = CoverageAccumulator()
    run_file(test_file, None, coverage=accumulator)

    subject = _subject(test_file)
    assert subject is not None
    return coverage_for_source(accumulator.result(), subject)


@pytest.mark.parametrize("test_file", _SUBJECT_TEST_FILES, ids=_module_name)
def test_module_compiles_functions_from_its_own_source(test_file: Path) -> None:
    """Every standard library module contributes at least one function to its own suite."""
    report = _report(test_file)

    assert report.function_count() > 0, f"{report.source_file} compiled no functions"


@pytest.mark.parametrize("test_file", _SUBJECT_TEST_FILES, ids=_module_name)
def test_module_has_no_dead_bytecode(test_file: Path) -> None:
    """No function in a standard library module contains an instruction nothing can reach."""
    report = _report(test_file)

    dead = [
        f"  {outcome.function.code.name} line {outcome.function.code.source_line}: "
        f"{[instruction.index for instruction in outcome.unreachable_instructions()]}"
        for outcome in report.outcomes
        if outcome.unreachable_instructions()
    ]
    assert not dead, f"{report.source_file} contains unreachable bytecode:\n" + "\n".join(dead)


@pytest.mark.parametrize("test_file", _FULLY_COVERED_TEST_FILES, ids=_module_name)
def test_fully_covered_module_stays_fully_covered(test_file: Path) -> None:
    """A module whose suite exercises every function in full continues to do so."""
    report = _report(test_file)

    incomplete = [
        f"  {outcome.function.code.name} line {outcome.function.code.source_line} "
        f"({outcome.status()})"
        for outcome in report.outcomes
        if not outcome.fully_covered()
    ]
    assert not incomplete, f"{report.source_file} is no longer fully covered:\n" + "\n".join(incomplete)
