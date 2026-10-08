"""
Tests for MenaiCFGStructInstanceFold.

Covers:
  1. A struct-is-instance? test whose receiver's proven fact is Known('struct',
     T) with T the tested type is folded: the branch is re-wired to its true
     edge and the test instruction is removed.
  2. The test is not folded when the receiver's fact is ANY, BOTTOM, a
     non-struct kind, Known('struct', None) (two struct types joined), or a
     struct type different from the one tested.
  3. The phi shape an (and (struct? v) (struct-is-instance? v T)) guard lowers
     to is folded on the true edge.
  4. A test whose result is used outside the branch keeps its instruction while
     the branch is still re-wired.
  5. A test defined in a dominating block and consumed by a later block's
     branch is folded.
  6. End-to-end: a monomorphic struct match folds its test out of the bytecode;
     a polymorphic match, an unknown-typed receiver, a joined-struct receiver,
     and a receiver proven only by a refinement all keep the test.
  7. Folding never changes the result of a program.
"""

import pytest

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGParamInstr,
    MenaiCFGPhiInstr,
    MenaiCFGReturnTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.cfg.menai_cfg_struct_instance_fold import MenaiCFGStructInstanceFold
from menai.cfg.menai_cfg_type_fact import ANY, BOTTOM, TypeFact
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiBoolean, MenaiInteger, MenaiStructType


_vid = 5000


def v(hint: str = "") -> MenaiCFGValue:
    global _vid
    _vid += 1
    return MenaiCFGValue(id=_vid, hint=hint)


def block(bid: int, *instrs, terminator=None, label: str = "block") -> MenaiCFGBlock:
    return MenaiCFGBlock(
        id=bid,
        label=label,
        instrs=tuple(instrs),
        terminator=terminator,
    )


def func(*blocks, params=None) -> MenaiCFGFunction:
    return MenaiCFGFunction(
        blocks=tuple(blocks),
        params=tuple(params or ()),
    )


def _fold(
    func_: MenaiCFGFunction,
    type_facts=None,
    test_types=None,
) -> tuple[MenaiCFGFunction, bool]:
    context = MenaiCFGContext()
    if type_facts:
        context.set_facts(func_, type_facts)

    if test_types:
        for value_id, struct_type in test_types.items():
            context.record_struct_type_of_test(func_, value_id, struct_type)

    return MenaiCFGStructInstanceFold()._optimize_function(func_, context)


POINT = MenaiStructType('point', 1, ('x', 'y'))
VEC = MenaiStructType('vec', 2, ('x', 'y'))


def _instance_test_cfg(receiver, type_arg):
    """
    Build a minimal function whose entry tests `receiver` against `type_arg`.

    entry: %p = struct-is-instance?(%receiver, %type_arg); branch %p → then/else
    """
    vp = v("p")
    then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
    els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
    entry = block(
        0,
        MenaiCFGBuiltinInstr(result=vp, op="struct-is-instance?", args=[receiver, type_arg]),
        terminator=MenaiCFGBranchTerm(cond=vp, true_block=1, false_block=2),
        label="entry",
    )
    return func(entry, then, els), vp


class TestFoldProven:
    """A test whose receiver is proven to be exactly the tested struct type folds."""

    def test_proven_receiver_folds_to_true_edge(self):
        """
        The receiver's fact is Known('struct', point) and the tested type is
        point, so the test is statically #t: the branch jumps to then and the
        test instruction is removed.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={vtype.id: POINT},
        )
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target == 1
        assert not any(
            isinstance(i, MenaiCFGBuiltinInstr) and i.op == "struct-is-instance?"
            for i in entry_new.instrs
        )

    def test_proven_receiver_by_value_identity(self):
        """
        The receiver's struct type and the tested type are distinct objects that
        denote the same type, so the comparison is by value and the test folds.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        same_type = MenaiStructType('point', 1, ('x', 'y'))
        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={vtype.id: same_type},
        )
        assert changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGJumpTerm)


