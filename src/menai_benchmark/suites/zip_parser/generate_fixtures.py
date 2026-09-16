"""
Generate the ZIP fixture files used by the zip_parser benchmark suite.

The fixtures cover archives with stored and deflate entries, and with a range
of entry counts and entry sizes.  They are committed so every run reads
byte-identical inputs.

Run from the repository root:

    python src/menai_benchmark/suites/zip_parser/generate_fixtures.py

Output is deterministic: every entry is given a fixed timestamp, a fixed
compression method, and content derived from fixed arithmetic patterns, so
re-running the script reproduces the committed files exactly.
"""

from __future__ import annotations

from pathlib import Path
import io
import zipfile

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# A fixed timestamp so archives do not embed the current time.
_FIXED_DATE_TIME = (2024, 1, 1, 0, 0, 0)

_DEFLATED = zipfile.ZIP_DEFLATED
_STORED = zipfile.ZIP_STORED


def _text_content(size: int) -> bytes:
    """Return a repeated English-like phrase of the given length."""
    phrase = b"the quick brown fox jumps over the lazy dog. "
    return (phrase * (size // len(phrase) + 1))[:size]


def _make_zip(entries: list[tuple[str, bytes, int]]) -> bytes:
    """Build a ZIP archive from (name, content, compression) triples."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content, compression in entries:
            info = zipfile.ZipInfo(name, date_time=_FIXED_DATE_TIME)
            info.compress_type = compression
            info.external_attr = 0
            archive.writestr(info, content)

    return buffer.getvalue()


def _many_entries(count: int) -> list[tuple[str, bytes, int]]:
    """Return a list of small deflate entries."""
    return [
        (f"file{i:03d}.txt", _text_content(200 + i), _DEFLATED)
        for i in range(count)
    ]


_FIXTURES: list[tuple[str, list[tuple[str, bytes, int]]]] = [
    ("single-deflate.zip", [
        ("hello.txt", _text_content(1024), _DEFLATED),
    ]),
    ("single-stored.zip", [
        ("hello.txt", _text_content(1024), _STORED),
    ]),
    ("mixed-16.zip", [
        (f"file{i:02d}.txt", _text_content(512 + i * 32),
         _DEFLATED if i % 2 == 0 else _STORED)
        for i in range(16)
    ]),
    ("many-128.zip", _many_entries(128)),
    ("large-256k.zip", [
        ("large.bin", _text_content(256 * 1024), _DEFLATED),
    ]),
]


def main() -> None:
    """Write every fixture file to the fixtures directory."""
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, entries in _FIXTURES:
        data = _make_zip(entries)
        (_FIXTURES_DIR / name).write_bytes(data)
        print(f"{name}: {len(entries)} entries, {len(data)} bytes")


if __name__ == "__main__":
    main()
