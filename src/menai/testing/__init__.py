"""
Test support and the library test runner shipped with the Menai package.

`support_module_dir` and `support_module_source` locate the `menai-test`
module, the assertion and node-construction helpers a `*.test.menai` suite
uses.  `run_test_suite` loads a suite, executes each leaf in isolation and
returns structured outcomes for a host tool to render.

The support module lives as package data inside the `menai` package and is
located through `importlib.resources`, the same mechanism the standard library
uses, so it is present in a wheel install rather than only in a source checkout.
"""

from menai.testing.runner import TestOutcome, run_test_suite
from menai.testing.support import (
    SUPPORT_MODULE_NAME, support_module_dir, support_module_source,
)

__all__ = [
    "SUPPORT_MODULE_NAME",
    "TestOutcome",
    "run_test_suite",
    "support_module_dir",
    "support_module_source",
]
