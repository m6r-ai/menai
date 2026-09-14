"""
Tests for MenaiCFGSwitchDispatch.

Covers:
  1. Cascading integer=? if-chain becomes a single dense switch terminator
  2. The integer guard for the switch scrutinee is inserted by
     MenaiCFGTypePropagation (not by switch dispatch), and is skipped when
     the type is already established — either by a prior integer=? guard or
     by an integer? type predicate on the branch true edge
  3. Sparse chains are left as branches (density heuristic)
  4. Chains shorter than two arms are not transformed
  5. A join point after the chain (phi result) still works end-to-end
  6. End-to-end execution: all arms dispatch correctly, default taken for
     out-of-range values, non-integer scrutinee raises a type error
  7. match with integer literals lowers through the same switch path
  8. Hand-written chains nested inside other expressions are transformed
"""

from menai.cfg.menai_cfg import MenaiCFGSwitchTerm
from menai.menai_compiler import MenaiCompiler
from menai.menai import Menai
from menai.menai_value import MenaiList, MenaiSymbol


def _build_cfg(source: str):
    """Compile source through the full CFG pass pipeline and return the CFG."""
    compiler = MenaiCompiler()
    resolved = compiler.compile_to_resolved_ast(source, "<test>")
    desugared = compiler.ast_desugarer.desugar(resolved)
    for p in compiler.ast_passes:
        desugared = p.optimize(desugared)
    ir = compiler.ir_builder.build(desugared)
    for p in compiler.ir_passes:
        ir, _ = p.optimize(ir)
    return compiler.cfg_builder.build(ir), compiler


def _run_passes(cfg):
    compiler = MenaiCompiler()
    for p in compiler.cfg_passes:
        cfg, _ = p.optimize(cfg)

    return cfg


CHAIN_SRC = """
(letrec ((classify (lambda (x)
  (if (integer=? x 1) 'one
  (if (integer=? x 2) 'two
  (if (integer=? x 3) 'three
  (if (integer=? x 4) 'four
  'other)))))))
  (classify 3))
"""

SPARSE_SRC = """
(letrec ((classify (lambda (x)
  (if (integer=? x 1) 'one
  (if (integer=? x 1000) 'thousand
  'other)))))
  (classify 1))
"""

SINGLE_SRC = """
(letrec ((classify (lambda (x)
  (if (integer=? x 1) 'one 'other))))
  (classify 1))
"""


def _count_switches(cfg) -> int:
    count = 0

    def walk(func):
        nonlocal count
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSwitchTerm):
                count += 1

            for instr in block.instrs:
                inner = getattr(instr, 'function', None)
                if inner is not None:
                    walk(inner)

    for top in cfg if isinstance(cfg, list) else [cfg]:
        walk(top)

    return count


class TestChainToSwitch:

    def test_cascading_chain_becomes_switch(self):
        """A four-arm integer if-chain produces exactly one switch terminator."""
        cfg, _ = _build_cfg(CHAIN_SRC)
        cfg = _run_passes(cfg)
        assert _count_switches(cfg) == 1

    def test_switch_shape(self):
        """The switch spans literals 1..4 with the correct default target."""
        cfg, _ = _build_cfg(CHAIN_SRC)
        cfg = _run_passes(cfg)

        for block in cfg.blocks:
            term = block.terminator
            if isinstance(term, MenaiCFGSwitchTerm):
                assert term.min == 1
                assert len(term.targets) == 4
                assert all(t is not None for t in term.targets)
                return

        raise AssertionError("no switch terminator found")

    def test_sparse_chain_not_transformed(self):
        """A chain spanning 1 and 1000 is too sparse to tabulate."""
        cfg, _ = _build_cfg(SPARSE_SRC)
        cfg = _run_passes(cfg)
        assert _count_switches(cfg) == 0

    def test_single_arm_not_transformed(self):
        """A single integer=? test is not a chain and is left alone."""
        cfg, _ = _build_cfg(SINGLE_SRC)
        cfg = _run_passes(cfg)
        assert _count_switches(cfg) == 0


