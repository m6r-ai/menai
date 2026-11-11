"""
Tests for MenaiCFGBranchFactProp.

Covers:
  1. A branch on the false edge of a dominating branch on the same value is
     folded to the false target.
  2. A branch on the true edge is folded to the true target.
  3. A fact survives intervening straight-line blocks.
  4. A fact is dropped at a join whose predecessors disagree.
  5. A branch on a value with no fact is left alone.
  6. End-to-end: the shared `vector-empty?` test produced by CSE is folded out
     of an inlined min-of/max-of caller, leaving one branch on the value.
"""

from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGFunction,
    MenaiCFGJumpTerm,
    MenaiCFGReturnTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_branch_fact_prop import MenaiCFGBranchFactProp
from menai.cfg.menai_cfg_optimization_pass import (
    MenaiCFGContext,
    collect_functions,
)
from menai.menai_compiler import MenaiCompiler


_vid = 9000


def v(hint: str = "") -> MenaiCFGValue:
    """Return a fresh SSA value."""
    global _vid
    _vid += 1
    return MenaiCFGValue(id=_vid, hint=hint)


def block(
    bid: int,
    *instrs,
    terminator=None,
    label: str = "block",
) -> MenaiCFGBlock:
    """Build a CFG block."""
    return MenaiCFGBlock(
        id=bid,
        label=label,
        instrs=tuple(instrs),
        terminator=terminator,
    )


def func(*blocks, params=None) -> MenaiCFGFunction:
    """Build a CFG function from blocks."""
    return MenaiCFGFunction(
        blocks=tuple(blocks),
        params=tuple(params or ()),
    )


def _fold(func_: MenaiCFGFunction) -> tuple[MenaiCFGFunction, bool]:
    """Run the pass on one function."""
    return MenaiCFGBranchFactProp()._optimize_function(func_, MenaiCFGContext())


def _branch_cfg(cond: MenaiCFGValue) -> MenaiCFGFunction:
    """
    Build an entry that branches on cond and an else block that branches again.

    entry: branch cond -> then / else
    then:  return a
    else:  branch cond -> else-then / else-else
    """
    then_block = block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then")
    else_then = block(3, terminator=MenaiCFGReturnTerm(value=v("b")), label="else-then")
    else_else = block(4, terminator=MenaiCFGReturnTerm(value=v("c")), label="else-else")
    else_block = block(
        2,
        terminator=MenaiCFGBranchTerm(
            cond=cond,
            true_block=else_then.id,
            false_block=else_else.id,
        ),
        label="else",
    )
    entry = block(
        0,
        terminator=MenaiCFGBranchTerm(
            cond=cond,
            true_block=then_block.id,
            false_block=else_block.id,
        ),
        label="entry",
    )
    return func(entry, then_block, else_block, else_then, else_else)


class TestFoldFromDominatingBranch:
    """A second branch on a value a dominating branch has already decided folds."""

    def test_false_edge_folds_to_false_target(self):
        """
        The else edge proves cond is false, so the else block's branch on cond
        becomes a jump to its false target.
        """
        cond = v("cond")
        f = _branch_cfg(cond)

        new_f, changed = _fold(f)

        assert changed
        else_block = next(block_ for block_ in new_f.blocks if block_.id == 2)
        assert else_block.terminator == MenaiCFGJumpTerm(target=4)

    def test_true_edge_folds_to_true_target(self):
        """
        The then edge proves cond is true, so a branch on cond there becomes a
        jump to its true target.
        """
        cond = v("cond")
        then_then = block(3, terminator=MenaiCFGReturnTerm(value=v("b")), label="then-then")
        then_else = block(4, terminator=MenaiCFGReturnTerm(value=v("c")), label="then-else")
        then_block = block(
            1,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=then_then.id,
                false_block=then_else.id,
            ),
            label="then",
        )
        else_block = block(2, terminator=MenaiCFGReturnTerm(value=v("d")), label="else")
        entry = block(
            0,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=then_block.id,
                false_block=else_block.id,
            ),
            label="entry",
        )
        f = func(entry, then_block, else_block, then_then, then_else)

        new_f, changed = _fold(f)

        assert changed
        then_block = next(block_ for block_ in new_f.blocks if block_.id == 1)
        assert then_block.terminator == MenaiCFGJumpTerm(target=3)

    def test_fact_crosses_intervening_block(self):
        """
        The fact on the else edge reaches a branch two blocks later, because the
        intervening block carries it through unconditionally.
        """
        cond = v("cond")
        branch_block = block(
            3,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=4,
                false_block=5,
            ),
            label="later",
        )
        else_block = block(
            2,
            terminator=MenaiCFGJumpTerm(target=branch_block.id),
            label="else",
        )
        entry = block(
            0,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=1,
                false_block=else_block.id,
            ),
            label="entry",
        )
        f = func(
            entry,
            block(1, terminator=MenaiCFGReturnTerm(value=v("a")), label="then"),
            else_block,
            branch_block,
            block(4, terminator=MenaiCFGReturnTerm(value=v("b")), label="then"),
            block(5, terminator=MenaiCFGReturnTerm(value=v("c")), label="else"),
        )

        new_f, changed = _fold(f)

        assert changed
        branch_block = next(block_ for block_ in new_f.blocks if block_.id == 3)
        assert branch_block.terminator == MenaiCFGJumpTerm(target=5)


