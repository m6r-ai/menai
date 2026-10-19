"""Tests for the Menai source formatter.

These tests define the formatting style specified by ADR-0051.  They are a
specification of behaviour, not a description of any particular implementation.
"""

import glob
from pathlib import Path

import pytest

from menai.ast.menai_lexer import MenaiLexer
from menai_format.menai_formatter import MenaiFormatter, FormatOptions


def format_code(code: str, **kwargs) -> str:
    """Format a source string with the given options."""
    return MenaiFormatter(FormatOptions(**kwargs)).format(code)


def token_texts(source: str) -> list[str]:
    """Return the exact source text of every non-comment token."""
    tokens = MenaiLexer().lex(source, preserve_comments=True)
    return [t.raw if t.raw is not None else str(t.value) for t in tokens if t.type.name != 'COMMENT']


def comment_tokens(source: str) -> list[str]:
    """Return the comment texts of a source string, in order."""
    tokens = MenaiLexer().lex(source, preserve_comments=True)
    return [str(t.value) for t in tokens if t.type.name == 'COMMENT']


class TestAtoms:
    """Atomic values are rendered unchanged."""

    @pytest.mark.parametrize('code', ['42', '3.14', '#t', '#f', '#none', 'symbol', '"hello"'])
    def test_atom(self, code):
        """An atom renders as itself with a trailing newline."""
        assert format_code(code) == code + '\n'

    def test_empty_list(self):
        """An empty list renders as ()."""
        assert format_code('()') == '()\n'

    def test_string_escapes(self):
        """A string's escapes are preserved."""
        assert format_code(r'"a\tb\nc\"d"') == r'"a\tb\nc\"d"' + '\n'


class TestCompact:
    """A list is compact when its rendering ends at or before the width."""

    def test_short_call(self):
        """A short call is compact."""
        assert format_code('(foo a b c)') == '(foo a b c)\n'

    def test_nested_short_call(self):
        """A short call with a nested short call is compact."""
        assert format_code('(foo (bar a) b)') == '(foo (bar a) b)\n'

    def test_compact_at_width(self):
        """A list whose rendering ends exactly at the width is compact."""
        code = '(foo ' + 'a' * 112 + ' b)'
        assert len(code) == 120
        assert format_code(code) == code + '\n'

    def test_multiline_over_width(self):
        """A list whose rendering ends past the width is multi-line."""
        code = '(foo ' + 'a' * 113 + ' b)'
        assert len(code) == 121
        result = format_code(code)
        assert result.count('\n') > 1

    def test_custom_width(self):
        """The width is configurable."""
        assert format_code('(foo a b)', max_width=5).count('\n') > 1


class TestGeneralList:
    """A multi-line general list of atoms aligns arguments under the first argument."""

    def test_arguments_align_under_first(self):
        """With no nested argument, each argument after the first goes under the first."""
        result = format_code('(foo aaaa bbbb cccc)', max_width=10)
        assert result == '(foo aaaa\n     bbbb\n     cccc)\n'

    def test_single_argument_on_next_line(self):
        """A general list with one argument puts it on the next line, indented by two."""
        result = format_code('(foo aaaaaaaaaaaaaaaaaaaa)', max_width=10)
        assert result == '(foo\n  aaaaaaaaaaaaaaaaaaaa)\n'

    def test_single_argument_does_not_align_under_head(self):
        """A lone argument is not pulled up onto the head line."""
        result = format_code('(outer (inner aaaaaaaaaaaaaaaaaaaa))', max_width=10)
        assert result == '(outer\n  (inner\n    aaaaaaaaaaaaaaaaaaaa))\n'

    def test_if_is_a_general_list(self):
        """if lays out its condition on the head line and branches under it."""
        result = format_code('(if (integer>? a b) (foo a) (bar b))', max_width=20)
        assert result == (
            '(if (integer>? a b)\n'
            '    (foo a)\n'
            '    (bar b))\n'
        )


