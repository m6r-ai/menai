"""
Location of the test support module shipped with the Menai package.

The `menai-test` module supplies the assertion and node-construction helpers a
`*.test.menai` suite uses to build its exported node-list.  It is not part of
the standard library and is not importable from an ordinary Menai program; a
host that runs suites makes it available by putting the directory returned by
`support_module_dir` on the module search path.

The module lives as package data inside the `menai` package and is located
through `importlib.resources`, the same mechanism the standard library uses,
so it is present in a wheel install rather than only in a source checkout.
"""

from importlib.resources import files
from pathlib import Path

SUPPORT_MODULE_NAME = "menai-test"


def support_module_dir() -> Path | None:
    """
    Return the on-disk directory containing the test support module.

    When the package is installed unpacked (the normal case for a wheel) the
    resource directory is a real filesystem path and is returned here.  Returns
    None when there is no filesystem location, in which case
    `support_module_source` is still usable.

    Returns:
        Path to the test support directory, or None if there is none
    """
    resource = files("menai") / "testing"
    try:
        return Path(str(resource))

    except TypeError:
        return None


def support_module_source() -> str:
    """
    Return the source text of the test support module.

    This works for every installation shape, including ones where the package
    is not unpacked to a real directory, because `importlib.resources` can read
    a resource without it corresponding to a file.

    Returns:
        The source text of `menai-test.menai`
    """
    return (files("menai") / "testing" / f"{SUPPORT_MODULE_NAME}.menai").read_text()
