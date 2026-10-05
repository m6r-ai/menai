from __future__ import annotations

import random

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram

_ITERATIONS = 5

_rng = random.Random(42)

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _text(length: int) -> str:
    """Return a deterministic pseudo-random lowercase string of the given length."""
    return "".join(_rng.choice(_ALPHABET) for _ in range(length))


def _search_case(haystack_len: int, needle_len: int, present: bool) -> dict:
    """
    Build a case input for a substring search.

    When *present* is True the needle is taken from the end of the haystack, so
    a match is found only after the scan has traversed most of the input.  When
    it is False the needle is absent, so the scan runs to exhaustion.  Absence
    is guaranteed by construction: the needle is uppercase, which cannot occur
    in the lowercase haystack.  Rejection-sampling the needle instead would not
    terminate for short needles over a large haystack, since every short
    lowercase combination is present.
    """
    haystack = _text(haystack_len)

    if present:
        needle = haystack[haystack_len - needle_len:]

    else:
        needle = "Z" * needle_len

    return {"haystack": haystack, "needle": needle}


def _hash_case(count: int, key_len: int) -> dict:
    """Build a case input of *count* distinct strings of the given length."""
    keys: list[str] = []
    seen: set[str] = set()
    while len(keys) < count:
        key = _text(key_len)
        if key not in seen:
            seen.add(key)
            keys.append(key)

    return {"keys": keys}


_SEARCH_CASES = {
    "search long/absent": _search_case(20000, 16, False),
    "search long/present": _search_case(20000, 16, True),
    "search short needle": _search_case(20000, 2, False),
    "search needle=haystack": _search_case(20000, 20000, True),
}

_HASH_CASES = {
    "hash 2000x8": _hash_case(2000, 8),
    "hash 2000x64": _hash_case(2000, 64),
    "hash 500x512": _hash_case(500, 512),
}


def _needle_expr(needle: str) -> str:
    """
    Return a Menai expression that builds the needle at runtime.

    The needle must not be a literal: a literal needle and a literal haystack
    would be constant-folded at compile time, leaving the timed loop measuring
    nothing.  Constructing it from a list of single-character strings keeps the
    search itself a genuine runtime operation.
    """
    chars = " ".join(f'"{c}"' for c in needle)
    return f"(list->string (list {chars}))"


def _search_expr(case_input: dict) -> str:
    """
    Return a Menai expression that searches a haystack for a needle.

    The haystack arrives as injected bytes and is converted to a string at
    runtime, for the same reason the needle is constructed rather than written
    as a literal.
    """
    needle = _needle_expr(case_input["needle"])
    return (
        f'(string-index (bytes->string (dict-get inputs "input-data")) {needle})'
    )


def _hash_expr(case_input: dict) -> str:
    """
    Return a Menai expression that builds a set from a list of strings.

    list->set hashes each element exactly once, so this isolates the string
    hash from table insertion and rehashing.
    """
    items = " ".join(f'"{key}"' for key in case_input["keys"])
    return f"(list->set (list {items}))"


class Suite(BenchmarkSuite):
    """Benchmark suite for string search and string hashing."""

    name = "string_ops"
    description = (
        "Substring search via string-index and string hashing via list->set."
    )

    def cases(self) -> list[BenchmarkCase]:
        """Return the search and hash cases."""
        cases = [
            BenchmarkCase(name=name, input=case_input, iterations=_ITERATIONS)
            for name, case_input in _SEARCH_CASES.items()
        ]
        cases.extend(
            BenchmarkCase(name=name, input=case_input, iterations=_ITERATIONS)
            for name, case_input in _HASH_CASES.items()
        )
        return cases

    def menai_program(self) -> MenaiProgram:
        """Return the expression builder and haystack fixture."""
        def expression(case_input: dict) -> str:
            if "haystack" in case_input:
                return _search_expr(case_input)

            return _hash_expr(case_input)

        def fixture(case_input: dict) -> bytes:
            """
            Bind the haystack as injected bytes for the search cases.

            The framework calls this for every case regardless of whether the
            expression reads the inputs dict, so the hash cases (which have no
            haystack) return an empty payload.
            """
            return case_input.get("haystack", "").encode("ascii")

        return MenaiProgram(expression=expression, fixture=fixture)
