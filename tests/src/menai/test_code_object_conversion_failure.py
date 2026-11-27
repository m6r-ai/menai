"""Tests for converting a program's code object tree into native code objects.

The C VM converts the Python CodeObject tree before execution.  A host-supplied
value that cannot be converted must raise a Python error, and the failure must
leave both the interpreter and the Menai instance usable afterwards.
"""

import pytest

from menai import (
    Menai, MenaiDict, MenaiInteger, MenaiList, MenaiString, MenaiVector,
)


def _with_unconvertible_list() -> MenaiDict:
    """Return a bound dict whose list ends in a value the VM cannot convert."""
    return MenaiDict((
        (MenaiString('c'), MenaiList((MenaiInteger(1), MenaiInteger(1.5)))),
    ))


def _with_unconvertible_vector() -> MenaiDict:
    """Return a bound dict whose vector ends in a value the VM cannot convert."""
    return MenaiDict((
        (MenaiString('c'), MenaiVector((MenaiInteger(1), MenaiInteger(2), MenaiInteger(1.5)))),
    ))


def test_unconvertible_constant_raises(menai):
    """A constant the VM cannot convert raises instead of terminating the process."""
    code = menai.compile('(list-length (dict-get inputs "c"))', inject=('inputs', _with_unconvertible_list()))

    with pytest.raises(TypeError):
        menai.execute_raw(code)


def test_unconvertible_element_in_a_container_raises(menai):
    """A container converted part way and then abandoned raises cleanly."""
    code = menai.compile('(vector-length (dict-get inputs "c"))', inject=('inputs', _with_unconvertible_vector()))

    with pytest.raises(TypeError):
        menai.execute_raw(code)


def test_instance_is_usable_after_a_failed_conversion(menai):
    """A failed conversion leaves the instance able to run a later program."""
    code = menai.compile('(list-length (dict-get inputs "c"))', inject=('inputs', _with_unconvertible_list()))

    with pytest.raises(TypeError):
        menai.execute_raw(code)

    assert menai.evaluate('(integer+ 1 2)') == 3


def test_distinct_programs_execute_in_one_process(tmp_path):
    """Three programs, one of them importing a module, run on separate instances.

    The native code objects built for one `execute()` call belong to it alone,
    so a program executed later must not observe any of them.
    """
    (tmp_path / 'doubling.menai').write_text(
        '(let ((double (lambda (n) (integer* n 2)))) (export double))\n',
        encoding='utf-8'
    )

    plain = Menai()
    assert plain.execute_raw(plain.compile('(integer+ 1 2)')).to_python() == 3

    text = Menai()
    assert text.execute_raw(text.compile('(string-upcase "abc")')).to_python() == 'ABC'

    injected = Menai(module_path=[str(tmp_path)])
    payload = MenaiDict(((MenaiString('c'), MenaiList((MenaiInteger(4), MenaiInteger(5), MenaiInteger(6)))),))
    code = injected.compile(
        '(let ((p (import "doubling"))) ((:: p double) (list-length (dict-get inputs "c"))))',
        inject=('inputs', payload),
    )
    assert injected.execute_raw(code).to_python() == 6
