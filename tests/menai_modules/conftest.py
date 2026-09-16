"""Shared fixtures for standard-library module tests."""

from pathlib import Path

import pytest

from menai import Menai

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MODULES_DIR = _REPO_ROOT / "menai_modules"
_RUNNER_DIR = _REPO_ROOT / "src" / "menai_test"


@pytest.fixture
def modules_dir() -> Path:
    """Path to the standard library modules directory."""
    return _MODULES_DIR


@pytest.fixture
def menai_modules() -> Menai:
    """Create a Menai instance with the standard library on its module path."""
    return Menai(module_path=[str(_RUNNER_DIR), str(_MODULES_DIR)])
