"""Tests for string operations and string manipulation functions."""

import pytest

from menai import Menai, MenaiEvalError, VMErrorCode
from menai.bytecode.menai_bytecode import Opcode, unpack_instruction


def _opaque(*bindings: str) -> str:
    """
    Build an expression whose operands are read from struct fields.

    The runtime-opcode tests need operands the compiler cannot resolve to
    constants.  A literal, or a `let` binding of one, is folded away — the
    inliner substitutes the literal into the body — so the opcode under test
    never reaches the bytecode.  A struct field read is never a compile-time
    constant, so the operation stays a runtime instruction.

    The last argument is the body; each preceding argument is a
    `name value` pair binding name to the Menai source literal value.
    """
    body = bindings[-1]
    pairs = [b.split(" ", 1) for b in bindings[:-1]]

    # Each value gets its own single-field struct, so the field read that
    # supplies it is opaque while the value itself stays a literal.
    reads = " ".join(
        f"({name} (struct-get b{index} 'v))"
        for index, (name, _) in enumerate(pairs)
    )
    inner = f"(let ({reads}) {body})" if reads else body

    for index, (_, value) in reversed(list(enumerate(pairs))):
        inner = (
            f"(let ((box{index} (struct (v))))"
            f" (let ((b{index} (box{index} {value})))"
            f" {inner}))"
        )

    return inner