class TestEndToEnd:

    def test_all_arms_dispatch(self):
        """Every arm value, plus the default, produce the right result."""
        m = Menai()
        src = """
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 'one
          (if (integer=? x 2) 'two
          (if (integer=? x 3) 'three
          (if (integer=? x 4) 'four
          'other)))))))
          (list (classify 1) (classify 2) (classify 3) (classify 4)
                (classify 5) (classify 0) (classify -1)))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiList)
        assert [e.name for e in result.elements] == [
            'one', 'two', 'three', 'four', 'other', 'other', 'other',
        ]

    def test_non_integer_scrutinee_raises(self):
        """A non-integer scrutinee still raises a type error via the guard."""
        import pytest
        from menai.menai_error import MenaiError

        m = Menai()
        src = """
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 'one
          (if (integer=? x 2) 'two
          'other)))))
          (classify "oops"))
        """
        with pytest.raises(MenaiError):
            m.evaluate_raw(src)

    def test_match_integer_literals(self):
        """match on integer literals lowers through the same switch path."""
        m = Menai()
        src = """
        (letrec ((f (lambda (x)
          (match x (1 'a) (2 'b) (3 'c) (4 'd) (_ 'z)))))
          (list (f 1) (f 2) (f 3) (f 4) (f 9)))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiList)
        assert [e.name for e in result.elements] == ['a', 'b', 'c', 'd', 'z']

    def test_switch_inside_nested_expression(self):
        """A chain used as an operand of an outer expression still works."""
        m = Menai()
        src = """
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 10
          (if (integer=? x 2) 20
          (if (integer=? x 3) 30
          0))))))
          (integer+ (classify 2) (classify 3)))
        """
        result = m.evaluate_raw(src)
        assert result.value == 50

    def test_bignum_scrutinee_takes_default(self):
        """A bignum scrutinee is out of table range and takes the default."""
        m = Menai()
        src = """
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 'one
          (if (integer=? x 2) 'two
          'other)))))
          (classify (integer-expn 2 100)))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiSymbol)
        assert result.name == 'other'


class TestTypeRefinement:
    """Tests for type refinement through integer? branch conditions."""

    def test_match_no_redundant_assert(self):
        """match on integer literals skips ASSERT_INTEGER via integer? type refinement.

        The match desugaring emits (if (integer? x) (integer=? x 1) ...),
        and the self-recursive call prevents inlining.  Type propagation
        tracks that the true edge of the integer? branch
        establishes x as integer, so the switch scrutinee guard is skipped.
        """
        from menai.bytecode.menai_bytecode import Opcode
        from menai.menai_compiler import MenaiCompiler

        src = """
        (letrec ((day-name (lambda (day-num)
          (if (integer=? day-num 0) (day-name 0)
          (match day-num
            (0 "sun") (1 "mon") (2 "tue") (3 "wed")
            (4 "thu") (5 "fri") (6 "sat") (_ "unknown"))))))
          (day-name 3))
        """
        compiler = MenaiCompiler()
        code = compiler.compile(src, "<test>")

        def find_switch(code_obj):
            for i in range(len(code_obj.instructions)):
                word = code_obj.instructions[i]
                op = (word >> 48) & 0xFFFF
                if op == Opcode.SWITCH_INTEGER:
                    prev = code_obj.instructions[i - 1]
                    prev_op = (prev >> 48) & 0xFFFF
                    return prev_op != Opcode.ASSERT_INTEGER

            for child in code_obj.code_objects:
                result = find_switch(child)
                if result is not None:
                    return result

            return None

        result = find_switch(code)
        assert result is not None, "no SWITCH_INTEGER found"
        assert result, "SWITCH_INTEGER preceded by redundant ASSERT_INTEGER"
