"""
Tests for MenaiCFGEnumInstanceFold.

Covers:
  1. An enum-is-instance? test whose receiver's proven fact is Known('enum', T)
     with T the tested type is folded: the branch is re-wired to its true edge
     and the test instruction is removed.
  2. The test is not folded when the receiver's fact is ANY, BOTTOM, a
     non-enum kind, Known('enum', None) (two enum types joined), or an enum
     type different from the one tested.
  3. The phi shape an (and (enum? v) (enum-is-instance? v T)) guard lowers to is
     folded on the true edge.
  4. A test defined in a dominating block and consumed by a later block's
     branch is folded.
  5. End-to-end: a monomorphic enum match folds its guard out of the bytecode;
     a polymorphic match and an unknown-typed receiver keep it.
  6. Folding never changes the result of a program, and in particular never
     lets a value of another enum type reach a fused switch's arm.
"""

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
from menai.cfg.menai_cfg_enum_instance_fold import MenaiCFGEnumInstanceFold
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.cfg.menai_cfg_type_fact import ANY, BOTTOM, TypeFact
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiBoolean, MenaiEnumType, MenaiInteger


_vid = 9000


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
        for value_id, enum_type in test_types.items():
            context.record_enum_type_of_test(func_, value_id, enum_type)

    return MenaiCFGEnumInstanceFold()._optimize_function(func_, context)


STATE = MenaiEnumType('state', 10, ('idle', 'running'))
MODE = MenaiEnumType('mode', 11, ('fast', 'slow'))


def _instance_test_cfg(receiver, type_arg):
    """
    Build a minimal function whose entry tests `receiver` against `type_arg`.

    entry: %p = enum-is-instance?(%receiver, %type_arg); branch %p -> then/else
    """
    vp = v("p")
    then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
    els = block(2, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
    entry = block(
        0,
        MenaiCFGBuiltinInstr(result=vp, op="enum-is-instance?", args=[receiver, type_arg]),
        terminator=MenaiCFGBranchTerm(cond=vp, true_block=1, false_block=2),
        label="entry",
    )
    return func(entry, then, els), vp


class TestFoldProven:
    """A test whose receiver is proven to be exactly the tested enum type folds."""

    def test_proven_receiver_folds_to_true_edge(self):
        """
        The receiver's fact is Known('enum', state) and the tested type is
        state, so the test is statically #t: the branch jumps to then and the
        test instruction is removed.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=STATE)},
            test_types={vtype.id: STATE},
        )
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target == 1
        assert not any(
            isinstance(i, MenaiCFGBuiltinInstr) and i.op == "enum-is-instance?"
            for i in entry_new.instrs
        )

    def test_proven_receiver_by_value_identity(self):
        """
        The receiver's enum type and the tested type are distinct objects that
        denote the same type, so the comparison is by value and the test folds.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        same_type = MenaiEnumType('state', 10, ('idle', 'running'))
        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=STATE)},
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
            test_types={vtype.id: STATE},
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
            test_types={vtype.id: STATE},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_joined_enum_types_not_folded(self):
        """
        Known('enum', None) means two enum types were joined and identity is
        lost, so the receiver could be either type and the test is load-bearing.
        """
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=None)},
            test_types={vtype.id: STATE},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_different_enum_type_not_folded(self):
        """The receiver is proven to be a mode, but the test names state."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=MODE)},
            test_types={vtype.id: STATE},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_non_enum_kind_not_folded(self):
        """A receiver proven to be an integer is not an enum of the tested type."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="integer")},
            test_types={vtype.id: STATE},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)

    def test_unresolved_test_type_not_folded(self):
        """A test whose enumtype argument does not resolve is left alone."""
        vrecv = v("recv")
        vtype = v("type")
        f, vp = _instance_test_cfg(vrecv, vtype)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=STATE)},
            test_types={},
        )
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGBranchTerm)


class TestPhiShape:
    """The (and (enum? v) (enum-is-instance? v T)) guard shape folds on true."""

    def test_phi_guard_folds_on_true_edge(self):
        """
        The phi joins the enum-is-instance? result with constant #f, which is
        the shape the two-part guard lowers to.  On the true edge only the test
        branch can have been taken, so the test is known #t.
        """
        vrecv = v("recv")
        vtype = v("type")
        vtest = v("test")
        vfalse = v("false")
        vphi = v("phi")

        false_instr = MenaiCFGConstInstr(result=vfalse, value=MenaiBoolean(False))
        phi = MenaiCFGPhiInstr(
            result=vphi,
            incoming=[(vtest, 0), (vfalse, 2)],
        )
        then = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(2, false_instr, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        entry = block(
            0,
            MenaiCFGBuiltinInstr(result=vtest, op="enum-is-instance?", args=[vrecv, vtype]),
            phi,
            terminator=MenaiCFGBranchTerm(cond=vphi, true_block=1, false_block=2),
            label="entry",
        )
        f = func(entry, then, els)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=STATE)},
            test_types={vtype.id: STATE},
        )
        assert changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGJumpTerm)
        assert new_f.blocks[0].terminator.target == 1


