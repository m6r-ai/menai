"""
Tests for MenaiCFGMergeExitBlocks.

Covers:
  1. The pipeline leaves no two equivalent exit blocks in a function
  2. The duplication being removed is real: without the pass, a literal match
     with a default arm produces a pair of equivalent exit blocks
  3. A match with no duplicated arm keeps its block count
  4. A raise arm duplicated by a type guard is merged to one block
  5. Execution is unchanged: every arm, the default, and a wrong-typed scrutinee
"""

from menai.cfg.menai_cfg import (
    MenaiCFGFunction,
    MenaiCFGMakeClosureInstr,
    MenaiCFGRaiseTerm,
    successor_ids,
)
from menai.cfg.menai_cfg_merge_exit_blocks import (
    MenaiCFGMergeExitBlocks,
    _blocks_equivalent,
)
from menai.menai import Menai
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiList, MenaiInteger


def _build_cfg(source: str, merge: bool = True) -> MenaiCFGFunction:
    """Compile source through the CFG pass pipeline, optionally without the merge pass."""
    compiler = MenaiCompiler()
    if not merge:
        compiler.cfg_passes = [
            cfg_pass
            for cfg_pass in compiler.cfg_passes
            if not isinstance(cfg_pass, MenaiCFGMergeExitBlocks)
        ]

    return compiler.compile_to_cfg(source)


def _functions(cfg: MenaiCFGFunction) -> list[MenaiCFGFunction]:
    """Return every function in the CFG, nested lambdas included."""
    found = [cfg]

    for block in cfg.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGMakeClosureInstr):
                found.extend(_functions(instr.function))

    return found


def _exit_blocks(func: MenaiCFGFunction) -> list:
    """Return the blocks of `func` that have no successors."""
    return [
        block
        for block in func.blocks
        if block.terminator is not None and successor_ids(block.terminator) == []
    ]


def _equivalent_exit_pairs(cfg: MenaiCFGFunction) -> int:
    """Return the number of pairs of equivalent exit blocks anywhere in the CFG."""
    pairs = 0

    for func in _functions(cfg):
        exits = _exit_blocks(func)
        for index, first in enumerate(exits):
            for second in exits[index + 1:]:
                if _blocks_equivalent(first, second):
                    pairs += 1

    return pairs


def _raise_block_count(cfg: MenaiCFGFunction) -> int:
    """Return the number of blocks ending in a raise anywhere in the CFG."""
    return sum(
        1
        for func in _functions(cfg)
        for block in func.blocks
        if isinstance(block.terminator, MenaiCFGRaiseTerm)
    )


def _block_count(cfg: MenaiCFGFunction) -> int:
    """Return the total number of blocks anywhere in the CFG."""
    return sum(len(func.blocks) for func in _functions(cfg))


MATCH_WITH_DEFAULT = """
(let ((classify (lambda (n)
                  (match n
                    (0 'zero)
                    (2 'two)
                    (3 'three)
                    (_ 'other)))))
  (classify 0))
"""

MATCH_WITH_RAISE_DEFAULT = """
(let ((classify (lambda (n)
                  (match n
                    (0 'zero)
                    (2 'two)
                    (_ (error "no arm matched"))))))
  (classify 0))
"""

MATCH_WITHOUT_DUPLICATION = """
(let ((classify (lambda (x)
                  (match x
                    ((? integer? n) 'int)
                    ((? string? s) 'str)))))
  (classify 0))
"""
MATCH_WITH_GUARDED_RAISE_DEFAULT = """
(let ((classify (lambda (lst)
                  (match (list-first lst)
                    (0 'zero)
                    (2 'two)
                    (_ (error (string-concat "no arm: " (integer->string (list-first lst)))))))))
  (classify (list 0)))
"""


