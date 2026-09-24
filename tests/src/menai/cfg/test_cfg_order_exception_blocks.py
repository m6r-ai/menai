"""
Tests for MenaiCFGOrderExceptionBlocks.

Blocks that end in a raise (a MenaiCFGRaiseTerm, lowered to RAISE_ERROR) are
exception paths.  The pass stably moves them to the end of the function's
block list, and the VM backend honours that ordering by emitting them last,
so the cold exception instructions sit after the hot normal path.

Covers:
  1. The pass stably partitions the block list: normal blocks keep their
     relative order, raise blocks keep theirs, and raise blocks come last.
  2. The entry block is never moved.
  3. A function with no raise block is left unchanged.
  4. A function whose raise block is already last reports no change.
  5. Nested lambdas are reordered too.
  6. End-to-end: RAISE_ERROR is emitted after every normal-path instruction
     of the enclosing function.
  7. Behaviour is unchanged: the normal path returns normally and the
     exception path raises.
"""

import pytest

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGFunction,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg.menai_cfg_order_exception_blocks import MenaiCFGOrderExceptionBlocks
from menai.menai import Menai
from menai.menai_compiler import MenaiCompiler
from menai.menai_error import MenaiError


def _block(block_id: int, terminator) -> MenaiCFGBlock:
    """Build a labelled block with the given terminator."""
    return MenaiCFGBlock(id=block_id, label=f"b{block_id}", terminator=terminator)


def _raise_block(block_id: int) -> MenaiCFGBlock:
    """Build a block terminated by a raise."""
    return _block(block_id, MenaiCFGRaiseTerm(message=MenaiCFGValue(id=block_id)))


def _return_block(block_id: int) -> MenaiCFGBlock:
    """Build a block terminated by a return."""
    return _block(block_id, MenaiCFGReturnTerm(value=MenaiCFGValue(id=block_id)))


def _function(blocks: list[MenaiCFGBlock]) -> MenaiCFGFunction:
    """Build a function from a block list."""
    return MenaiCFGFunction(blocks=blocks)


class TestStablePartition:

    def test_raise_blocks_moved_last(self):
        """Normal blocks keep order; raise blocks follow, in their own order."""
        func = _function([
            _return_block(0),
            _raise_block(1),
            _return_block(2),
            _raise_block(3),
        ])
        new_func, changed = MenaiCFGOrderExceptionBlocks().optimize(func)
        assert changed is True
        assert [b.id for b in new_func.blocks] == [0, 2, 1, 3]

    def test_entry_block_stays_first(self):
        """The entry block is never moved."""
        func = _function([
            _return_block(0),
            _raise_block(1),
        ])
        new_func, _ = MenaiCFGOrderExceptionBlocks().optimize(func)
        assert new_func.blocks[0].id == 0

    def test_raise_entry_block_stays_first(self):
        """An entry block that itself raises is pinned first, not moved last."""
        func = _function([
            _raise_block(0),
            _return_block(1),
        ])
        new_func, _ = MenaiCFGOrderExceptionBlocks().optimize(func)
        assert [b.id for b in new_func.blocks] == [0, 1]

    def test_no_raise_block_unchanged(self):
        """A function with no raise block reports no change."""
        func = _function([_return_block(0), _return_block(1)])
        new_func, changed = MenaiCFGOrderExceptionBlocks().optimize(func)
        assert changed is False
        assert [b.id for b in new_func.blocks] == [0, 1]

    def test_already_last_reports_no_change(self):
        """A raise block already at the end reports no change."""
        func = _function([_return_block(0), _raise_block(1)])
        new_func, changed = MenaiCFGOrderExceptionBlocks().optimize(func)
        assert changed is False
        assert [b.id for b in new_func.blocks] == [0, 1]

    def test_nested_lambda_reordered(self):
        """A nested lambda's raise block is moved to the end too."""
        from menai.cfg.menai_cfg import MenaiCFGMakeClosureInstr

        inner = _function([_return_block(0), _raise_block(1), _return_block(2)])
        outer = MenaiCFGFunction(
            blocks=[
                MenaiCFGBlock(
                    id=0,
                    label="entry",
                    instrs=[
                        MenaiCFGMakeClosureInstr(
                            result=MenaiCFGValue(id=0),
                            function=inner,
                            captures=[],
                        ),
                    ],
                    terminator=MenaiCFGReturnTerm(value=MenaiCFGValue(id=0)),
                ),
            ],
        )
        new_outer, changed = MenaiCFGOrderExceptionBlocks().optimize(outer)
        assert changed is True
        new_inner = collect_functions(new_outer)[1]
        assert [b.id for b in new_inner.blocks] == [0, 2, 1]


def _compile(src: str):
    """Compile Menai source and return the top-level CodeObject."""
    return MenaiCompiler().compile(src)


def _last_raise_index(code) -> int:
    """Return the index of the last RAISE_ERROR instruction in code."""
    last = -1
    for i, instr in enumerate(code.instructions):
        if unpack_instruction(instr).opcode == int(Opcode.RAISE_ERROR):
            last = i

    return last


def _first_return_index(code) -> int:
    """Return the index of the first RETURN instruction in code."""
    for i, instr in enumerate(code.instructions):
        if unpack_instruction(instr).opcode == int(Opcode.RETURN):
            return i

    return -1


class TestEmittedLayout:

    def test_raise_emitted_after_normal_path(self):
        """RAISE_ERROR is emitted after the normal-path RETURN."""
        code = _compile("""
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 'one
          (if (integer=? x 2) 'two
          (error "unsupported"))))))
          (classify 1))
        """)
        raise_idx = _last_raise_index(code)
        return_idx = _first_return_index(code)
        assert raise_idx != -1, "no RAISE_ERROR emitted"
        assert return_idx != -1, "no RETURN emitted"
        assert raise_idx > return_idx

    def test_raise_emitted_last(self):
        """RAISE_ERROR is the last instruction in the code object."""
        code = _compile("""
        (letrec ((f (lambda (x)
          (if (integer=? x 1) 10
          (error "bad")))))
          (f 1))
        """)
        raise_idx = _last_raise_index(code)
        assert raise_idx != -1
        assert raise_idx == len(code.instructions) - 1


class TestBehaviourUnchanged:

    def test_normal_path_returns(self):
        """The normal path still returns the expected value."""
        m = Menai()
        result = m.evaluate_and_format("""
        (letrec ((classify (lambda (x)
          (if (integer=? x 1) 'one
          (if (integer=? x 2) 'two
          (error "unsupported"))))))
          (classify 2))
        """)
        assert result == "two"

    def test_exception_path_raises(self):
        """The exception path still raises."""
        m = Menai()
        with pytest.raises(MenaiError):
            m.evaluate_raw("""
            (letrec ((classify (lambda (x)
              (if (integer=? x 1) 'one
              (if (integer=? x 2) 'two
              (error "unsupported"))))))
              (classify 5))
            """)

    def test_match_without_catch_all_raises(self):
        """A match with no catch-all still raises on an unmatched value."""
        m = Menai()
        with pytest.raises(MenaiError):
            m.evaluate_raw("""
            (letrec ((f (lambda (x)
              (match x (1 'a) (2 'b)))))
              (f 9))
            """)

    def test_match_without_catch_all_matches(self):
        """A match with no catch-all still returns on a matched value."""
        m = Menai()
        result = m.evaluate_and_format("""
        (letrec ((f (lambda (x)
          (match x (1 'a) (2 'b)))))
          (f 2))
        """)
        assert result == "b"