class TestPeers:
    """A general list with a nested argument breaks after the head."""

    def test_nested_arguments_break_after_head(self):
        """A list of nested arguments puts each on its own line indented by two."""
        result = format_code('(list (foo aaaa bbbb) (bar cccc dddd))', max_width=20)
        assert result == '(list\n  (foo aaaa bbbb)\n  (bar cccc dddd))\n'

    def test_one_nested_argument_breaks_after_head(self):
        """A single nested argument breaks after the head; the atom arguments follow it."""
        result = format_code('(list acc (foo aaaa bbbb cccc))', max_width=27)
        assert result == '(list\n  acc\n  (foo aaaa bbbb cccc))\n'

    def test_all_atom_arguments_align_under_first(self):
        """A list of atoms aligns under the first argument."""
        result = format_code('(list aaaa bbbb cccc)', max_width=10)
        assert result == '(list aaaa\n      bbbb\n      cccc)\n'

    def test_if_is_exempt(self):
        """if keeps its condition on the head line even when a part is nested."""
        result = format_code('(if (foo aaaa) (bar bbbb) (baz cccc))', max_width=20)
        assert result == '(if (foo aaaa)\n    (bar bbbb)\n    (baz cccc))\n'


class TestCompoundForms:
    """A compound form with a nested body is multi-line even when it fits."""

    def test_if_with_nested_body_is_multiline(self):
        """An if with a nested condition or branch expands."""
        result = format_code('(if (integer>? a b) (foo a) (bar b))')
        assert result == '(if (integer>? a b)\n    (foo a)\n    (bar b))\n'

    def test_if_of_atoms_is_compact(self):
        """An if whose parts are all atoms stays compact."""
        assert format_code('(if a b c)') == '(if a b c)\n'

    def test_let_with_nested_body_is_multiline(self):
        """A let with a nested body expands."""
        result = format_code('(let ((x 5)) (foo x))')
        assert result == '(let ((x 5))\n  (foo x))\n'

    def test_let_with_atom_body_is_compact(self):
        """A let whose body is an atom stays compact."""
        assert format_code('(let ((x 5)) x)') == '(let ((x 5)) x)\n'

    def test_loop_is_an_ordinary_call(self):
        """A binding named loop is an ordinary call, compact when it fits."""
        assert format_code('(loop (integer+ i 1) acc)') == '(loop (integer+ i 1) acc)\n'

    def test_loop_breaks_after_head_when_it_does_not_fit(self):
        """A binding named loop breaks after the head when it has a nested argument."""
        result = format_code('(loop (integer+ i 1) aaaaaaaaaaaaaaaaaaaa)', max_width=20)
        assert result == '(loop\n  (integer+ i 1)\n  aaaaaaaaaaaaaaaaaaaa)\n'

    def test_lambda_with_nested_body_is_multiline(self):
        """A lambda with a nested body expands, body indented by two."""
        result = format_code('(lambda (x) (foo x))')
        assert result == '(lambda (x)\n  (foo x))\n'

    def test_lambda_with_atom_body_is_compact(self):
        """A lambda whose body is an atom stays compact."""
        assert format_code('(lambda (x) x)') == '(lambda (x) x)\n'

    def test_lambda_body_ignores_params(self):
        """A lambda with an empty body is compact even though its params are a list."""
        assert format_code('(lambda ())') == '(lambda ())\n'

    def test_plain_call_is_not_a_compound_form(self):
        """A plain call is compact when it fits, even with a nested argument."""
        assert format_code('(foo (bar a) b)') == '(foo (bar a) b)\n'


