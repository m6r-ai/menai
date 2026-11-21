#!/usr/bin/env python3
"""Command-line tool for the Menai formatter."""

import argparse
import sys
import traceback
from pathlib import Path

from menai import MenaiError
from menai_format.menai_formatter import MenaiFormatter, FormatOptions

STDIN = '-'


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description='Format Menai source code in place',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Format files in place
  menai-format myfile.menai src/menai/stdlib/*.menai

  # Write the result to another file
  menai-format myfile.menai -o formatted.menai

  # Print the result to stdout
  menai-format myfile.menai -o -

  # Format from stdin
  echo "(let ((x 5)(y 10)) (+ x y))" | menai-format -

  # Report which files need formatting without changing them
  menai-format --check src/menai/prelude.menai src/menai/stdlib/*.menai
"""
    )
    parser.add_argument(
        'inputs',
        nargs='+',
        metavar='FILE',
        help='Files to format in place (use "-" to read stdin and print to stdout)'
    )
    parser.add_argument(
        '-o', '--output',
        metavar='FILE',
        help='Write the result to FILE instead of in place (use "-" for stdout); '
             'requires a single input'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Report which files need formatting and exit 1 if any do; changes nothing'
    )
    parser.add_argument(
        '--indent',
        type=int,
        default=2,
        help='Number of spaces per indentation level (default: 2)'
    )
    parser.add_argument(
        '--max-width',
        type=int,
        default=120,
        help='Maximum column for compact formatting (default: 120)'
    )
    parser.add_argument(
        '--comment-spacing',
        type=int,
        default=2,
        help='Spaces before end-of-line comments (default: 2)'
    )

    return parser


def _parse_args() -> argparse.Namespace:
    """Parse the command line and validate the combinations of options."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.check and args.output is not None:
        parser.error('--check cannot be combined with --output')

    if args.output is not None and len(args.inputs) > 1:
        parser.error('--output requires a single input file')

    if STDIN in args.inputs and len(args.inputs) > 1:
        parser.error('stdin ("-") cannot be combined with other input files')

    return args


def _write_result(formatted: str, output: str) -> bool:
    """Write formatted source to the output destination, returning True on failure."""
    if output == STDIN:
        print(formatted, end='')

        return False

    try:
        Path(output).write_text(formatted, encoding='utf-8')

    except OSError as exc:
        print(f"Error writing {output}: {exc}", file=sys.stderr)
        return True

    print(f"Formatted code written to {output}")

    return False


def _format_source(
    formatter: MenaiFormatter,
    source: str,
    path: Path | None,
    args: argparse.Namespace,
) -> bool:
    """
    Format one source string, performing the requested action with the result.

    Returns True if the operation failed: the source could not be formatted, or
    the file could not be written, or a check found the source unformatted.
    """
    display = STDIN if path is None else str(path)

    try:
        formatted = formatter.format(source)

    except MenaiError as exc:
        print(f"Error formatting {display}: {exc}", file=sys.stderr)
        return True

    except Exception as exc:
        print(f"Error formatting {display}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return True

    if args.check:
        if formatted == source:
            print(f"✓ {display} is already formatted")

            return False

        print(f"✗ {display} needs formatting")

        return True

    if args.output is not None:
        return _write_result(formatted, args.output)

    if path is None:
        print(formatted, end='')

        return False

    if formatted == source:
        return False

    try:
        path.write_text(formatted, encoding='utf-8')

    except OSError as exc:
        print(f"Error writing {path}: {exc}", file=sys.stderr)
        return True

    print(f"Formatted {path}")

    return False


def _format_inputs(formatter: MenaiFormatter, args: argparse.Namespace) -> bool:
    """Format every input in turn, returning True if any of them failed."""
    if args.inputs == [STDIN]:
        return _format_source(formatter, sys.stdin.read(), None, args)

    failed = False
    for name in args.inputs:
        path = Path(name)

        try:
            source = path.read_text(encoding='utf-8')

        except OSError as exc:
            print(f"Error reading {name}: {exc}", file=sys.stderr)
            failed = True

            continue

        if _format_source(formatter, source, path, args):
            failed = True

    return failed


def main() -> None:
    """Main entry point for the Menai formatter CLI."""
    args = _parse_args()

    options = FormatOptions(
        indent_size=args.indent,
        max_width=args.max_width,
        comment_spacing=args.comment_spacing
    )
    formatter = MenaiFormatter(options)

    sys.exit(1 if _format_inputs(formatter, args) else 0)


if __name__ == '__main__':
    main()
