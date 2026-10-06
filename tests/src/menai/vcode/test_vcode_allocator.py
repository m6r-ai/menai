"""
Tests for the VCode slot allocator.

The allocator assigns a concrete slot to every virtual register.  Most of its
work is coalescing: writing a value directly into the slot of the value it is
moved into, so the move becomes a same-slot no-op.  These tests pin down the
safety conditions of that coalescing, which are what keep a coalesced write from
clobbering a value that is still live.

The subtle case is a MenaiVCodeMakeClosure whose result is coalesced into a slot
that one of its own captures occupies.  The bytecode emitter writes the closure
slot first and then patches each capture by reading its slot, so a capture read
happens *after* the result write.  Coalescing the result into a capture's slot
therefore overwrites the captured value before it is patched, and the closure
captures itself.
"""

from menai.vcode.menai_vcode import (
    MenaiVCodeFunction,
    MenaiVCodeJump,
    MenaiVCodeMakeClosure,
    MenaiVCodeMove,
    MenaiVCodeOperand,
    MenaiVCodeReg,
    MenaiVCodeReturn,
)
from menai.vcode.menai_vcode_allocator import allocate_slots


def _reg(reg_id: int, hint: str = "") -> MenaiVCodeReg:
    """Return a virtual register with the given id."""
    return MenaiVCodeReg(id=reg_id, hint=hint)


def _make_closure_result_not_coalesced_into_capture():
    """
    Build a VCode function whose self-loop back-edge moves a MAKE_CLOSURE
    result into a parameter slot that the closure captures:

      entry:  r0 = param 0 ('k')
              r1 = make_closure <child> [r0]   ; captures the param
              r0 = r1                          ; self-loop back-edge move
              self-loop -> entry
              return r0

    The MAKE_CLOSURE result (r1) is the sole use of its definition and is moved
    into r0's slot by the back-edge, so it is a coalescing candidate.  But the
    closure captures r0, and the emitter reads that capture after writing the
    closure slot, so r1 must not be assigned r0's slot.
    """
    child = MenaiVCodeFunction(
        params=("v",),
        param_reg_ids=(0,),
        instrs=(MenaiVCodeReturn(value=MenaiVCodeOperand.of_reg(_reg(0, "v"))),),
    )
    k = _reg(0, "k")
    closure = _reg(1, "closure")
    instrs = (
        MenaiVCodeMakeClosure(
            dst=closure,
            function=child,
            captures=(MenaiVCodeOperand.of_reg(k),),
        ),
        MenaiVCodeMove(dst=k, src=closure),
        MenaiVCodeJump(label="entry", is_self_loop=True),
        MenaiVCodeReturn(value=MenaiVCodeOperand.of_reg(k)),
    )
    return MenaiVCodeFunction(
        instrs=instrs,
        params=("k",),
        param_reg_ids=(0,),
    )


class TestMakeClosureResultNotCoalescedIntoCapture:
    """A closure result must not share a slot with any of its captures."""

    def test_result_slot_differs_from_capture_slot(self):
        func = _make_closure_result_not_coalesced_into_capture()
        slot_map = allocate_slots(func)

        capture_slot = slot_map.slot_of(_reg(0, "k"))
        result_slot = slot_map.slot_of(_reg(1, "closure"))

        assert result_slot != capture_slot