class TestAndOr:
    """and/or are variadic, written flat, and always multi-line with 2+ operands."""

    def test_two_operands_multiline(self):
        """Two operands always expand one per line."""
        assert format_code('(and a b)') == '(and a\n     b)\n'

    def test_three_operands_multiline(self):
        """Three operands expand one per line."""
        assert format_code('(or a b c)') == '(or a\n    b\n    c)\n'

    def test_single_operand_compact(self):
        """A single-operand and/or is compact."""
        assert format_code('(and a)') == '(and a)\n'

    def test_zero_operands_compact(self):
        """A zero-operand and/or is compact."""
        assert format_code('(and)') == '(and)\n'

    def test_flat_form_is_preserved(self):
        """A flat or is not re-nested into a cascade."""
        assert format_code('(or a b c d)') == '(or a\n    b\n    c\n    d)\n'


class TestMatch:
    """A match is always multi-line, with arms indented by two."""

    def test_match_always_multiline(self):
        """A short match is still multi-line."""
        assert format_code('(match x (1 "one") (_ "other"))') == (
            '(match x\n'
            '  (1 "one")\n'
            '  (_ "other"))\n'
        )

    def test_arm_compact_when_it_fits(self):
        """An arm is compact when it fits."""
        result = format_code('(match x (1 (foo a b)) (_ "other"))')
        assert '  (1 (foo a b))' in result

    def test_arm_body_on_next_line_when_long(self):
        """An arm's body goes on the next line when the arm does not fit."""
        result = format_code('(match x (1 (foo aaaa bbbb cccc dddd)) (_ "z"))', max_width=20)
        assert '  (1\n   (foo aaaa' in result

    def test_arm_body_measured_after_pattern(self):
        """An arm body is measured at the column it occupies after the pattern."""
        result = format_code('(match x (a-long-pattern (foo a b c d e f g h)) (_ "z"))', max_width=30)
        assert '  (a-long-pattern\n' in result


class TestLambda:
    """A lambda indents its body by two from the opening parenthesis."""

    def test_compact_lambda(self):
        """A short lambda is compact."""
        assert format_code('(lambda (x) x)') == '(lambda (x) x)\n'

    def test_body_indent(self):
        """A multi-line lambda indents its body by two."""
        result = format_code('(lambda (x) (foo aaaa bbbb cccc))', max_width=10)
        assert result == '(lambda (x)\n  (foo aaaa\n       bbbb\n       cccc))\n'


class TestBindings:
    """Bindings put the value on the next line at the name's column."""

    def test_compact_binding(self):
        """A short binding is compact."""
        assert format_code('(let ((x 5)) x)') == '(let ((x 5)) x)\n'

    def test_lambda_value_on_next_line(self):
        """A binding whose value is a lambda puts the lambda on the next line."""
        result = format_code('(let ((f (lambda () 1)) (g 2)) (f))')
        assert '(f\n    (lambda () 1))' in result

    def test_value_on_next_line(self):
        """A binding whose value is multi-line puts the value on the next line."""
        code = '(let ((f (lambda (x) (foo aaaa bbbb cccc dddd)))) (f 1))'
        result = format_code(code, max_width=20)
        assert '(f\n       (lambda (x)' in result

    def test_value_column_is_name_column(self):
        """The value aligns with the binding name."""
        result = format_code('(let ((f (lambda (x) (foo aaaa bbbb cccc)))) (f 1))', max_width=20)
        lines = result.split('\n')
        assert lines[0] == '(let ((f'
        assert lines[1] == '       (lambda (x)'

    def test_compact_value_measured_after_name(self):
        """A value is measured at the column it occupies after the binding name."""
        code = '(let ((a-long-binding-name (foo aaaa bbbb cccc dddd eeee))) x)'
        result = format_code(code, max_width=40)
        assert result.startswith('(let ((a-long-binding-name\n')

    def test_binding_counts_its_closing_paren(self):
        """A binding is compact only when its closing paren fits within the width."""
        at_width = '(let ((f (foo ' + 'a' * 60 + '))) x)'
        over_width = '(let ((f (foo ' + 'a' * 61 + '))) x)'
        assert len(at_width) == 80
        assert len(over_width) == 81
        assert format_code(at_width, max_width=80) == at_width + '\n'
        result = format_code(over_width, max_width=80)
        assert result == '(let ((f (foo ' + 'a' * 61 + ')))\n  x)\n'

    def test_long_name_forces_value_onto_next_line(self):
        """A long binding name leaves no room, so the value goes on the next line."""
        code = '(let ((name-that-is-quite-long (foo a b c d e f g h))) x)'
        result = format_code(code, max_width=30)
        assert '(name-that-is-quite-long\n' in result

    def test_if_value_with_nested_body_is_multiline(self):
        """A binding whose value is an if with a nested body expands, even when it fits."""
        code = '(let ((a (if (foo x) (bar y) (baz z)))) a)'
        result = format_code(code)
        assert result == (
            '(let ((a\n'
            '       (if (foo x)\n'
            '           (bar y)\n'
            '           (baz z))))\n'
            '  a)\n'
        )

    def test_if_value_of_atoms_is_compact(self):
        """A binding whose value is an if of atoms stays compact."""
        assert format_code('(let ((a (if p q r))) a)') == '(let ((a (if p q r))) a)\n'


