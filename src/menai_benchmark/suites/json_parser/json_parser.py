"""
Pure Python JSON parser using an explicit stack — mirrors the Menai implementation.

Parses a JSON string into Python native types:
  JSON object  -> dict
  JSON array   -> list
  JSON string  -> str
  JSON integer -> int
  JSON float   -> float
  JSON true    -> True
  JSON false   -> False
  JSON null    -> None

Raises ValueError on malformed input.
"""

import json


def parse(s: str) -> object:
    """Parse a JSON string and return the equivalent Python value."""
    value, pos = _dispatch(s, 0, [])
    pos = _skip_ws(s, pos)
    if pos != len(s):
        raise ValueError(f"Unexpected trailing content at position {pos}: {s[pos:pos+20]!r}")

    return value


def _skip_ws(s: str, pos: int) -> int:
    """Skip whitespace characters and return the new position."""
    while pos < len(s) and s[pos] in ' \t\n\r':
        pos += 1

    return pos


def _parse_string(s: str, pos: int) -> tuple[str, int]:
    """Parse a JSON string starting just after the opening quote."""
    chars: list[str] = []
    length = len(s)

    while pos < length:
        ch = s[pos]
        if ch == '"':
            return ''.join(chars), pos + 1

        if ch == '\\':
            if pos + 1 >= length:
                raise ValueError("Unterminated escape sequence")

            esc = s[pos + 1]
            unescaped = {
                '"': '"', '\\': '\\', '/': '/',
                'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
            }.get(esc)

            if unescaped is None:
                if esc == 'u':
                    if pos + 5 >= length:
                        raise ValueError("Incomplete \\uXXXX escape")

                    hex_str = s[pos + 2:pos + 6]
                    try:
                        unescaped = chr(int(hex_str, 16))

                    except ValueError as exc:
                        raise ValueError(f"Invalid \\uXXXX escape: {hex_str!r}") from exc

                    chars.append(unescaped)
                    pos += 6
                    continue

                raise ValueError(f"Unknown escape sequence: \\{esc}")

            chars.append(unescaped)
            pos += 2

        else:
            chars.append(ch)
            pos += 1

    raise ValueError("Unterminated string literal")


def _parse_number(s: str, pos: int) -> tuple[int | float, int]:
    """Parse a JSON number."""
    end = pos
    length = len(s)

    while end < length and s[end] in '-+.eE0123456789':
        end += 1

    num_str = s[pos:end]

    try:
        if '.' in num_str or 'e' in num_str or 'E' in num_str:
            return float(num_str), end

        return int(num_str), end

    except ValueError as exc:
        raise ValueError(f"Invalid number literal: {num_str!r}") from exc


def _parse_keyword(s: str, pos: int, kw: str, val: object) -> tuple[object, int]:
    """Parse a keyword literal (true, false, null)."""
    end = pos + len(kw)
    if end > len(s):
        raise ValueError(f"Unexpected end of input reading '{kw}'")

    if s[pos:end] != kw:
        raise ValueError(f"Unrecognized keyword at position {pos}: {s[pos:pos+10]!r}")

    return val, end


# Explicit-stack trampoline
#
# Each stack frame is a tuple describing what to do when a sub-value completes:
#   ("array",  s, acc)       - resume building an array
#   ("object", s, d, key)    - resume building an object
def _resume(val: object, pos: int, stack: list) -> tuple[object, int]:
    """Apply the top stack frame with the completed value, or return if stack empty."""
    while stack:
        frame = stack[-1]

        if frame[0] == "array":
            _, s, acc = frame
            acc.append(val)
            pos = _skip_ws(s, pos)

            if pos >= len(s):
                raise ValueError("Unterminated array")

            ch = s[pos]
            if ch == ']':
                stack.pop()
                val = acc
                pos += 1
                continue

            if ch == ',':
                stack[-1] = ("array", s, acc)
                return _dispatch(s, _skip_ws(s, pos + 1), stack)

            raise ValueError(f"Expected ',' or ']' in array, got {ch!r} at position {pos}")

        # "object"
        _, s, d, key = frame
        d[key] = val
        pos = _skip_ws(s, pos)

        if pos >= len(s):
            raise ValueError("Unterminated object")

        ch = s[pos]
        if ch == '}':
            stack.pop()
            val = d
            pos += 1
            continue

        if ch == ',':
            stack.pop()
            return _parse_object_key(s, _skip_ws(s, pos + 1), d, stack)

        raise ValueError(f"Expected ',' or '}}' in object, got {ch!r} at position {pos}")

    return val, pos