class TestNoFold:
    """A branch is left alone when the facts do not decide it."""

    def test_conflicting_predecessor_facts_do_not_fold(self):
        """
        One predecessor reaches the join with cond true and the other with cond
        false, so the join has no fact and its branch stays.
        """
        cond = v("cond")
        join = block(
            3,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=4,
                false_block=5,
            ),
            label="join",
        )
        entry = block(
            0,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=1,
                false_block=2,
            ),
            label="entry",
        )
        f = func(
            entry,
            block(1, terminator=MenaiCFGJumpTerm(target=join.id), label="true"),
            block(2, terminator=MenaiCFGJumpTerm(target=join.id), label="false"),
            join,
            block(4, terminator=MenaiCFGReturnTerm(value=v("a")), label="then"),
            block(5, terminator=MenaiCFGReturnTerm(value=v("b")), label="else"),
        )

        new_f, changed = _fold(f)

        assert not changed
        assert new_f is f

    def test_branch_on_unknown_value_does_not_fold(self):
        """A branch on a value no dominating branch has decided stays."""
        cond = v("cond")
        other = v("other")
        entry = block(
            0,
            terminator=MenaiCFGBranchTerm(
                cond=cond,
                true_block=1,
                false_block=2,
            ),
            label="entry",
        )
        branch_block = block(
            3,
            terminator=MenaiCFGBranchTerm(
                cond=other,
                true_block=4,
                false_block=5,
            ),
            label="branch",
        )
        f = func(
            entry,
            block(1, terminator=MenaiCFGJumpTerm(target=3), label="true"),
            block(2, terminator=MenaiCFGJumpTerm(target=3), label="false"),
            branch_block,
            block(4, terminator=MenaiCFGReturnTerm(value=v("a")), label="then"),
            block(5, terminator=MenaiCFGReturnTerm(value=v("b")), label="else"),
        )

        new_f, changed = _fold(f)

        assert not changed
        assert new_f is f


class TestPipeline:
    """The pass consumes the shared conditions that CSE produces."""

    def test_shared_inlined_guard_is_folded(self):
        """
        CSE unifies the caller's vector-empty? test with the copies inlined
        from min-of and max-of.  The pass folds the two later branches, leaving
        one branch on the shared value.
        """
        source = """
(let ((min-of (lambda (v)
                (if (vector-empty? v)
                    #none
                    (fold-vector float-min (vector-nth v 0) v))))
      (max-of (lambda (v)
                (if (vector-empty? v)
                    #none
                    (fold-vector float-max (vector-nth v 0) v)))))
  (lambda (v count)
    (if (or (integer<? count 1)
            (vector-empty? v))
        #none
        (let ((lo (min-of v))
              (hi (max-of v)))
          (float- hi lo)))))
"""
        cfg = MenaiCompiler().compile_to_cfg(source)
        target = next(
            func_ for func_ in collect_functions(cfg)
            if func_.binding_name is None
            and len(func_.params) == 2
            and set(func_.free_vars) == {"float-max", "float-min"}
        )

        empty_results = {
            instr.result.id
            for block_ in target.blocks
            for instr in block_.instrs
            if isinstance(instr, MenaiCFGBuiltinInstr)
            and instr.op == "vector-empty?"
        }
        branches = [
            block_.terminator
            for block_ in target.blocks
            if isinstance(block_.terminator, MenaiCFGBranchTerm)
            and block_.terminator.cond.id in empty_results
        ]

        assert len(branches) == 1