class TestBindingList:
    """A binding list is inline when its bindings are short, else on the next line."""

    def test_single_binding_inline(self):
        """A single binding keeps the binding list on the form line."""
        code = '(let ((loop (lambda (i) (foo aaaa bbbb cccc dddd)))) (loop 0))'
        result = format_code(code, max_width=20)
        assert result.startswith('(let ((loop\n')

    def test_all_short_bindings_inline(self):
        """A binding list of short bindings stays inline."""
        code = '(let ((a 1) (b 2) (c 3)) (foo aaaa bbbb cccc dddd eeee))'
        result = format_code(code, max_width=20)
        assert result.startswith('(let ((a 1)\n')

    def test_long_bindings_on_next_line(self):
        """A binding list with a multi-line binding goes on the next line."""
        code = '(let ((f (lambda (x) (foo aaaa bbbb cccc dddd))) (g (lambda (y) y))) (f 1))'
        result = format_code(code, max_width=20)
        assert result.startswith('(let\n  ((f\n')

    def test_body_indent(self):
        """The body is indented by two from the form."""
        code = '(let ((x 5)) (foo aaaa bbbb cccc dddd))'
        result = format_code(code, max_width=20)
        assert '\n  (foo' in result


class TestComments:
    """Comments are preserved and placed on their own lines."""

    def test_standalone_comment_in_list(self):
        """A standalone comment in a list is placed on its own line."""
        result = format_code('(foo a ; hello\n b)')
        assert '; hello' in result
        assert result == '(foo a\n     ; hello\n     b)\n'

    def test_comment_before_binding(self):
        """A comment before a binding is placed on its own line."""
        code = '(let\n  (; note\n   (a 1))\n  a)'
        result = format_code(code)
        assert result == '(let\n  (; note\n   (a 1))\n  a)\n'

    def test_comment_preserved_in_compact(self):
        """A comment prevents compact rendering."""
        result = format_code('(foo a b) ; trailing')
        assert '; trailing' in result

    def test_header_comments_preserved(self):
        """Top-level comments are preserved."""
        result = format_code('; header\n(foo a b)')
        assert result.startswith('; header\n(foo a b)')

    def test_trailing_comment_closes_the_list(self):
        """A comment trailing the last argument does not swallow the closing paren."""
        result = format_code('(foo aaaa bbbb cccc ; note\n)', max_width=10)
        assert result == '(foo aaaa\n     bbbb\n     cccc\n     ; note\n     )\n'

    def test_trailing_comment_keeps_token_stream(self):
        """The closing paren after a trailing comment is still a token."""
        source = '(list (foo 1) ; year\n (bar 2) ; month\n (baz 3) ; day\n)'
        result = format_code(source)
        assert token_texts(source) == token_texts(result)

    def test_trailing_comment_in_binding(self):
        """A comment trailing the last binding does not swallow the closing paren."""
        source = '(let ((a 1) ; note\n) a)'
        result = format_code(source, max_width=10)
        assert token_texts(source) == token_texts(result)

    def test_trailing_comment_in_lambda(self):
        """A comment trailing a lambda body does not swallow the closing paren."""
        source = '(lambda (x) (foo x) ; note\n)'
        result = format_code(source, max_width=10)
        assert token_texts(source) == token_texts(result)

    def test_trailing_comment_in_match(self):
        """A comment trailing the last match arm does not swallow the closing paren."""
        source = '(match x (1 "one") (_ "other") ; note\n)'
        result = format_code(source)
        assert token_texts(source) == token_texts(result)

    def test_trailing_comment_in_dict(self):
        """A comment trailing the last dict pair does not swallow the closing paren."""
        source = '(dict "a" 1 "b" 2 ; note\n)'
        result = format_code(source, max_width=10)
        assert token_texts(source) == token_texts(result)

    def test_trailing_comment_is_idempotent(self):
        """Formatting a list with a trailing comment twice equals formatting once."""
        source = '(foo aaaa bbbb cccc ; note\n)'
        once = format_code(source, max_width=10)
        assert format_code(once, max_width=10) == once


