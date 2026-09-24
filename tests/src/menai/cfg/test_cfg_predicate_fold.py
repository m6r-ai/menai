"""
Tests for MenaiCFGPredicateFold.

Covers:
  1. A type predicate on a locally-proven value is folded to the taken branch
     (both the true and the false outcome).
  2. A predicate on a parameter-derived value is not folded.
  3. A predicate on a call result is not folded.
  4. A predicate whose argument's fact is unknown (ANY) is not folded.
  5. A phi whose incoming values are all locally proven is locally proven; a
     phi with a parameter incoming is not.
  6. A predicate whose result is used outside the branch keeps its instruction
     but the branch is still re-wired.
  7. End-to-end: the deflate prefix-key pattern folds its none? check, and a
     recursive match over a parameter is left intact.
"""

import pytest

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGParamInstr,
    MenaiCFGPhiInstr,
    MenaiCFGReturnTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_predicate_fold import MenaiCFGPredicateFold
from menai.cfg.menai_cfg_type_fact import ANY, TypeFact
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiInteger, MenaiNone, MenaiString


_vid = 3000


def v(hint: str = "") -> MenaiCFGValue:
    global _vid
    _vid += 1
    return MenaiCFGValue(id=_vid, hint=hint)


def block(bid: int, *instrs, terminator=None, label: str = "block") -> MenaiCFGBlock:
    b = MenaiCFGBlock(id=bid, label=label)
    b.instrs = list(instrs)
    b.terminator = terminator
    return b


def func(*blocks, params=None, type_facts=None) -> MenaiCFGFunction:
    f = MenaiCFGFunction(blocks=list(blocks), params=params or [])
    f.type_facts = type_facts or {}
    _link(f)
    return f


def _link(f: MenaiCFGFunction) -> None:
    for b in f.blocks:
        b.predecessors = []
    for b in f.blocks:
        t = b.terminator
        if isinstance(t, MenaiCFGJumpTerm):
            t.target.predecessors.append(b)
        elif isinstance(t, MenaiCFGBranchTerm):
            t.true_block.predecessors.append(b)
            t.false_block.predecessors.append(b)


def _fold(func_: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
    return MenaiCFGPredicateFold()._optimize_function(func_)


class TestFoldLocallyProven:
    """A predicate on a locally-proven value folds to the taken branch."""

    def test_integer_predicate_true_folds_to_true_edge(self):
        """
        entry: %n = const 5; %p = integer?(%n); branch %p → then / else
        %n is a constant, so integer? is #t and the branch jumps to then.
        """
        vn = v("n")
        vp = v("p")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vn, value=MenaiInteger(5)),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vn]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, type_facts={vn.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target.id == then.id
        assert not any(isinstance(i, MenaiCFGBuiltinInstr) for i in entry_new.instrs)

    def test_none_predicate_false_folds_to_false_edge(self):
        """
        entry: %n = const 5; %p = none?(%n); branch %p → then / else
        %n is an integer, so none? is #f and the branch jumps to else.
        """
        vn = v("n")
        vp = v("p")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vn, value=MenaiInteger(5)),
            MenaiCFGBuiltinInstr(result=vp, op="none?", args=[vn]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, type_facts={vn.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target.id == els.id


class TestNotFolded:
    """Predicates whose argument's fact is not locally proven are left alone."""

    def test_parameter_argument_not_folded(self):
        """
        A parameter's fact is interprocedurally derived, so even when the
        analysis reports a type, the predicate is not folded.
        """
        vparam = v("x")
        vp = v("p")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGParamInstr(result=vparam, index=0, param_name="x"),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vparam]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, params=["x"], type_facts={vparam.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_call_result_argument_not_folded(self):
        """A call result's type is not proven locally, so the predicate stays."""
        vfn = v("fn")
        vcall = v("call")
        vp = v("p")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vfn, value=MenaiInteger(0)),
            MenaiCFGCallInstr(result=vcall, func=vfn, args=[]),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vcall]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, type_facts={vcall.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert not changed

    def test_unknown_fact_not_folded(self):
        """A locally-proven value whose fact is ANY determines nothing."""
        vn = v("n")
        vp = v("p")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vn, value=MenaiInteger(5)),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vn]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, type_facts={vn.id: ANY})

        new_f, changed = _fold(f)
        assert not changed


