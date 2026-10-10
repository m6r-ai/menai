"""
Tests for MenaiCFGEnumSwitchDispatch.

Covers:
  1. A match whose arms are all enum patterns of one type becomes a single
     dense switch terminator
  2. The switch table is dense: every variant index 0..n-1 has a target
  3. Arms of different enum types are not fused into one switch
  4. A single enum arm is not a chain and is left alone
  5. End-to-end execution: every arm dispatches correctly and a non-enum
     scrutinee falls through rather than raising
  6. The written (: TypeName 'variant) pattern form is grouped, so the hoisted
     enum guard is emitted once rather than per arm
"""

from menai.cfg.menai_cfg import MenaiCFGSwitchEnumTerm
from menai.menai import Menai
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiList, MenaiSymbol


def _build_cfg(source: str):
    """Compile source through the full CFG pass pipeline and return the CFG."""
    compiler = MenaiCompiler()
    return compiler.compile_to_cfg(source)


CHAIN_SRC = """
(letrec ((State (enum (idle running stopped paused)))
         (classify (lambda (s)
           (match s
             ((: State 'idle) 'a)
             ((: State 'running) 'b)
             ((: State 'stopped) 'c)
             ((: State 'paused) 'd)
             (_ 'other)))))
  (classify (State 'idle)))
"""

SINGLE_SRC = """
(letrec ((State (enum (idle running)))
         (classify (lambda (s)
           (match s
             ((: State 'idle) 'a)
             (_ 'other)))))
  (classify (State 'idle)))
"""

TWO_TYPES_SRC = """
(letrec ((State (enum (idle running)))
         (Mode (enum (fast slow)))
         (classify (lambda (s)
           (match s
             ((: State 'idle) 'a)
             ((: State 'running) 'b)
             ((: Mode 'fast) 'c)
             ((: Mode 'slow) 'd)
             (_ 'other)))))
  (classify (State 'idle)))
"""


def _enum_switches(cfg) -> list:
    """Return every enum switch terminator reachable in the CFG.

    The switch need not be in the top-level function: a match inside a
    letrec-bound lambda is compiled into a nested function, and the guard's
    reference to the enum type makes that lambda capture the type, which can
    keep it from being inlined into the caller.  Walk the whole function tree,
    exactly as _count_switches does.
    """
    found = []

    def walk(func):
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSwitchEnumTerm):
                found.append(block.terminator)

            for instr in block.instrs:
                inner = getattr(instr, 'function', None)
                if inner is not None:
                    walk(inner)

    for top in cfg if isinstance(cfg, list) else [cfg]:
        walk(top)

    return found


def _count_switches(cfg) -> int:
    count = 0

    def walk(func):
        nonlocal count
        for block in func.blocks:
            if isinstance(block.terminator, MenaiCFGSwitchEnumTerm):
                count += 1

            for instr in block.instrs:
                inner = getattr(instr, 'function', None)
                if inner is not None:
                    walk(inner)

    for top in cfg if isinstance(cfg, list) else [cfg]:
        walk(top)

    return count


class TestChainToSwitch:

    def test_enum_chain_becomes_switch(self):
        """A four-arm enum match produces exactly one switch terminator."""
        cfg = _build_cfg(CHAIN_SRC)
        assert _count_switches(cfg) == 1

    def test_switch_table_is_dense(self):
        """The switch covers every variant index and has a real target for each."""
        cfg = _build_cfg(CHAIN_SRC)

        switches = _enum_switches(cfg)
        assert len(switches) == 1, "expected exactly one enum switch terminator"

        term = switches[0]
        assert len(term.targets) == 4
        assert all(t is not None for t in term.targets)

    def test_single_arm_not_transformed(self):
        """A single enum arm is not a chain and is left alone."""
        cfg = _build_cfg(SINGLE_SRC)
        assert _count_switches(cfg) == 0

    def test_two_enum_types_are_not_fused(self):
        """Arms of different enum types produce a switch per enum type."""
        cfg = _build_cfg(TWO_TYPES_SRC)
        assert _count_switches(cfg) >= 2


class TestEndToEnd:

    def test_all_arms_dispatch(self):
        """Every arm value, plus the default, produces the right result."""
        m = Menai()
        src = """
        (letrec ((State (enum (idle running stopped paused)))
                 (classify (lambda (s)
                   (match s
                     ((: State 'idle) 'a)
                     ((: State 'running) 'b)
                     ((: State 'stopped) 'c)
                     ((: State 'paused) 'd)
                     (_ 'other)))))
          (list (classify (State 'idle)) (classify (State 'running))
                (classify (State 'stopped)) (classify (State 'paused))))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiList)
        assert [e.name for e in result.elements] == ['a', 'b', 'c', 'd']

    def test_non_enum_scrutinee_falls_through(self):
        """An enum pattern is total: a non-enum scrutinee takes the default."""
        m = Menai()
        src = """
        (letrec ((State (enum (idle running)))
                 (classify (lambda (s)
                   (match s
                     ((: State 'idle) 'a)
                     ((: State 'running) 'b)
                     (_ 'other)))))
          (classify 42))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiSymbol)
        assert result.name == 'other'

    def test_exhaustive_match_without_default(self):
        """A match covering every variant needs no default arm."""
        m = Menai()
        src = """
        (letrec ((State (enum (idle running)))
                 (classify (lambda (s)
                   (match s
                     ((: State 'idle) 'a)
                     ((: State 'running) 'b)))))
          (classify (State 'running)))
        """
        result = m.evaluate_raw(src)
        assert isinstance(result, MenaiSymbol)
        assert result.name == 'b'


class TestWrittenTypePatternForm:
    """
    The written (: TypeName 'variant) pattern form must reach the switch.

    The match dispatcher groups a run of same-type enum arms and hoists one
    (enum? tmp) guard over them.  The written pattern head is the reserved ':'
    symbol, so a grouping check that recognises only the normalised
    (TypeName 'variant) shape falls back to the per-arm path, which emits a
    guard per arm and splits each test from its branch.  The switch pass then
    never matches and the match compiles to a comparison chain.

    A switch terminator is therefore the observable consequence of grouping,
    and its presence is the regression guard.
    """

    def test_written_form_emits_switch(self):
        """The written pattern form still produces a switch terminator."""
        cfg = _build_cfg(CHAIN_SRC)
        assert _count_switches(cfg) == 1