class TestBlankLines:
    """Blank lines between elements are preserved, up to two."""

    def test_blank_line_between_bindings(self):
        """A blank line between bindings is preserved."""
        code = '(let\n  ((a 1)\n\n   (b 2))\n  (+ a b))'
        result = format_code(code)
        assert '(a 1)\n\n' in result

    def test_blank_line_between_top_level_forms(self):
        """A blank line between top-level forms is preserved."""
        result = format_code('(foo)\n\n(bar)')
        assert result == '(foo)\n\n(bar)\n'

    def test_excess_blank_lines_collapsed(self):
        """Runs of more than two blank lines are collapsed."""
        result = format_code('(foo)\n\n\n\n\n(bar)')
        assert result == '(foo)\n\n(bar)\n'

    def test_blank_line_after_multiline_binding(self):
        """A multi-line binding is followed by a blank line before the next."""
        code = '(let ((a (lambda (x) (foo aaaa bbbb cccc dddd))) (b 2)) (+ a b))'
        result = format_code(code, max_width=20)
        assert 'dddd)))\n\n   (b 2)' in result

    def test_blank_line_before_multiline_binding(self):
        """A multi-line binding is preceded by a blank line after the previous."""
        code = '(let ((a 1) (b (lambda (x) (foo aaaa bbbb cccc dddd))) (c 3)) (+ a b c))'
        result = format_code(code, max_width=20)
        assert '(a 1)\n\n   (b\n' in result
        assert 'dddd)))\n\n   (c 3)' in result

    def test_blank_line_before_comment_after_multiline_binding(self):
        """The blank line goes before a comment that precedes the next binding."""
        code = '(let ((a (lambda (x) (foo aaaa bbbb cccc dddd)))\n; note\n(b 2)) (+ a b))'
        result = format_code(code, max_width=20)
        assert 'dddd)))\n\n   ; note\n   (b 2)' in result

    def test_no_blank_line_between_single_line_bindings(self):
        """Single-line bindings are not separated by blank lines."""
        result = format_code('(let ((a 1) (b 2) (c 3)) (foo aaaa bbbb cccc dddd))', max_width=20)
        assert '(a 1)\n      (b 2)\n      (c 3)' in result


class TestQuote:
    """Quoted expressions are preserved."""

    def test_quoted_symbol(self):
        """A quoted symbol is preserved."""
        assert format_code("(list 'a 'b)") == "(list 'a 'b)\n"

    def test_quoted_list(self):
        """A quoted list is preserved."""
        assert format_code("(list '(a b) '(c d))") == "(list '(a b) '(c d))\n"