class TestPhiProvenance:
    """A phi is locally proven only when every incoming value is."""

    def test_phi_of_locals_is_folded(self):
        """
        join: %m = phi [%a ← blockA, %b ← blockB]; %p = integer?(%m)
        Both incoming values are constants, so the phi is locally proven and
        the predicate folds.
        """
        va = v("a")
        vb = v("b")
        vm = v("m")
        vp = v("p")
        then = block(3, terminator=MenaiCFGReturnTerm(value=v("x")), label="then")
        els = block(4, terminator=MenaiCFGReturnTerm(value=v("y")), label="else")
        join = block(
            2,
            MenaiCFGPhiInstr(result=vm, incoming=[(va, block(0, label="A")), (vb, block(1, label="B"))]),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vm]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="join",
        )
        block_a = block(0, MenaiCFGConstInstr(result=va, value=MenaiInteger(1)), label="A")
        block_b = block(1, MenaiCFGConstInstr(result=vb, value=MenaiInteger(2)), label="B")
        block_a.terminator = MenaiCFGJumpTerm(target=join)
        block_b.terminator = MenaiCFGJumpTerm(target=join)
        entry = block(
            9,
            MenaiCFGConstInstr(result=v("c"), value=MenaiInteger(0)),
            terminator=MenaiCFGBranchTerm(cond=v("c"), true_block=block_a, false_block=block_b),
            label="entry",
        )
        f = func(entry, block_a, block_b, join, then, els, type_facts={va.id: TypeFact(kind="integer"), vb.id: TypeFact(kind="integer"), vm.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert changed

        join_new = next(b for b in new_f.blocks if b.id == 2)
        assert isinstance(join_new.terminator, MenaiCFGJumpTerm)
        assert join_new.terminator.target.id == then.id

    def test_phi_with_parameter_incoming_not_folded(self):
        """A phi with a parameter incoming is not locally proven."""
        vparam = v("x")
        vconst = v("k")
        vm = v("m")
        vp = v("p")
        then = block(3, terminator=MenaiCFGReturnTerm(value=v("x")), label="then")
        els = block(4, terminator=MenaiCFGReturnTerm(value=v("y")), label="else")
        block_a = block(0, MenaiCFGParamInstr(result=vparam, index=0, param_name="x"), label="A")
        block_b = block(1, MenaiCFGConstInstr(result=vconst, value=MenaiInteger(2)), label="B")
        join = block(
            2,
            MenaiCFGPhiInstr(result=vm, incoming=[(vparam, block_a), (vconst, block_b)]),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vm]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="join",
        )
        block_a.terminator = MenaiCFGJumpTerm(target=join)
        block_b.terminator = MenaiCFGJumpTerm(target=join)
        entry = block(
            9,
            MenaiCFGConstInstr(result=v("c"), value=MenaiInteger(0)),
            terminator=MenaiCFGBranchTerm(cond=v("c"), true_block=block_a, false_block=block_b),
            label="entry",
        )
        f = func(entry, block_a, block_b, join, then, els, params=["x"], type_facts={vparam.id: TypeFact(kind="integer"), vconst.id: TypeFact(kind="integer"), vm.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert not changed


class TestPredicateResultUsedElsewhere:
    """A predicate result used outside the branch keeps its instruction."""

    def test_predicate_kept_when_result_used_elsewhere(self):
        """
        The predicate result feeds both the branch and a return value, so the
        instruction is retained; only the branch is re-wired.
        """
        vn = v("n")
        vp = v("p")
        vuse = v("use")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=vuse), label="else")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vn, value=MenaiInteger(5)),
            MenaiCFGBuiltinInstr(result=vp, op="integer?", args=[vn]),
            MenaiCFGBuiltinInstr(result=vuse, op="boolean-not", args=[vp]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=then, false_block=els),
            label="entry",
        )
        f = func(entry, then, els, type_facts={vn.id: TypeFact(kind="integer")})

        new_f, changed = _fold(f)
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target.id == then.id
        assert any(
            isinstance(i, MenaiCFGBuiltinInstr) and i.op == "integer?"
            for i in entry_new.instrs
        )


def _count_op(code, opcode) -> int:
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)

    return n


class TestEndToEnd:
    """Whole-pipeline behaviour."""

    def test_prefix_key_none_check_folds(self):
        """
        The deflate prefix-key pattern: a none? check on the result of a
        bytes read is folded away because the read is locally proven integer.
        """
        src = """
        (letrec
          ((prefix-key
            (lambda (b i)
              (if (integer>? (integer+ i 3) (bytes-length b))
                  #none
                  (bytes-read-u24-be b i))))
           (f
            (lambda (b i table)
              (let ((key (prefix-key b i)))
                (if (none? key)
                    table
                    (dict-set table key
                              (list-prepend (dict-get table key (list)) i)))))))
          f)
        """
        code = MenaiCompiler().compile(src)
        assert _count_op(code, Opcode.NONE_P) == 0

    def test_recursive_match_over_parameter_not_folded(self):
        """
        A recursive function matching on a parameter must keep every type
        predicate: the parameter can be called with values of several types
        through an unresolvable higher-order call.
        """
        src = """
        (letrec ((process-nested (lambda (data)
                                   (match data
                                          ((? integer? n) n)
                                          ((? string? s) (string-length s))
                                          ((? list? l) (fold-list integer+ 0 (map-list process-nested l)))
                                          (_ 0)))))
          (process-nested (list 10 "test" (list 5 "hi") 20)))
        """
        code = MenaiCompiler().compile(src)
        assert _count_op(code, Opcode.INTEGER_P) >= 1
        assert _count_op(code, Opcode.STRING_P) >= 1
        assert _count_op(code, Opcode.LIST_P) >= 1
