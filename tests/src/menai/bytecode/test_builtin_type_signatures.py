"""Tests for the builtin type-signature table.

The table is the sole statement of a builtin call's statically proven result
type, so an entry that disagrees with the builtin is a miscompile rather than a
missed optimisation: guard insertion omits the argument check the wrong fact
makes redundant, and predicate folding rewrites tests against a type the value
never has.  Every entry is therefore checked against the type the builtin
actually produces.
"""

from menai import Menai, MenaiError
from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES
from menai.cfg.menai_cfg_type_fact import fact_for_value
from menai.menai_builtin_registry import BUILTINS


_ARGUMENT_SAMPLES = {
    'any': '1',
    'integer': '1',
    'float': '0.5',
    'complex': '(float->complex 0.5 0.5)',
    'string': '"abc"',
    'boolean': '#t',
    'symbol': "'a",
    'bytes': '(string->bytes "abcdefghijklmnop")',
    'list': '(list 1 2)',
    'dict': '(dict "a" 1)',
    'set': '(set 1)',
    'vector': '(vector 1 2)',
    'function': 'integer-abs',
    'struct': '(Point 1 2)',
    'structtype': 'Point',
    'enum': "(e 'x)",
    'enumtype': 'e',
}

_CALL_ARGUMENT_OVERRIDES = {
    'integer->string': ('1', '10'),
    'string->integer-codepoint': ('"a"',),
    'string-hex->bytes': ('"abcd"',),
    'list->string': ('(list "a" "b")', '"abc"'),
    'list->bytes': ('(list 1 2)',),
    'struct-with': ('(Point 1 2)', "'a", '1'),
}

_NOMINAL_ARGUMENT_TYPES = frozenset({'struct', 'structtype', 'enum', 'enumtype'})

_NOMINAL_TYPE_SCOPE = '(let ((Point (struct (a b))) (e (enum (x y)))) {})'


def _call_expression(name: str, arg_types: list[str]) -> str:
    """Build a call to a builtin with one sample argument of each declared type."""
    args = _CALL_ARGUMENT_OVERRIDES.get(
        name, tuple(_ARGUMENT_SAMPLES[arg_type] for arg_type in arg_types),
    )
    call = "(" + " ".join((name, *args)) + ")"

    if _NOMINAL_ARGUMENT_TYPES.intersection(arg_types):
        return _NOMINAL_TYPE_SCOPE.format(call)

    return call


class TestSignatureTableCoverage:
    """The signature table describes exactly the opcode-backed builtins."""

    def test_table_covers_every_opcode_backed_builtin(self):
        """Every builtin has a signature and every signature names a builtin."""
        assert set(BUILTIN_TYPE_SIGNATURES) == set(BUILTINS)


class TestDeclaredResultTypesMatchRuntime:
    """A builtin produces the result type its signature declares."""

    def test_every_builtin_produces_its_declared_result_type(self):
        """Each concrete declared result type is the type the builtin returns.

        An entry whose declared result type is None states that the type is not
        captured by the table; the analysis treats such a call as any type, so
        there is nothing to hold the builtin to.
        """
        menai = Menai()
        mismatched = []
        uncheckable = []

        for name, (arg_types, declared) in sorted(BUILTIN_TYPE_SIGNATURES.items()):
            if declared is None:
                continue

            expression = _call_expression(name, arg_types)
            try:
                actual = fact_for_value(menai.evaluate_raw(expression)).kind
            except MenaiError as exc:
                uncheckable.append(f"{expression}: {exc}")
                continue

            if actual != declared:
                mismatched.append(f"{expression}: declared {declared}, produced {actual}")

        assert uncheckable == [], "sample call produced no value: " + "; ".join(uncheckable)
        assert mismatched == [], "declared result type mismatched: " + "; ".join(mismatched)