class TestCrossBlock:
    """A test defined in a dominating block and consumed later still folds."""

    def test_test_in_dominating_block_folds(self):
        vrecv = v("recv")
        vtype = v("type")
        vtest = v("test")

        then = block(2, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
        els = block(3, terminator=MenaiCFGReturnTerm(value=v("b")), label="else")
        mid = block(
            1,
            terminator=MenaiCFGBranchTerm(cond=vtest, true_block=2, false_block=3),
            label="mid",
        )
        entry = block(
            0,
            MenaiCFGBuiltinInstr(result=vtest, op="enum-is-instance?", args=[vrecv, vtype]),
            terminator=MenaiCFGJumpTerm(target=1),
            label="entry",
        )
        f = func(entry, mid, then, els)

        new_f, changed = _fold(
            f,
            type_facts={vrecv.id: TypeFact(kind="enum", enum_type=STATE)},
            test_types={vtype.id: STATE},
        )
        assert changed

        mid_new = next(b for b in new_f.blocks if b.id == 1)
        assert isinstance(mid_new.terminator, MenaiCFGJumpTerm)
        assert mid_new.terminator.target == 2


def _count_opcode(code, opcode) -> int:
    """Count occurrences of an opcode in a code object tree."""
    total = 0
    for word in code.instructions:
        if unpack_instruction(word).opcode == opcode:
            total += 1

    for child in code.code_objects:
        total += _count_opcode(child, opcode)

    return total


class TestEndToEnd:
    """The fold's effect on real compiled programs."""

    def test_monomorphic_match_folds_the_guard(self):
        """
        The scrutinee is a state parameter, so the analysis proves its type and
        the guard folds out of the bytecode entirely.
        """
        src = """
        (letrec ((State (enum (idle running)))
                 (classify (lambda (s)
                   (match s
                     ((: State 'idle) 'a)
                     ((: State 'running) 'b)
                     (_ 'other)))))
          (classify (State 'idle)))
        """
        code = MenaiCompiler().compile(src, "<test>")
        assert _count_opcode(code, Opcode.ENUM_IS_INSTANCE_P) == 0

    def test_polymorphic_match_keeps_the_guard(self):
        """
        The scrutinee's type is not provable, so the guard must stay: it is what
        stops a value of another enum type reaching the switch's arm.
        """
        src = """
        (letrec ((A (enum (x y z)))
                 (B (enum (x y z)))
                 (classify (lambda (v)
                   (match v
                     ((: A 'x) "ax")
                     ((: A 'y) "ay")
                     ((: A 'z) "az")
                     (_ "fallthrough")))))
          (classify (B 'y)))
        """
        code = MenaiCompiler().compile(src, "<test>")
        assert _count_opcode(code, Opcode.ENUM_IS_INSTANCE_P) >= 1

    def test_folding_does_not_change_results(self):
        """A program whose guard folds still dispatches every variant correctly."""
        from menai import Menai

        m = Menai()
        result = m.evaluate_and_format(
            "(letrec ((State (enum (idle running stopped)))"
            "         (classify (lambda (s)"
            "           (match s"
            "             ((: State 'idle) \"i\")"
            "             ((: State 'running) \"r\")"
            "             ((: State 'stopped) \"s\")"
            "             (_ \"other\")))))"
            "  (list (classify (State 'idle))"
            "        (classify (State 'running))"
            "        (classify (State 'stopped))))"
        )
        assert result == '("i" "r" "s")'

    def test_folding_keeps_wrong_typed_values_out_of_arms(self):
        """
        The safety property the guard exists for: a value of another enum type
        falls through even when the guard's receiver type is proven elsewhere.
        """
        from menai import Menai

        m = Menai()
        result = m.evaluate_and_format(
            "(let ((A (enum (x y z)))"
            "      (B (enum (p q r))))"
            "  (match (B 'q)"
            "    ((: A 'x) \"ax\")"
            "    ((: A 'y) \"ay\")"
            "    ((: A 'z) \"az\")"
            "    ((: B 'p) \"bp\")"
            "    ((: B 'q) \"bq\")"
            "    ((: B 'r) \"br\")"
            "    (_ \"fallthrough\")))"
        )
        assert result == '"bq"'