class TestMergeRemovesDuplicates:

    def test_pipeline_leaves_no_equivalent_exit_blocks(self):
        """No two exit blocks in the compiled program are equivalent."""
        cfg = _build_cfg(MATCH_WITH_DEFAULT)
        assert _equivalent_exit_pairs(cfg) == 0

    def test_duplicate_exit_blocks_exist_without_the_pass(self):
        """The pass has something to remove: the default arm is built twice."""
        cfg = _build_cfg(MATCH_WITH_DEFAULT, merge=False)
        assert _equivalent_exit_pairs(cfg) >= 1

    def test_merge_reduces_the_block_count(self):
        """Merging the duplicated default arm removes a block."""
        without = _build_cfg(MATCH_WITH_DEFAULT, merge=False)
        with_merge = _build_cfg(MATCH_WITH_DEFAULT)
        assert _block_count(with_merge) < _block_count(without)

    def test_guarded_raise_default_merges_to_one_block(self):
        """
        A raising default arm that reads a guarded operand is built twice, and merges.

        The type guard proves the scrutinee's type on the arm chain, so the copy
        reached from the guard's false edge has to test the operand again.  That
        extra test is what makes the two copies distinguishable before the merge,
        and the merge removes both the duplicate and the test.
        """
        without = _build_cfg(MATCH_WITH_GUARDED_RAISE_DEFAULT, merge=False)
        with_merge = _build_cfg(MATCH_WITH_GUARDED_RAISE_DEFAULT)

        assert _raise_block_count(without) == 2
        assert _raise_block_count(with_merge) == 1

    def test_unguarded_raise_default_is_not_duplicated(self):
        """A raising default arm that reads nothing guarded is built once already."""
        without = _build_cfg(MATCH_WITH_RAISE_DEFAULT, merge=False)
        with_merge = _build_cfg(MATCH_WITH_RAISE_DEFAULT)

        assert _raise_block_count(without) == 1
        assert _block_count(with_merge) == _block_count(without)

    def test_no_duplication_leaves_the_block_count_alone(self):
        """A match that builds no duplicated arm is not changed by the pass."""
        without = _build_cfg(MATCH_WITHOUT_DUPLICATION, merge=False)
        with_merge = _build_cfg(MATCH_WITHOUT_DUPLICATION)
        assert _block_count(with_merge) == _block_count(without)


class TestExecutionIsUnchanged:

    def test_every_arm_and_the_default(self):
        """Each arm and the default produce their value after merging."""
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((classify (lambda (n)
                              (match n
                                (0 'zero)
                                (2 'two)
                                (3 'three)
                                (_ 'other)))))
              (list (classify 0) (classify 2) (classify 3) (classify 7)))
            """)

        assert isinstance(result, MenaiList)
        assert [element.name for element in result.elements] == [
            'zero', 'two', 'three', 'other',
        ]

    def test_wrong_typed_scrutinee_takes_the_merged_default(self):
        """A scrutinee that is not an integer reaches the default arm, not a crash."""
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((classify (lambda (n)
                              (match n
                                (0 'zero)
                                (2 'two)
                                (_ 'other)))))
              (classify "not an integer"))
            """)

        assert result.name == 'other'

    def test_raising_default_still_raises(self):
        """The merged raise block still raises for a value no arm matches."""
        menai = Menai()
        try:
            menai.evaluate_raw("""
                (let ((classify (lambda (n)
                                  (match n
                                    (0 'zero)
                                    (2 'two)
                                    (_ (error "no arm matched"))))))
                  (classify 5))
                """)

        except Exception as exc:  # noqa: BLE001 - the error type is the VM's, not the test's concern
            assert "no arm matched" in str(exc)
            return

        raise AssertionError("expected the default arm to raise")

    def test_integer_arm_dispatch_survives(self):
        """A match whose arms are consecutive still returns an integer result."""
        menai = Menai()
        result = menai.evaluate_raw("""
            (let ((f (lambda (n) (match n (1 10) (2 20) (_ 30)))))
              (list (f 1) (f 2) (f 9)))
            """)

        assert isinstance(result, MenaiList)
        assert [element.value for element in result.elements] == [10, 20, 30]
        assert all(isinstance(element, MenaiInteger) for element in result.elements)