class TestNotFolded:
    """A test whose receiver's fact does not prove the tested type stays."""

    def test_unknown_fact_not_folded(self):
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: ANY},
            test_types={vtype.id: POINT},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_bottom_fact_not_folded(self):
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: BOTTOM},
            test_types={vtype.id: POINT},
        )
        assert not changed

    def test_non_struct_kind_not_folded(self):
        """A receiver proven to be a list cannot be the tested struct type."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="list")},
            test_types={vtype.id: POINT},
        )
        assert not changed

    def test_tagless_struct_not_folded(self):
        """
        The receiver's fact is Known('struct', None): two struct types were
        joined and the identity was lost, so the value could be the wrong type.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct")},
            test_types={vtype.id: POINT},
        )
        assert not changed

    def test_different_struct_type_not_folded(self):
        """The receiver is a VEC but the test names POINT, so the test is #f."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=VEC)},
            test_types={vtype.id: POINT},
        )
        assert not changed

    def test_unresolved_tested_type_not_folded(self):
        """The tested struct type could not be resolved, so nothing is proven."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={},
        )
        assert not changed


class TestPhiShape:
    """The (and (struct? v) (struct-is-instance? v T)) shape folds on true."""

    def test_phi_joining_test_with_false_folds(self):
        """
        join: %p = phi [%test ← testblock, %f ← falseblock]; branch %p → then/else
        The test is proven, so the branch jumps to then and the test is removed.
        """
        vrecv = v("recv")
        vtype = v("type")
        vtest = v("test")
        vfalse = v("false")
        vp = v("p")
        then = block(3, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(4, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        join = block(
            2,
            MenaiCFGPhiInstr(result=vp, incoming=[(vtest, 0), (vfalse, 1)]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=3, false_block=4),
            label="join",
        )
        test_block = block(
            0,
            MenaiCFGBuiltinInstr(result=vtest, op="struct-is-instance?", args=[vrecv, vtype]),
            MenaiCFGConstInstr(result=v("cond"), value=MenaiInteger(0)),
            terminator=MenaiCFGJumpTerm(target=2),
            label="test",
        )
        false_block = block(
            1,
            MenaiCFGConstInstr(result=vfalse, value=MenaiBoolean(False)),
            terminator=MenaiCFGJumpTerm(target=2),
            label="false",
        )
        f = func(test_block, false_block, join, then, els)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={vtype.id: POINT},
        )
        assert changed

        join_new = next(b for b in new_f.blocks if b.id == 2)
        assert isinstance(join_new.terminator, MenaiCFGJumpTerm)
        assert join_new.terminator.target == 3


class TestResultUsedElsewhere:
    """A test result used outside the branch keeps its instruction."""

    def test_test_kept_when_result_used_elsewhere(self):
        """
        The test result feeds both the branch and a return value, so the
        instruction is retained; only the branch is re-wired.
        """
        vrecv = v("recv")
        vtype = v("type")
        vp = v("p")
        vuse = v("use")
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, terminator=MenaiCFGReturnTerm(value=vuse), label="else")
        entry = block(
            0,
            MenaiCFGBuiltinInstr(result=vp, op="struct-is-instance?", args=[vrecv, vtype]),
            MenaiCFGBuiltinInstr(result=vuse, op="boolean-not", args=[vp]),
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=1, false_block=2),
            label="entry",
        )
        f = func(entry, then, els)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={vtype.id: POINT},
        )
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target == 1
        assert any(
            isinstance(i, MenaiCFGBuiltinInstr) and i.op == "struct-is-instance?"
            for i in entry_new.instrs
        )


class TestTestInDominatingBlock:
    """A test defined in a dominating block is folded at a later block's branch."""

    def test_test_in_earlier_block_folds(self):
        """
        testblock defines the test and jumps to branchblock, whose branch
        consumes the test's result.  The test is located through the value
        definition map, not the branch's own block, and folded.
        """
        vrecv = v("recv")
        vtype = v("type")
        vp = v("p")
        then = block(2, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(3, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        test_block = block(
            0,
            MenaiCFGBuiltinInstr(result=vp, op="struct-is-instance?", args=[vrecv, vtype]),
            terminator=MenaiCFGJumpTerm(target=1),
            label="test",
        )
        branch_block = block(
            1,
            terminator=MenaiCFGBranchTerm(cond=vp, true_block=2, false_block=3),
            label="branch",
        )
        f = func(test_block, branch_block, then, els)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="struct", struct_type=POINT)},
            test_types={vtype.id: POINT},
        )
        assert changed

        branch_new = next(b for b in new_f.blocks if b.id == 1)
        assert isinstance(branch_new.terminator, MenaiCFGJumpTerm)
        assert branch_new.terminator.target == 2

        test_new = next(b for b in new_f.blocks if b.id == 0)
        assert not any(
            isinstance(i, MenaiCFGBuiltinInstr) and i.op == "struct-is-instance?"
            for i in test_new.instrs
        )