class TestStrings:
    """Test string operations and manipulation functions."""

    @pytest.mark.parametrize("expression,expected", [
        # Basic string literals
        ('"hello"', '"hello"'),
        ('""', '""'),  # Empty string
        ('"hello world"', '"hello world"'),

        # Strings with spaces
        ('"  hello  "', '"  hello  "'),
        ('"\\thello\\n"', '"\\thello\\n"'),  # Tabs and newlines preserved in output
    ])
    def test_string_literals(self, menai, expression, expected):
        """Test basic string literal parsing and formatting."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected_content", [
        # Escape sequences
        ('"\\""', '"'),  # Escaped quote
        ('"\\\\"', '\\'),  # Escaped backslash
        ('"\\n"', '\n'),  # Newline
        ('"\\t"', '\t'),  # Tab
        ('"\\r"', '\r'),  # Carriage return

        # Unicode escape sequences
        ('"\\u0041"', 'A'),  # Unicode A
        ('"\\u03B1"', 'α'),  # Greek alpha
        ('"\\u03B2"', 'β'),  # Greek beta
        ('"\\u03B3"', 'γ'),  # Greek gamma

        # Combined escapes
        ('"Hello\\nWorld"', 'Hello\nWorld'),
        ('"Quote: \\""', 'Quote: "'),
        ('"Path\\\\to\\\\file"', 'Path\\to\\file'),
    ])
    def test_string_escape_sequences(self, menai, expression, expected_content):
        """Test string escape sequence processing."""
        result = menai.evaluate(expression)
        assert result == expected_content

    @pytest.mark.parametrize("unicode_expr,expected_content", [
        # Basic Unicode
        ('"Hello 世界"', 'Hello 世界'),
        ('"Café naïve"', 'Café naïve'),

        # Emoji (4-byte UTF-8)
        ('"Hello 👋"', 'Hello 👋'),
        ('"🌍 World"', '🌍 World'),

        # Mixed ASCII and Unicode
        ('"résumé"', 'résumé'),
        ('"Москва"', 'Москва'),  # Russian
        ('"北京"', '北京'),  # Chinese

        # Unicode via escape sequences
        ('"\\u4F60\\u597D"', '你好'),  # "Hello" in Chinese
    ])
    def test_unicode_string_handling(self, menai, unicode_expr, expected_content):
        """Test Unicode string handling."""
        result = menai.evaluate(unicode_expr)
        assert result == expected_content

    @pytest.mark.parametrize("expression,expected", [
        # Basic string append
        ('(string-concat "hello" " " "world")', '"hello world"'),
        ('(string-concat "a" "b" "c")', '"abc"'),

        # Empty string handling
        ('(string-concat)', '""'),  # Identity case
        ('(string-concat "")', '""'),
        ('(string-concat "" "hello")', '"hello"'),
        ('(string-concat "hello" "")', '"hello"'),

        # Many arguments
        ('(string-concat "a" "b" "c" "d" "e")', '"abcde"'),

        # Unicode strings
        ('(string-concat "Hello " "世界")', '"Hello 世界"'),
    ])
    def test_string_append(self, menai, expression, expected):
        """Test string-concat operation."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # Basic string length
        ('(string-length "hello")', '5'),
        ('(string-length "")', '0'),
        ('(string-length "a")', '1'),

        # Unicode length (character count, not byte count)
        ('(string-length "世界")', '2'),
        ('(string-length "café")', '4'),
        ('(string-length "👋")', '1'),  # Single emoji

        # Strings with escape sequences
        ('(string-length "hello\\nworld")', '11'),  # Includes the newline
        ('(string-length "\\t")', '1'),  # Tab is one character
    ])
    def test_string_length(self, menai, expression, expected):
        """Test string-length function."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # Basic string-slice (3-argument form)
        ('(string-slice "hello" 1 4)', '"ell"'),  # Indices 1, 2, 3
        ('(string-slice "hello" 0 5)', '"hello"'),  # Full string
        ('(string-slice "hello" 0 1)', '"h"'),  # First character
        ('(string-slice "hello" 4 5)', '"o"'),  # Last character

        # Empty string-slice
        ('(string-slice "hello" 2 2)', '""'),  # Empty range
        ('(string-slice "hello" 0 0)', '""'),  # Empty range at start

        # Unicode string-slices
        ('(string-slice "世界你好" 1 3)', '"界你"'),
        ('(string-slice "café" 1 3)', '"af"'),
    ])
    def test_string_slice(self, menai, expression, expected):
        """Test string-slice function (3-argument form)."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # string-slice 2-argument form: (string-slice str start) -> from start to end
        ('(string-slice "hello" 2)', '"llo"'),    # from index 2 to end
        ('(string-slice "hello" 0)', '"hello"'),  # full string
        ('(string-slice "hello" 5)', '""'),        # start at end -> empty
        ('(string-slice "hello" 1)', '"ello"'),   # from index 1 to end
        ('(string-slice "hello" 4)', '"o"'),       # last character only
    ])
    def test_string_slice_two_arg_form(self, menai, expression, expected):
        """Test string-slice function (2-argument form: from index to end of string)."""
        assert menai.evaluate_and_format(expression) == expected

    def test_string_slice_index_errors(self, menai):
        """Test string-slice with invalid indices."""
        # Index out of range should be handled gracefully or raise error
        # Let's test the current behavior
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-slice "hello" 0 10)')  # End beyond string

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-slice "hello" -1 3)')  # Negative start

    def test_string_slice_negative_end_index(self, menai):
        """Test string-slice with negative end index."""
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "hello" 1 -1)')
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "test" 0 -5)')
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "" 0 -1)')  # Empty string with negative end
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

    def test_string_slice_start_index_out_of_range(self, menai):
        """Test string-slice with start index beyond string length."""
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "hello" 10 15)')  # start_idx > string_len
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "hi" 5 6)')  # start_idx > string_len
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "" 1 2)')  # start_idx > empty string length
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

        # Test with Unicode strings
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "世界" 5 6)')  # start beyond Unicode string
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

    def test_string_slice_start_greater_than_end(self, menai):
        """Test string-slice with start index greater than end index."""
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "hello" 3 1)')  # start > end
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_AFTER_END

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "test" 4 2)')  # start > end
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_AFTER_END

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "world" 5 0)')  # start > end
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_AFTER_END

        # Test with Unicode strings
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "café" 3 1)')  # start > end with Unicode
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_AFTER_END

    def test_string_slice_edge_cases_comprehensive(self, menai):
        """Test comprehensive edge cases for string-slice validation."""
        # Valid boundary cases (should work)
        assert menai.evaluate_and_format('(string-slice "hello" 5 5)') == '""'  # start == end == length
        assert menai.evaluate_and_format('(string-slice "hello" 0 0)') == '""'  # start == end == 0
        assert menai.evaluate_and_format('(string-slice "hello" 2 2)') == '""'  # start == end in middle

        # Test validation order precedence
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "test" -1 10)')  # start negative, end out of range
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "test" 0 -1)')  # start valid, end negative
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "test" 10 15)')  # both out of range, start checked first
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

    def test_string_slice_with_different_string_lengths(self, menai):
        """Test error conditions with various string lengths."""
        # Empty string
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "" 1 1)')
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

        # Single character
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "a" 2 3)')
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_OUT_OF_RANGE

        # Long string
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "abcdefghijklmnop" 10 5)')
        assert exc_info.value.error_code == VMErrorCode.SLICE_START_AFTER_END

        # Multiple violations - should catch first one encountered
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(string-slice "hello" -1 -2)')  # Both negative, start checked first
        assert exc_info.value.error_code == VMErrorCode.NEGATIVE_SLICE_INDEX

    @pytest.mark.parametrize("expression,expected", [
        # String case conversion
        ('(string-upcase "hello")', '"HELLO"'),
        ('(string-upcase "Hello World")', '"HELLO WORLD"'),
        ('(string-upcase "")', '""'),
        ('(string-upcase "ALREADY UPPER")', '"ALREADY UPPER"'),

        ('(string-downcase "HELLO")', '"hello"'),
        ('(string-downcase "Hello World")', '"hello world"'),
        ('(string-downcase "")', '""'),
        ('(string-downcase "already lower")', '"already lower"'),

        # Unicode case conversion
        ('(string-upcase "café")', '"CAFÉ"'),
        ('(string-downcase "CAFÉ")', '"café"'),
    ])
    def test_string_case_conversion(self, menai, expression, expected):
        """Test string case conversion functions."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # String character reference
        ('(string-nth "hello" 0)', '"h"'),
        ('(string-nth "hello" 1)', '"e"'),
        ('(string-nth "hello" 4)', '"o"'),

        # Unicode character reference
        ('(string-nth "世界" 0)', '"世"'),
        ('(string-nth "世界" 1)', '"界"'),
        ('(string-nth "café" 3)', '"é"'),
    ])
    def test_string_nth(self, menai, expression, expected):
        """Test string-nth function."""
        assert menai.evaluate_and_format(expression) == expected

    def test_string_nth_index_errors(self, menai):
        """Test string-nth with invalid indices."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-nth "hello" 5)')  # Index out of range

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-nth "hello" -1)')  # Negative index

    @pytest.mark.parametrize("expression,expected", [
        ('(string-with "hello" 0 "H")', '"Hello"'),
        ('(string-with "hello" 4 "O")', '"hellO"'),
        ('(string-with "hello" 1 "x")', '"hxllo"'),
        ('(string-with "" 0 "x")', None),

        # The replacement is a codepoint, so a surrogate pair counts as one.
        ('(string-with "😀a" 0 "z")', '"za"'),
        ('(string-with "café" 3 "e")', '"cafe"'),
    ])
    def test_string_with(self, menai, expression, expected):
        """string-with replaces the codepoint at an index."""
        if expected is None:
            with pytest.raises(MenaiEvalError):
                menai.evaluate(expression)

            return

        assert menai.evaluate_and_format(expression) == expected

    def test_string_with_preserves_length(self, menai):
        """string-with returns a string of the same codepoint length."""
        assert menai.evaluate('(string-length (string-with "😀ab" 1 "z"))') == 3

    def test_string_with_leaves_the_source_unchanged(self, menai):
        """A string is immutable, so replacing an element does not affect the source."""
        assert menai.evaluate('(let ((s "hello")) (list (string-with s 1 "x") s))') == [
            "hxllo", "hello",
        ]

    def test_string_with_index_errors(self, menai):
        """An out-of-range index raises, matching string-nth."""
        with pytest.raises(MenaiEvalError, match="index out of range"):
            menai.evaluate('(string-with "hello" 5 "x")')

        with pytest.raises(MenaiEvalError, match="index out of range"):
            menai.evaluate('(string-with "hello" -1 "x")')

    @pytest.mark.parametrize("replacement", ['""', '"xy"'])
    def test_string_with_requires_a_single_character(self, menai, replacement):
        """The replacement must be one codepoint, like everything else a string holds."""
        with pytest.raises(MenaiEvalError, match="single-character"):
            menai.evaluate(f'(string-with "hello" 1 {replacement})')

    def test_string_with_rewrites_a_string_in_a_loop(self, menai):
        """A uniquely-owned accumulator is rewritten in place and stays hashable."""
        expression = (
            '(let ((fill (lambda (s i n)'
            '              (if (integer>=? i n) s (fill (string-with s i "b") (integer+ i 1) n)))))'
            '  (let ((r (fill "aaaa" 0 4)))'
            '    (list r (string=? r "bbbb") (dict-get (dict r 1) "bbbb") (set-member? (set r) "bbbb"))))'
        )
        assert menai.evaluate(expression) == ["bbbb", True, 1, True]

    @pytest.mark.parametrize("expression,expected", [
        # String index search
        ('(string-index "hello world" "world")', '6'),
        ('(string-index "hello world" "hello")', '0'),
        ('(string-index "hello world" "o w")', '4'),
        ('(string-index "hello world" "xyz")', '#none'),
        ('(string-index "hello" "")', '0'),  # Empty string found at start
        ('(string-index "" "")', '0'),  # Empty found in empty at start
        ('(string-index "" "a")', '#none'),  # Non-empty not found in empty

        # Unicode index
        ('(string-index "Hello 世界" "世界")', '6'),
        ('(string-index "café" "fé")', '2'),
    ])
    def test_string_index(self, menai, expression, expected):
        """Test string-index function."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # First character inside the set
        ('(string-index-any "hello world" (vector 32 32))', '5'),

        # No character inside the set
        ('(string-index-any "hello" (vector 48 57))', '#none'),

        # The earliest position wins across several ranges
        ('(string-index-any "abcdef" (vector 101 101 99 99))', '2'),

        # Adjacent ranges behave as one range
        ('(string-index-any "abcd" (vector 97 97 98 98))', '0'),
        ('(string-index-any "xyz" (vector 97 97 98 98))', '#none'),

        # Overlapping ranges do not change the answer
        ('(string-index-any "abcdef" (vector 97 101 99 102))', '0'),
        ('(string-index-any "zeb" (vector 97 101 99 102))', '1'),

        # Empty subject
        ('(string-index-any "" (vector 0 1114111))', '#none'),

        # A range reaching the last codepoint
        ('(string-index-any "abc" (vector 1114111 1114111))', '#none'),
        ('(string-index-any (string-concat "ab" (integer-codepoint->string 1114111)) (vector 1114111 1114111))', '2'),

        # Non-ASCII ranges
        ('(string-index-any "x\u03b1\u03b2" (vector 945 969))', '1'),
        ('(string-index-any "hello" (vector 945 969))', '#none'),

        # Indices are codepoints, not bytes: an astral character is one position
        ('(string-index-any (string-concat "a" (integer-codepoint->string 128512) "b") (vector 98 98))', '2'),
        ('(string-index-any (string-concat "a" (integer-codepoint->string 128512) "b") (vector 128512 128512))', '1'),
    ])
    def test_string_index_any(self, menai, expression, expected):
        """Test string-index-any character-set search."""
        assert menai.evaluate_and_format(expression) == expected

    def test_string_index_any_is_an_opcode(self, menai):
        """Test that string-index-any compiles to its own opcode rather than a helper."""
        code = menai.compile('(string-index-any "hello world" (vector 32 32))')
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_INDEX_ANY in ops, \
            "Expected STRING_INDEX_ANY in bytecode for the character-set search"

    @pytest.mark.parametrize("expression,expected", [
        # String prefix predicate
        ('(string-prefix? "hello world" "hello")', '#t'),
        ('(string-prefix? "hello world" "he")', '#t'),
        ('(string-prefix? "hello world" "")', '#t'),  # Empty prefix
        ('(string-prefix? "hello world" "world")', '#f'),  # Not at start
        ('(string-prefix? "hello" "hello world")', '#f'),  # Prefix longer than string

        # Unicode prefix
        ('(string-prefix? "世界你好" "世界")', '#t'),
        ('(string-prefix? "café" "ca")', '#t'),
    ])
    def test_string_prefix(self, menai, expression, expected):
        """Test string-prefix? predicate."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # String suffix predicate
        ('(string-suffix? "hello world" "world")', '#t'),
        ('(string-suffix? "hello world" "ld")', '#t'),
        ('(string-suffix? "hello world" "")', '#t'),  # Empty suffix
        ('(string-suffix? "hello world" "hello")', '#f'),  # Not at end
        ('(string-suffix? "hello" "hello world")', '#f'),  # Suffix longer than string

        # Unicode suffix
        ('(string-suffix? "世界你好" "你好")', '#t'),
        ('(string-suffix? "café" "é")', '#t'),
    ])
    def test_string_suffix(self, menai, expression, expected):
        """Test string-suffix? predicate."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # Prefix matches
        (_opaque('s "hello world"', 'p "hello"', '($string-prefix? s p)'), '#t'),
        (_opaque('s "hello world"', 'p "he"', '($string-prefix? s p)'), '#t'),
        (_opaque('s "hello world"', 'p ""', '($string-prefix? s p)'), '#t'),  # Empty prefix
        # Prefix non-matches
        (_opaque('s "hello world"', 'p "world"', '($string-prefix? s p)'), '#f'),  # Not at start
        (_opaque('s "hello"', 'p "hello world"', '($string-prefix? s p)'), '#f'),  # Prefix longer
        # Unicode prefix
        (_opaque('s "世界你好"', 'p "世界"', '($string-prefix? s p)'), '#t'),
        (_opaque('s "café"', 'p "ca"', '($string-prefix? s p)'), '#t'),
    ])
    def test_string_prefix_runtime(self, menai, expression, expected):
        """Test string-prefix? opcode directly (not constant-folded)."""
        code = menai.compile(expression)
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_PREFIX_P in ops, \
            f"Expected STRING_PREFIX_P in bytecode for {expression!r}"
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # Suffix matches
        (_opaque('s "hello world"', 'su "world"', '($string-suffix? s su)'), '#t'),
        (_opaque('s "hello world"', 'su "ld"', '($string-suffix? s su)'), '#t'),
        (_opaque('s "hello world"', 'su ""', '($string-suffix? s su)'), '#t'),  # Empty suffix
        # Suffix non-matches
        (_opaque('s "hello world"', 'su "hello"', '($string-suffix? s su)'), '#f'),  # Not at end
        (_opaque('s "hello"', 'su "hello world"', '($string-suffix? s su)'), '#f'),  # Suffix longer
        # Unicode suffix
        (_opaque('s "世界你好"', 'su "你好"', '($string-suffix? s su)'), '#t'),
        (_opaque('s "café"', 'su "é"', '($string-suffix? s su)'), '#t'),
    ])
    def test_string_suffix_runtime(self, menai, expression, expected):
        """Test string-suffix? opcode directly (not constant-folded)."""
        code = menai.compile(expression)
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_SUFFIX_P in ops, \
            f"Expected STRING_SUFFIX_P in bytecode for {expression!r}"
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # String equality predicate
        ('(string=? "hello" "hello")', '#t'),
        ('(string=? "hello" "world")', '#f'),
        ('(string=? "" "")', '#t'),

        # Multiple arguments (all must be equal)
        ('(string=? "hello" "hello" "hello")', '#t'),
        ('(string=? "hello" "hello" "world")', '#f'),

        # Case sensitive
        ('(string=? "Hello" "hello")', '#f'),

        # Unicode equality
        ('(string=? "世界" "世界")', '#t'),
        ('(string=? "café" "cafe")', '#f'),  # Different characters
    ])
    def test_string_equality(self, menai, expression, expected):
        """Test string=? predicate."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # String to number conversion
        ('(string->number "42")', '42'),
        ('(string->number "3.14")', '3.14'),
        ('(string->number "-5")', '-5'),
        ('(string->number "0")', '0'),

        # Scientific notation
        ('(string->number "1e2")', '100.0'),
        ('(string->number "1.5e-2")', '0.015'),

        # Surrounding whitespace is accepted (trimmed before parsing)
        ('(string->number " 42 ")', '42'),
        ('(string->number "  3.14  ")', '3.14'),

        # Complex numbers
        ('(string->number "1+2j")', '1+2j'),
        ('(string->number "3j")', '3j'),

        # Base-prefixed literals are no longer supported — return #none
        ('(string->number "#xff")', '#none'),
        ('(string->number "#b1010")', '#none'),
        ('(string->number "#o755")', '#none'),

        # Upper case prefixes also return #none
        ('(string->number "#XFF")', '#none'),
        ('(string->number "#B1010")', '#none'),
        ('(string->number "#O755")', '#none'),

        # Negative base-prefixed literals also return #none
        ('(string->number "-#xff")', '#none'),
        ('(string->number "-#b1010")', '#none'),
        ('(string->number "-#o755")', '#none'),

        # Unparseable strings return #f
        ('(string->number "hello")', '#none'),
        ('(string->number "12.34.56")', '#none'),
        ('(string->number "")', '#none'),
        ('(string->number "#xZZ")', '#none'),
        ('(string->number "#b2")', '#none'),
        ('(string->number "#o9")', '#none'),
    ])
    def test_string_to_number(self, menai, expression, expected):
        """Test string->number conversion."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(string->float "3.14")', '3.14'),
        ('(string->float "-5.5")', '-5.5'),
        ('(string->float "0")', '0.0'),
        ('(string->float "1e2")', '100.0'),
        ('(string->float "  42  ")', '42.0'),

        # Strings that are not floats return #none
        ('(string->float "hello")', '#none'),
        ('(string->float "")', '#none'),
        ('(string->float "1+2j")', '#none'),
        ('(string->float "12.34.56")', '#none'),
    ])
    def test_string_to_float(self, menai, expression, expected):
        """Test string->float conversion."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        (_opaque('s "ff"', 'r 16', '($string->integer s r)'), '255'),
        (_opaque('s "1010"', 'r 2', '($string->integer s r)'), '10'),
        (_opaque('s "377"', 'r 8', '($string->integer s r)'), '255'),
        (_opaque('s "42"', 'r 10', '($string->integer s r)'), '42'),
        (_opaque('s "-5"', 'r 10', '($string->integer s r)'), '-5'),
        (_opaque('s "  42  "', 'r 10', '($string->integer s r)'), '42'),
        (_opaque('s "xyz"', 'r 10', '($string->integer s r)'), '#none'),
        (_opaque('s "0xff"', 'r 16', '($string->integer s r)'), '#none'),
        (_opaque('s ""', 'r 10', '($string->integer s r)'), '#none'),
    ])
    def test_string_to_integer_runtime(self, menai, expression, expected):
        """Test the $string->integer opcode directly (not constant-folded)."""
        code = menai.compile(expression)
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_TO_INTEGER in ops, \
            f"Expected STRING_TO_INTEGER in bytecode for {expression!r}"
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        (_opaque('s "3.14"', '($string->float s)'), '3.14'),
        (_opaque('s "-5.5"', '($string->float s)'), '-5.5'),
        (_opaque('s "0"', '($string->float s)'), '0.0'),
        (_opaque('s "1e2"', '($string->float s)'), '100.0'),
        (_opaque('s "  42  "', '($string->float s)'), '42.0'),
        (_opaque('s "hello"', '($string->float s)'), '#none'),
        (_opaque('s ""', '($string->float s)'), '#none'),
        (_opaque('s "1+2j"', '($string->float s)'), '#none'),
        (_opaque('s "12.34.56"', '($string->float s)'), '#none'),
    ])
    def test_string_to_float_runtime(self, menai, expression, expected):
        """Test the $string->float opcode directly (not constant-folded)."""
        code = menai.compile(expression)
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_TO_FLOAT in ops, \
            f"Expected STRING_TO_FLOAT in bytecode for {expression!r}"
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        (_opaque('s "1+2j"', '($string->complex s)'), '1+2j'),
        (_opaque('s "3j"', '($string->complex s)'), '3j'),
        (_opaque('s "2.5-0.5j"', '($string->complex s)'), '2.5-0.5j'),
        (_opaque('s " 1+2j "', '($string->complex s)'), '1+2j'),
        (_opaque('s "1.5"', '($string->complex s)'), '1.5+0j'),
        (_opaque('s "hello"', '($string->complex s)'), '#none'),
        (_opaque('s ""', '($string->complex s)'), '#none'),
        (_opaque('s "junk+2j"', '($string->complex s)'), '#none'),
    ])
    def test_string_to_complex_runtime(self, menai, expression, expected):
        """Test the $string->complex opcode directly (not constant-folded)."""
        code = menai.compile(expression)
        ops = [unpack_instruction(w).opcode for w in code.instructions]
        assert Opcode.STRING_TO_COMPLEX in ops, \
            f"Expected STRING_TO_COMPLEX in bytecode for {expression!r}"
        assert menai.evaluate_and_format(expression) == expected

    def test_string_to_float_type_error(self, menai):
        """Test string->float raises on non-string argument (type error, not parse failure)."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->float 42)')

    @pytest.mark.parametrize("expression,expected", [
        ('(string->complex "1+2j")', '1+2j'),
        ('(string->complex "3j")', '3j'),
        ('(string->complex "2.5-0.5j")', '2.5-0.5j'),
        ('(string->complex " 1+2j ")', '1+2j'),
        ('(string->complex "1.5")', '1.5+0j'),

        # Strings that are not complex numbers return #none
        ('(string->complex "hello")', '#none'),
        ('(string->complex "")', '#none'),
        ('(string->complex "junk+2j")', '#none'),
    ])
    def test_string_to_complex(self, menai, expression, expected):
        """Test string->complex conversion."""
        assert menai.evaluate_and_format(expression) == expected

    def test_string_to_complex_type_error(self, menai):
        """Test string->complex raises on non-string argument (type error, not parse failure)."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->complex 42)')

    def test_string_to_number_type_error(self, menai):
        """Test string->number raises on non-string argument (type error, not parse failure)."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->number 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->number (list 1 2))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->number #t)')

    @pytest.mark.parametrize("expression,expected", [
        # Number to string conversion
        ('(integer->string 42)', '"42"'),
        ('(float->string 3.14)', '"3.14"'),
        ('(integer->string -5)', '"-5"'),
        ('(integer->string 0)', '"0"'),

        # Complex numbers
        ('(complex->string (float->complex 1.0 2.0))', '"1+2j"'),
        ('(complex->string 1j)', '"1j"'),

        # number->string dispatches on type
        ('(number->string 42)', '"42"'),
        ('(number->string 3.14)', '"3.14"'),
        ('(number->string (float->complex 1.0 2.0))', '"1+2j"'),
        ('(number->string -5)', '"-5"'),
        ('(number->string 0)', '"0"'),
    ])
    def test_number_to_string(self, menai, expression, expected):
        """Test number to string conversions."""
        assert menai.evaluate_and_format(expression) == expected

    def test_number_to_string_type_error(self, menai):
        """Test number->string raises on non-number argument."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(number->string "hello")')
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(number->string (list 1 2))')
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(number->string #t)')

    @pytest.mark.parametrize("expression,expected", [
        # String to list conversion
        ('(string->list "hello")', '("h" "e" "l" "l" "o")'),
        ('(string->list "")', '()'),
        ('(string->list "a")', '("a")'),

        # Unicode string to list
        ('(string->list "世界")', '("世" "界")'),
        ('(string->list "café")', '("c" "a" "f" "é")'),
    ])
    def test_string_to_list(self, menai, expression, expected):
        """Test string->list conversion."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # List to string conversion
        ('(list->string (list "h" "e" "l" "l" "o"))', '"hello"'),
        ('(list->string (list))', '""'),
        ('(list->string (list "a"))', '"a"'),

        # Unicode list to string
        ('(list->string (list "世" "界"))', '"世界"'),
        ('(list->string (list "c" "a" "f" "é"))', '"café"'),
    ])
    def test_list_to_string(self, menai, expression, expected):
        """Test list->string conversion."""
        assert menai.evaluate_and_format(expression) == expected

    def test_list_to_string_non_string_elements(self, menai):
        """Test list->string with non-string elements."""
        # Should accept and convert non-string elements
        result = menai.evaluate_and_format('(list->string (list "h" "i"))')
        assert result == '"hi"'

    @pytest.mark.parametrize("expression,expected", [
        # String split
        ('(string->list "a,b,c" ",")', '("a" "b" "c")'),
        ('(string->list "hello world" " ")', '("hello" "world")'),
        ('(string->list "one::two::three" "::")', '("one" "two" "three")'),

        # Edge cases
        ('(string->list "" ",")', '("")'),  # Empty string splits to list with empty string
        ('(string->list "hello" "")', '("h" "e" "l" "l" "o")'),  # Split on empty delimiter
        ('(string->list "no-delimiters" ",")', '("no-delimiters")'),  # No delimiter found

        # Unicode split
        ('(string->list "世界,你好" ",")', '("世界" "你好")'),
    ])
    def test_string_split(self, menai, expression, expected):
        """Test string->list function."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        # String join
        ('(list->string (list "hello" "world") " ")', '"hello world"'),
        ('(list->string (list "a" "b" "c") ",")', '"a,b,c"'),
        ('(list->string (list "one" "two" "three") "::")', '"one::two::three"'),

        # Edge cases
        ('(list->string (list) ",")', '""'),  # Empty list
        ('(list->string (list "hello") ",")', '"hello"'),  # Single element
        ('(list->string (list "a" "b") "")', '"ab"'),  # Empty separator

        # Unicode join
        ('(list->string (list "世界" "你好") " ")', '"世界 你好"'),
    ])
    def test_string_join(self, menai, expression, expected):
        """Test list->string function."""
        assert menai.evaluate_and_format(expression) == expected

    def test_string_join_non_string_list(self, menai):
        """Test list->string with non-string list elements."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(list->string (list 1 2 3) ",")')

    def test_string_operations_type_validation(self, menai):
        """Test that string operations reject non-string arguments."""
        # string-concat with non-strings
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-concat "hello" 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-concat #t "world")')

        # string-length with non-string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-length 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-length (list "h" "i"))')

        # string-slice with non-string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-slice 42 0 1)')

        # string-upcase/downcase with non-string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-upcase 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-downcase #t)')

    def test_string_index_type_validation(self, menai):
        """Test that string-index requires string arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index 42 "hello")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-prefix? "hello" 42)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-suffix? #t "world")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string=? "hello" 42)')

    def test_string_index_any_invalid_sets(self, menai):
        """Test that string-index-any rejects a malformed character set."""
        # An empty set is a caller bug, not "matches nothing"
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector))')

        # Ranges are flat (lo hi) pairs, so the count must be even
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector 97 97 98))')

        # lo must not exceed hi
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector 98 97))')

        # Every bound must be a codepoint
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector -1 5))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector 0 1114112))')

        # Every element must be an integer
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (vector "a" "z"))')

    def test_string_index_any_type_validation(self, menai):
        """Test that string-index-any requires a string subject and a vector set."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any 42 (vector 97 97))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index-any "hello" (list 97 97))')

    def test_string_prefix_suffix_type_validation_runtime(self, menai):
        """Test that string-prefix? and string-suffix? opcodes reject non-string arguments."""
        # Non-string first argument
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((s 42) (p "hello")) ($string-prefix? s p))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((s #t) (p "world")) ($string-suffix? s p))')

        # Non-string second argument
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((s "hello") (p 42)) ($string-prefix? s p))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(let ((s "hello") (p #t)) ($string-suffix? s p))')

    def test_string_conversion_type_validation(self, menai):
        """Test that string conversion functions validate argument types."""
        # string->number requires string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->number 42)')

        # integer->string requires number
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(integer->string "hello")')

        # float->string requires number
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(float->string "hello")')

        # complex->string requires number
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(complex->string "hello")')

        # string->list requires string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->list 42)')

        # list->string requires list
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(list->string "hello")')

    def test_string_split_join_type_validation(self, menai):
        """Test that string->list and list->string validate argument types."""
        # string->list requires strings
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->list 42 ",")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string->list "hello" 42)')

        # list->string requires list and string
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(list->string "hello" ",")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(list->string (list "a" "b") 42)')

    def test_string_arity_validation(self, menai):
        """Test that string functions validate argument counts."""
        # Functions requiring exactly 1 argument
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-length)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-length "hello" "world")')

        # Functions requiring exactly 2 arguments
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-index "hello")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-nth "hello")')

        # string-slice requires 2 or 3 arguments (not 1)
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(string-slice "hello")')

    def test_round_trip_string_list_conversion(self, menai):
        """Test round-trip conversion between strings and lists."""
        test_strings = [
            '"hello"',
            '"world"',
            '""',
            '"café"',
            '"世界"',
        ]

        for string_expr in test_strings:
            # string -> list -> string should be identity
            round_trip = f'(list->string (string->list {string_expr}))'
            result = menai.evaluate_and_format(round_trip)
            assert result == menai.evaluate_and_format(string_expr)

    def test_complex_string_operations(self, menai, helpers):
        """Test complex combinations of string operations."""
        # Split, process, and rejoin
        complex_expr = '''
        (list->string
          (map (lambda (s) (string-upcase s))
               (string->list "hello,world,test" ","))
          " | ")
        '''
        # This requires lambda and map to work, so it's more of an integration test
        # For now, let's test a simpler combination
        helpers.assert_evaluates_to(
            menai,
            '(string-concat (string-upcase "hello") " " (string-downcase "WORLD"))',
            '"HELLO world"'
        )

        # Nested string operations
        helpers.assert_evaluates_to(
            menai,
            '(string-length (string-concat "hello" " " "world"))',
            '11'
        )

        # String operations with conversions
        helpers.assert_evaluates_to(
            menai,
            '(string-concat "Count: " (integer->string 42))',
            '"Count: 42"'
        )


class TestStringConcatInPlace:
    """
    Test that appending to a string preserves purity.

    The VM appends in place when the receiver is uniquely owned, so these cases
    pin the conditions under which it must not: a value that is still observable,
    a value whose hash has already been taken, and growth past the buffer's
    capacity.
    """

    def test_append_leaves_an_observable_value_unchanged(self, menai):
        """A string that is still named is not modified by a concat onto it."""
        expression = _opaque(
            's "ab"',
            "(list s (string-concat s (string-upcase \"c\")))",
        )
        assert menai.evaluate_and_format(expression) == '("ab" "abC")'

    def test_append_leaves_a_live_binding_unchanged(self, menai):
        """Two concats from one value each produce their own result."""
        expression = (
            '(let ((keep (string-upcase "x")))'
            '  (let ((a (string-concat keep (integer->string 1)))'
            '        (b (string-concat keep (integer->string 2))))'
            '    (list keep a b)))'
        )
        assert menai.evaluate(expression) == ["X", "X1", "X2"]

    def test_append_in_a_loop_builds_every_piece_in_order(self, menai):
        """A loop-carried accumulator appends each piece in order."""
        expression = (
            '(let ((loop (lambda (i acc)'
            '             (if (integer=? i 6)'
            '                 acc'
            '                 (loop (integer+ i 1) (string-concat acc (integer->string i)))))))'
            '  (loop 0 ""))'
        )
        assert menai.evaluate(expression) == "012345"

    def test_three_argument_concat_accumulates_in_order(self, menai):
        """A variadic concat folds through the same opcode, in order."""
        expression = (
            '(let ((loop (lambda (i acc)'
            '             (if (integer=? i 4)'
            '                 acc'
            '                 (loop (integer+ i 1) (string-concat acc "a" (integer->string i)))))))'
            '  (loop 0 ""))'
        )
        assert menai.evaluate(expression) == "a0a1a2a3"

    def test_append_of_a_string_to_itself(self, menai):
        """Concatenating a value with itself doubles it and leaves the original."""
        expression = _opaque('s "ab"', "(list s (string-concat s s))")
        assert menai.evaluate_and_format(expression) == '("ab" "abab")'

    def test_appended_value_hashes_as_its_new_contents(self, menai):
        """A value grown in place is found by a lookup keyed on its new contents."""
        expression = (
            '(let ((loop (lambda (i acc)'
            '             (if (integer=? i 3) acc (loop (integer+ i 1) (string-concat acc "z"))))))'
            '  (set-member? (list->set (list (loop 0 ""))) "zzz"))'
        )
        assert menai.evaluate(expression) is True

    def test_grown_value_still_works_as_a_dict_key(self, menai):
        """A value grown past its buffer's capacity is still a usable key."""
        expression = (
            '(let ((loop (lambda (i acc)'
            '             (if (integer=? i 40) acc (loop (integer+ i 1) (string-concat acc "q"))))))'
            '  (dict-get (dict-with (dict) (loop 0 "") 7) (loop 0 "") #none))'
        )
        assert menai.evaluate(expression) == 7

    def test_append_of_wide_characters_grows_correctly(self, menai):
        """Characters outside the Basic Multilingual Plane survive in-place growth."""
        expression = (
            '(let ((loop (lambda (i acc)'
            '             (if (integer=? i 4) acc (loop (integer+ i 1) (string-concat acc "\U0001f600"))))))'
            '  (string-length (loop 0 "")))'
        )
        assert menai.evaluate(expression) == 4