class TestDict:
    """A multi-line dict keeps each key with its value."""

    def test_pairs_on_own_lines(self):
        """Each key/value pair goes on its own line, aligned under the first key."""
        code = '(dict "function?" function? "symbol?" symbol? "none?" none?)'
        result = format_code(code, max_width=20)
        assert result == (
            '(dict "function?" function?\n'
            '      "symbol?" symbol?\n'
            '      "none?" none?)\n'
        )

    def test_value_shares_key_line(self):
        """A pair whose value is multi-line keeps the value's opening on the key line."""
        code = '(dict "a" (list aaaa bbbb cccc dddd) "b" 2)'
        result = format_code(code, max_width=20)
        assert result.startswith('(dict "a" (list aaaa\n')

    def test_compact_dict(self):
        """A short dict is compact."""
        assert format_code('(dict "a" 1 "b" 2)') == '(dict "a" 1 "b" 2)\n'

    def test_only_the_last_pair_reserves_the_closing_paren(self):
        """A pair that is not last is measured without the dict's closing paren."""
        code = '(dict "k" (foo ' + 'a' * 24 + ') "b" 2 "c" 3)'
        result = format_code(code, max_width=40)
        assert result == '(dict "k" (foo ' + 'a' * 24 + ')\n      "b" 2\n      "c" 3)\n'


class TestLiteralPreservation:
    """A literal's exact source text is preserved."""

    @pytest.mark.parametrize('literal', [
        '#xffffffff', '#o755', '#b1010', '#d42',
        '3.140', '43.0e9', '1e10', '0.5', '1+2j',
        '#t', '#f', '#none',
    ])
    def test_literal_text_preserved(self, literal):
        """A literal renders exactly as written."""
        assert format_code(literal) == literal + '\n'

    def test_string_escapes_preserved(self):
        """A string's escape sequences are preserved verbatim."""
        assert format_code(r'"a\tb"') == r'"a\tb"' + '\n'
        assert format_code(r'"\u0041"') == r'"\u0041"' + '\n'


class TestIdempotency:
    """Formatting is idempotent: formatting twice equals formatting once."""

    @pytest.mark.parametrize('code', [
        '(let ((x 5) (y 10)) (+ x y))',
        '(and a b c)',
        '(match x (1 "one") (_ "other"))',
        '(let ((f (lambda (n) (if (integer<=? n 1) 1 (integer* n (f (integer- n 1))))))) (f 5))',
        '(foo a ; c\n b)',
        '(let\n  ((a 1)\n\n   (b 2))\n  (+ a b))',
    ])
    def test_idempotent(self, code):
        """Formatting the formatted output changes nothing."""
        once = format_code(code)
        assert format_code(once) == once


class TestStdlibInvariants:
    """Formatting the standard library preserves the program and its comments."""

    @staticmethod
    def _files():
        root = Path(__file__).resolve().parents[3]
        prelude = root / 'src' / 'menai' / 'prelude.menai'
        stdlib = sorted(glob.glob(str(root / 'src' / 'menai' / 'stdlib' / '*.menai')))
        return [str(prelude)] + stdlib

    def test_preserves_tokens(self):
        """Formatting preserves the exact non-comment token stream."""
        formatter = MenaiFormatter()
        for path in self._files():
            source = Path(path).read_text(encoding='utf-8')
            formatted = formatter.format(source)
            assert token_texts(source) == token_texts(formatted), path

    def test_preserves_comments(self):
        """Formatting preserves every comment."""
        formatter = MenaiFormatter()
        for path in self._files():
            source = Path(path).read_text(encoding='utf-8')
            formatted = formatter.format(source)
            assert comment_tokens(source) == comment_tokens(formatted), path

    def test_idempotent(self):
        """Formatting the standard library is idempotent."""
        formatter = MenaiFormatter()
        for path in self._files():
            source = Path(path).read_text(encoding='utf-8')
            once = formatter.format(source)
            assert formatter.format(once) == once, path
