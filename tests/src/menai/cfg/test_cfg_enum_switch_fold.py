"""
Tests for MenaiCFGEnumSwitchFold.

Covers:
  1. A switch whose scrutinee is a constructed enum becomes a jump to the arm
     for its variant, and the construction it no longer needs is removed.
  2. A switch whose scrutinee is not a constructed enum is left alone.
  3. End-to-end: a match on an enum the compiler knows the variant of emits no
     SWITCH_ENUM, and still returns the arm the variant selects.
"""

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGConstInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGMakeEnumInstr,
    MenaiCFGParamInstr,
    MenaiCFGReturnTerm,
    MenaiCFGSwitchEnumTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_enum_switch_fold import MenaiCFGEnumSwitchFold
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.menai import Menai
from menai.menai_value import MenaiEnum, MenaiEnumType


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


def _fold(func_: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
    return MenaiCFGEnumSwitchFold()._optimize_function(func_, MenaiCFGContext())


_MODE = MenaiEnumType("mode", 0, ("auto", "stored", "fixed"))


class TestFoldedSwitch:
    """A switch on a constructed enum jumps to the arm for its variant."""

    def test_selected_variant_becomes_jump(self):
        """
        entry: %m = make_enum mode stored
               switch %m [0: block1, 1: block2, 2: block3] default=block3

        The scrutinee is variant 1, so the switch becomes a jump to block2.
        """
        vm = v("m")
        entry = block(
            0,
            MenaiCFGMakeEnumInstr(result=vm, enum_type=_MODE, variant_index=1),
            terminator=MenaiCFGSwitchEnumTerm(
                value=vm, targets=(1, 2, 3), default_block=3,
            ),
            label="entry",
        )
        arms = [
            block(bid, terminator=MenaiCFGReturnTerm(value=v("r")), label=f"arm{bid}")
            for bid in (1, 2, 3)
        ]
        f = func(entry, *arms)

        new_f, changed = _fold(f)
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target == 2

    def test_unread_construction_is_removed(self):
        """
        The construction's only reader was the switch, so once the switch is a
        jump the construction is dead and is swept.
        """
        vm = v("m")
        entry = block(
            0,
            MenaiCFGMakeEnumInstr(result=vm, enum_type=_MODE, variant_index=0),
            terminator=MenaiCFGSwitchEnumTerm(
                value=vm, targets=(1, 2, 2), default_block=2,
            ),
            label="entry",
        )
        arms = [
            block(bid, terminator=MenaiCFGReturnTerm(value=v("r")), label=f"arm{bid}")
            for bid in (1, 2)
        ]
        f = func(entry, *arms)

        new_f, changed = _fold(f)
        assert changed
        assert not any(
            isinstance(instr, MenaiCFGMakeEnumInstr)
            for block_ in new_f.blocks
            for instr in block_.instrs
        )

    def test_construction_read_elsewhere_is_kept(self):
        """
        The construction is also returned, so it stays; only the switch folds.
        """
        vm = v("m")
        entry = block(
            0,
            MenaiCFGMakeEnumInstr(result=vm, enum_type=_MODE, variant_index=2),
            terminator=MenaiCFGSwitchEnumTerm(
                value=vm, targets=(1, 2, 3), default_block=3,
            ),
            label="entry",
        )
        arms = [
            block(bid, terminator=MenaiCFGReturnTerm(value=v("r")), label=f"arm{bid}")
            for bid in (1, 2)
        ]
        join = block(3, terminator=MenaiCFGReturnTerm(value=vm), label="join")
        f = func(entry, *arms, join)

        new_f, changed = _fold(f)
        assert changed

        entry_new = new_f.blocks[0]
        assert isinstance(entry_new.terminator, MenaiCFGJumpTerm)
        assert entry_new.terminator.target == 3
        assert any(isinstance(instr, MenaiCFGMakeEnumInstr) for instr in entry_new.instrs)


class TestNotFolded:
    """A switch whose scrutinee's variant is not known is left alone."""

    def test_parameter_scrutinee_not_folded(self):
        """
        entry: %s = param 0; switch %s [0: block1, 1: block2] default=block2

        Nothing determines the variant, so the switch stays.
        """
        vs = v("s")
        entry = block(
            0,
            MenaiCFGParamInstr(result=vs, index=0, param_name="s"),
            terminator=MenaiCFGSwitchEnumTerm(value=vs, targets=(1, 2), default_block=2),
            label="entry",
        )
        arms = [
            block(bid, terminator=MenaiCFGReturnTerm(value=v("r")), label=f"arm{bid}")
            for bid in (1, 2)
        ]
        f = func(entry, *arms, params=["s"])

        new_f, changed = _fold(f)
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGSwitchEnumTerm)

    def test_constant_scrutinee_not_folded(self):
        """
        entry: %s = const <mode auto>; switch %s [0: block1, 1: block2] default=block2

        A constant enum value is not an enum construction, so the pass does not
        fold it.
        """
        vs = v("s")
        entry = block(
            0,
            MenaiCFGConstInstr(result=vs, value=MenaiEnum(_MODE, 0)),
            terminator=MenaiCFGSwitchEnumTerm(value=vs, targets=(1, 2), default_block=2),
            label="entry",
        )
        arms = [
            block(bid, terminator=MenaiCFGReturnTerm(value=v("r")), label=f"arm{bid}")
            for bid in (1, 2)
        ]
        f = func(entry, *arms)

        new_f, changed = _fold(f)
        assert not changed
        assert isinstance(new_f.blocks[0].terminator, MenaiCFGSwitchEnumTerm)


def _count_op(code, opcode) -> int:
    n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
    for nested in code.code_objects:
        n += _count_op(nested, opcode)

    return n


class TestEndToEnd:
    """Whole-pipeline behaviour."""

    def test_known_variant_switch_not_emitted(self):
        """
        A match on an enum whose variant the compiler knows needs no jump table,
        and the arm the variant selects is the one that returns.
        """
        src = """
        (let ((State (enum (idle running)))
              (classify (lambda (s)
                (match s
                  ((: State 'idle) 1)
                  ((: State 'running) 2)))))
          (classify (State 'idle)))
        """
        menai = Menai()
        code = menai.compile(src)
        assert _count_op(code, Opcode.SWITCH_ENUM) == 0
        assert menai.evaluate_and_format(src) == "1"