def _count_op(code, opcode) -> int:
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)

    return n


def _instance_op_count(code) -> int:
    return _count_op(code, Opcode.STRUCT_IS_INSTANCE_P)


MONO_SRC = """
(letrec ((point (struct (x y)))
         (get-x (lambda (p)
                  (match p
                         ((: point a b) (integer+ a b))
                         (_ 0)))))
  (get-x (point 1 2)))
"""

POLY_SRC = """
(letrec ((point (struct (x y)))
         (vec (struct (x y)))
         (get-x (lambda (p)
                  (match p
                         ((: point a b) (integer+ a b))
                         (_ 0)))))
  (list (get-x (point 1 2)) (get-x (vec 3 4))))
"""

UNKNOWN_RECEIVER_SRC = """
(let ((point (struct (x y))))
  (letrec ((get-x (lambda (p)
                    (match p
                           ((: point a b) (integer+ a b))
                           (_ 0)))))
    (list (get-x (point 1 2)) (get-x (dict "x" 1)))))
"""

JOINED_STRUCT_SRC = """
(let ((point (struct (x y)))
      (vec (struct (x y))))
  (letrec ((pick (lambda (n) (if (integer<=? n 0) (point 1 2) (vec 3 4))))
           (get-x (lambda (p)
                    (match p
                           ((: point a b) (integer+ a b))
                           (_ 0)))))
    (get-x (pick 1))))
"""

REFINED_RECEIVER_SRC = """
(let ((point (struct (x y)))
      (box (struct (item tag))))
  (let ((inner (struct-get (box (point 1 2) 9) 'item)))
    (match inner
           ((: point a b) (integer+ a b))
           (_ 0))))
"""


class TestEndToEnd:
    """Whole-pipeline behaviour."""

    def test_monomorphic_match_test_folded_out(self):
        """
        Every call site passes a point, so the struct-is-instance? test proves
        #t and is removed from the bytecode.
        """
        code = MenaiCompiler().compile(MONO_SRC)
        assert _instance_op_count(code) == 0

    def test_polymorphic_match_test_retained(self):
        """
        The function is called with two different struct types, so the
        receiver's type identity is not proven and the test is retained.
        """
        code = MenaiCompiler().compile(POLY_SRC)
        assert _instance_op_count(code) >= 1

    def test_unknown_receiver_test_retained(self):
        """
        The function is called with both a struct and a dict, so the receiver's
        type is unknown and the test is load-bearing.
        """
        code = MenaiCompiler().compile(UNKNOWN_RECEIVER_SRC)
        assert _instance_op_count(code) >= 1

    def test_joined_struct_receiver_test_retained(self):
        """
        The receiver is a struct but two struct types were joined, so the
        identity is lost and the test is retained.
        """
        code = MenaiCompiler().compile(JOINED_STRUCT_SRC)
        assert _instance_op_count(code) >= 1

    def test_refined_receiver_test_retained(self):
        """
        The receiver's type is proven only by the test itself, so the test must
        not be folded: it is what establishes the type on its true edge.
        """
        code = MenaiCompiler().compile(REFINED_RECEIVER_SRC)
        assert _instance_op_count(code) >= 1

    def test_monomorphic_result_correct(self, menai):
        assert menai.evaluate_and_format(MONO_SRC) == "3"

    def test_polymorphic_result_correct(self, menai):
        assert menai.evaluate_and_format(POLY_SRC) == "(3 0)"

    def test_unknown_receiver_result_correct(self, menai):
        assert menai.evaluate_and_format(UNKNOWN_RECEIVER_SRC) == "(3 0)"

    def test_joined_struct_result_correct(self, menai):
        assert menai.evaluate_and_format(JOINED_STRUCT_SRC) == "0"

    def test_refined_receiver_result_correct(self, menai):
        assert menai.evaluate_and_format(REFINED_RECEIVER_SRC) == "3"
