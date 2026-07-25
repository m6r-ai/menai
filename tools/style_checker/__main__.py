"""
Entry point for running the style checks standalone.

The checks normally run as part of the full code checker:

    source venv/bin/activate && python -m tools.code_checker

This module runs just the style-check portion (the pylint plugin)
against ``src`` for quick iteration during development:

    source venv/bin/activate && python -m tools.style_checker
"""

import subprocess
import sys


def main() -> int:
    """Run pylint with only the style plugin enabled."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pylint",
            "--load-plugins=tools.style_checker.style_checker",
            "--disable=all",
            "--enable=m6r-no-property,m6r-no-optional,m6r-no-aligned-assigns,"
            "m6r-no-union,m6r-blank-before-dedent,m6r-multiline-docstring",
            "src",
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())