class TestStringSliceViews:
    """
    Test that a string slice or trim shares the codepoints of the value it came from.

    A slice is a view into its source rather than a copy, so these cases pin the
    behaviour that representation makes it possible to break: a slice reading only
    its own range, a source staying unchanged when a slice of it is grown, and a
    view comparing and hashing as the codepoints it covers.
    """

    def test_slice_reads_only_its_own_range(self, menai):
        """A slice of a live value reads its range of that value's codepoints."""
        expression = _opaque('s "abcdef"', "(list (string-slice s 1 3) s)")
        assert menai.evaluate(expression) == ["bc", "abcdef"]

    def test_append_to_the_source_leaves_a_slice_unchanged(self, menai):
        """Appending to a value does not disturb a slice taken from it."""
        expression = _opaque(
            's "abcdef"',
            '(list (string-slice s 1 3) (string-concat s (string-upcase "!")) s)',
        )
        assert menai.evaluate(expression) == ["bc", "abcdef!", "abcdef"]

    def test_slice_of_a_slice_yields_the_inner_range(self, menai):
        """A slice of a slice is indexed from the inner slice's first codepoint."""
        expression = _opaque('s "abcdef"', "(string-slice (string-slice s 1 5) 1 3)")
        assert menai.evaluate(expression) == "cd"

    def test_slice_of_a_trimmed_value(self, menai):
        """Slicing a trimmed value indexes from the first surviving codepoint."""
        expression = _opaque('s "  padded  "', "(string-slice (string-trim s) 0 3)")
        assert menai.evaluate(expression) == "pad"

    def test_append_to_a_slice_leaves_the_source_unchanged(self, menai):
        """Growing a slice in a loop produces its own value and not the source's."""
        expression = _opaque(
            's "ABCDEF"',
            "(list s"
            " (let ((loop (lambda (i acc)"
            '              (if (integer=? i 3) acc (loop (integer+ i 1) (string-concat acc "z"))))))'
            "   (loop 0 (string-slice s 1 3))))",
        )
        assert menai.evaluate(expression) == ["ABCDEF", "BCzzz"]

    def test_trim_reads_only_the_untrimmed_range(self, menai):
        """Each trim removes whitespace from its own end of the value."""
        expression = _opaque(
            's "  padded  "',
            "(list (string-trim s) (string-trim-left s) (string-trim-right s) s)",
        )
        assert menai.evaluate(expression) == ["padded", "padded  ", "  padded", "  padded  "]

    def test_trim_of_a_trimmed_value_is_unchanged(self, menai):
        """Trimming an already-trimmed value yields the same contents."""
        expression = _opaque('s "  padded  "', "(string-trim (string-trim (string-trim s)))")
        assert menai.evaluate(expression) == "padded"

    def test_trim_of_whitespace_only_is_empty(self, menai):
        """A value of only whitespace trims to an empty string at either end."""
        expression = _opaque(
            's "   "',
            "(list (string-trim s)"
            " (string-length (string-trim s))"
            " (string-trim-left s)"
            " (string-trim-right s))",
        )
        assert menai.evaluate(expression) == ["", 0, "", ""]

    def test_slice_of_an_empty_range(self, menai):
        """An empty range at any valid position is an empty string."""
        expression = _opaque(
            's "hello"',
            "(list (string-slice s 5 5) (string-slice s 2 2) (string-slice s 0 0))",
        )
        assert menai.evaluate(expression) == ["", "", ""]

    def test_slice_of_the_whole_value(self, menai):
        """A range covering the whole value yields all of its codepoints."""
        expression = _opaque(
            's "hello"',
            "(list (string-slice s 0 5) (string-slice s 0) (string-trim s))",
        )
        assert menai.evaluate(expression) == ["hello", "hello", "hello"]

    def test_slice_of_wide_characters(self, menai):
        """Codepoints outside the Basic Multilingual Plane are sliced whole."""
        expression = _opaque(
            's "😀a😀b"',
            "(list (string-slice s 1 3) (string-length (string-slice s 1 3)))",
        )
        assert menai.evaluate(expression) == ["a\U0001f600", 2]

    def test_slice_compares_as_its_own_codepoints(self, menai):
        """A slice compares and measures as the codepoints it covers."""
        expression = _opaque(
            's "abcdef"',
            "(list (string=? (string-slice s 1 3) \"bc\")"
            "      (string<? (string-slice s 1 3) \"bd\")"
            "      (string-length (string-slice s 1 3)))",
        )
        assert menai.evaluate(expression) == [True, True, 2]

    def test_trimmed_value_hashes_as_its_trimmed_contents(self, menai):
        """A trimmed value is found in a set by its trimmed contents."""
        expression = _opaque(
            's "  key  "',
            '(set-member? (list->set (list (string-trim s))) "key")',
        )
        assert menai.evaluate(expression) is True

    def test_sliced_value_works_as_a_dict_key(self, menai):
        """A slice is a usable key and is found by the codepoints it covers."""
        expression = _opaque(
            's "  key  "',
            '(dict-get (dict-with (dict) (string-slice s 2 5) 7) "key" #none)',
        )
        assert menai.evaluate(expression) == 7

    def test_slices_combine_into_one_value(self, menai):
        """Joining trimmed views yields the codepoints each of them covers."""
        expression = _opaque(
            's "  alpha  beta  "',
            '(list->string (list (string-trim (string-slice s 0 9))'
            '                     (string-trim (string-slice s 7 14))) "-")',
        )
        assert menai.evaluate(expression) == "alpha-beta"
