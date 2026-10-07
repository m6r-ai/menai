from __future__ import annotations

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 10

_WORDS = (
    "the quick brown fox jumps over the lazy dog while a cat and a bird watch "
    "from the fence and the dog sleeps in the afternoon sun near the old barn "
)


def _prose(size: int) -> str:
    """Return a repeated English-like phrase of the given length."""
    return (_WORDS * (size // len(_WORDS) + 1))[:size]


def _log_lines(count: int) -> str:
    """Return a log-like text where only a few lines carry the target word."""
    lines = []
    for i in range(count):
        level = "ERROR" if i % 20 == 0 else "INFO"
        lines.append(f"2026-10-06 12:{i % 60:02d}:00 {level} request {i}")

    return "\n".join(lines)


def _number_lines(count: int) -> str:
    """Return prose lines with embedded runs of digits."""
    lines = []
    for i in range(count):
        lines.append(f"item {i} measured {i * 37} units and {i * 11 + 5} grams")

    return "\n".join(lines)


def _spaced_runs(count: int) -> str:
    """Return text with runs of whitespace between words."""
    return "   ".join(f"word{i}" for i in range(count))


_FIXTURES: dict[str, str] = {
    "log-1000": _log_lines(1000),
    "numbers-1000": _number_lines(1000),
    "prose-64k": _prose(64 * 1024),
    "spaced-4000": _spaced_runs(4000),
}


def _fixture_text(name: str) -> bytes:
    """Return the fixture text for a name as UTF-8 bytes."""
    return _FIXTURES[name].encode("utf-8")


def _expr(body: str) -> str:
    """Wrap a body in the regexp import and a string binding from the fixture."""
    return (
        '(let ((re (import "regexp")))'
        "  (let ((s (bytes->string (dict-get inputs \"input-data\"))))"
        f"    {body}))"
    )


_CASES: list[tuple[str, str, str]] = [
    (
        "compile-per-line",
        "log-1000",
        '(let ((compile (:: re compile)) (split (:: re split)))'
        '  (map-list (lambda (line)'
        '              (compile "^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} (ERROR|WARN|INFO) .*$"))'
        '            (split (compile "\\\\n") s)))',
    ),
    (
        "search-prefix-log",
        "log-1000",
        '(let ((compile (:: re compile)) (search (:: re search)))'
        '  (search (compile "ERROR") s))',
    ),
    (
        "search-class-numbers",
        "numbers-1000",
        '(let ((compile (:: re compile)) (search (:: re search)))'
        '  (search (compile "[0-9]+") s))',
    ),
    (
        "search-alternation",
        "prose-64k",
        '(let ((compile (:: re compile)) (search (:: re search)))'
        '  (search (compile "cat|dog|bird") s))',
    ),
    (
        "search-all-class",
        "numbers-1000",
        '(let ((compile (:: re compile)) (search-all (:: re search-all)))'
        '  (search-all (compile "[0-9]+") s))',
    ),
    (
        "search-all-prefix",
        "log-1000",
        '(let ((compile (:: re compile)) (search-all (:: re search-all)))'
        '  (search-all (compile "ERROR") s))',
    ),
    (
        "split-whitespace",
        "spaced-4000",
        '(let ((compile (:: re compile)) (split (:: re split)))'
        '  (split (compile "\\\\s+") s))',
    ),
    (
        "replace-whitespace",
        "prose-64k",
        '(let ((compile (:: re compile)) (replace (:: re replace)))'
        '  (replace (compile "\\\\s+") s "_"))',
    ),
    (
        "chained-per-line",
        "log-1000",
        '(let ((compile (:: re compile)) (split (:: re split)) (search (:: re search)))'
        '  (let ((rx (compile "ERROR")))'
        '    (map-list (lambda (line) (search rx line)) (split (compile "\\\\n") s))))',
    ),
]


class Suite(BenchmarkSuite):
    """Benchmark suite for the Menai regular-expression module."""

    name = "regexp"
    description = "Compile, search, split, and replace with regular expressions."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per regexp operation and input."""
        return [
            BenchmarkCase(name=name, input=(name, fixture), iterations=_ITERATIONS)
            for name, fixture, _ in _CASES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the regexp expression, reading the case's fixture text."""
        expressions = {name: _expr(body) for name, _, body in _CASES}

        def expression(case_input: tuple[str, str]) -> str:
            return expressions[case_input[0]]

        def fixture(case_input: tuple[str, str]) -> bytes:
            return _fixture_text(case_input[1])

        return MenaiProgram(expression=expression, fixture=fixture)