def _parse_object_key(s: str, pos: int, d: dict, stack: list) -> tuple[object, int]:
    """Parse the next key:value pair in an object."""
    if pos >= len(s):
        raise ValueError("Unterminated object")

    if s[pos] != '"':
        raise ValueError(f"Expected string key in object, got {s[pos]!r} at position {pos}")

    key, pos = _parse_string(s, pos + 1)
    pos = _skip_ws(s, pos)

    if pos >= len(s) or s[pos] != ':':
        raise ValueError(f"Expected ':' after object key at position {pos}")

    stack.append(("object", s, d, key))
    return _dispatch(s, _skip_ws(s, pos + 1), stack)


def _dispatch(s: str, pos: int, stack: list) -> tuple[object, int]:
    """Inspect the character at pos and begin parsing the appropriate value."""
    pos = _skip_ws(s, pos)

    if pos >= len(s):
        raise ValueError("Unexpected end of JSON input")

    ch = s[pos]

    val: object

    if ch == '{':
        pos = _skip_ws(s, pos + 1)
        if pos >= len(s):
            raise ValueError("Unterminated object")

        if s[pos] == '}':
            return _resume({}, pos + 1, stack)

        return _parse_object_key(s, pos, {}, stack)

    if ch == '[':
        pos = _skip_ws(s, pos + 1)
        if pos >= len(s):
            raise ValueError("Unterminated array")

        if s[pos] == ']':
            return _resume([], pos + 1, stack)

        stack.append(("array", s, []))
        return _dispatch(s, pos, stack)

    if ch == '"':
        val, pos = _parse_string(s, pos + 1)
        return _resume(val, pos, stack)

    if ch == 't':
        val, pos = _parse_keyword(s, pos, 'true', True)
        return _resume(val, pos, stack)

    if ch == 'f':
        val, pos = _parse_keyword(s, pos, 'false', False)
        return _resume(val, pos, stack)

    if ch == 'n':
        val, pos = _parse_keyword(s, pos, 'null', None)
        return _resume(val, pos, stack)

    if ch == '-' or ch.isdigit():
        val, pos = _parse_number(s, pos)
        return _resume(val, pos, stack)

    raise ValueError(f"Unexpected character {ch!r} at position {pos}")


def _run_self_tests() -> None:
    """Run the self-test suite comparing our parser against json.loads."""
    # Large flat array: 100 integers
    flat_array = "[" + ",".join(str(i) for i in range(100)) + "]"

    # Large flat object: 50 key-value pairs
    flat_object = "{" + ",".join(f'"k{i}": {i}' for i in range(50)) + "}"

    # Mixed nested: array of 20 objects, each with a nested array of 5 integers
    mixed_items = []
    for i in range(20):
        vals = ",".join(str(i * 5 + j) for j in range(5))
        active = "true" if i % 2 == 0 else "false"
        mixed_items.append(f'{{"id": {i}, "vals": [{vals}], "active": {active}}}')

    mixed_nested = "[" + ",".join(mixed_items) + "]"

    # String-heavy object: 20 string values with various escapes
    string_pairs = []
    for i in range(20):
        string_pairs.append(f'"field{i}": "value\\nwith\\ttab\\nand\\nnewlines {i}"')

    string_heavy = "{" + ",".join(string_pairs) + "}"

    # Number variety: integers, negative floats, scientific notation
    numbers_array = (
        "[0, -42, 3.14, -3.14, 1.0e10, -2.5e-3, 100, 0.001, 999999, -0.5,"
        " 42.0, 1e5, 6.022e23, -1.602e-19, 12345.6789]"
    )

    # Unicode escapes: strings with multiple \uXXXX sequences
    unicode_strings = (
        '["\\u0041\\u0042\\u0043", "\\u00e9\\u00e8\\u00ea",'
        ' "\\u4e2d\\u6587\\u5b57\\u7b26", "\\u03a0\\u03b1\\u03b9"]'
    )

    tests = [
        '{"name": "Alice", "age": 30, "active": true, "score": 9.5, "tags": ["admin", "user"], '
        '"address": {"city": "Wonderland", "zip": null}}',
        flat_array,
        flat_object,
        mixed_nested,
        string_heavy,
        numbers_array,
        unicode_strings,
        # Long string (2000 chars)
        '"' + ('abcdefghij' * 200) + '"',
        # Deep nesting (500 levels)
        ('[' * 500) + '0' + (']' * 500),
    ]

    all_passed = True
    for t in tests:
        try:
            ours = parse(t)
            theirs = json.loads(t)
            match = ours == theirs
            status = "PASS" if match else "FAIL"
            if not match:
                all_passed = False
                print(f"{status}: {t[:60]!r}")
                print(f"  ours:   {ours!r}")
                print(f"  theirs: {theirs!r}")

            else:
                print(f"{status}: {t[:60]!r}")

        except Exception as e:
            all_passed = False
            print(f"ERROR: {t[:60]!r} -> {e}")

    print()
    print("All passed!" if all_passed else "Some tests FAILED.")


if __name__ == '__main__':
    _run_self_tests()
