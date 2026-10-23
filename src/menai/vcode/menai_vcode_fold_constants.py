"""
Constant folding into immediate operands for VCode.

Runs *before* slot allocation, immediately after coalesce_constants.

Motivation
----------
A constant that is not a singleton is materialised by the VCode builder as a
MenaiVCodeLoadConst into a scratch register, and that register is then read by
the consuming instruction.  For an opcode that can take a constant operand this
is pure overhead: a register slot is allocated, a LOAD_CONST is executed, and
the register file is read, all to obtain a value that is known at compile time.

This pass rewrites a foldable builtin operand from a register to a constant
operand.  The bytecode emitter then encodes the constant-pool index directly in
the instruction word and sets the corresponding tag bit, so the VM reads the
constant pool instead of the register file.  Because the rewrite happens before
slot allocation, the constant's register is never assigned a slot: the win is
both an eliminated instruction and eliminated register pressure.

Which positions are foldable
----------------------------
Each Opcode member carries a const_mask recording which source positions may
hold a constant-pool index.  This pass only folds a position the opcode's mask
permits; a position outside the mask is left as a register.  The mask is the
single source of truth shared with the bytecode emitter, the validator, and the
C VM.

Safety
------
A use of a constant register is folded only when the register has a *unique*
definition in the function and that definition is a constant: a
MenaiVCodeLoadConst, a capture-less MenaiVCodeMakeClosure, or a
MenaiVCodeMove whose source is itself a constant register.

VCode registers are SSA value ids (see the allocator's module docstring), so a
register with a unique definition is dominated by that definition at every
use: the constant is the value the register holds wherever it is read.
A register with more than one definition — which arises when phi elimination
redefines a register in each arm — has no single reaching definition, so it is
left as a register.

The MOVE case matters because a constant frequently reaches its use through a
phi-elimination move.  A loop variable that the loop never reassigns — for
example the function argument of an inlined higher-order function such as
fold-list — is initialised before the loop by a single move from the constant,
and the loop back-edge is a self-move that the VCode builder drops.  The
register therefore has a unique definition, but that definition is a MOVE, not
the constant load itself.  Propagating foldability along MOVE edges lets the
constant reach the use through such a chain.  Propagation runs to a fixed
point, so a chain of moves is followed to its constant source.

A MOVE from a constant register is not merely followed through: the move itself
is rewritten to a MenaiVCodeLoadConst of the same constant, so the scratch
register the constant was loaded into stops being the move's source.  This is
the same substitution as any other folded use — `dst = src` where `src` holds a
known constant is `dst = <constant>` — but it is the only way to shorten a MOVE,
which has a register destination and no constant-operand form.  The rewrite
subsumes deleting a dead move: a converted move whose destination has no
remaining unfolded use is removed by the same rule that removes any other
folded definition, because converting it leaves its source with one fewer use.

Converting rather than deleting is what lets the seed of a loop-carried
variable be simplified.  Such a seed cannot itself be folded into the variable's
uses — the variable is defined twice, so no constant describes it at those
uses — but it need not route through a scratch register, and after conversion it
does not.

The "unique definition" requirement is what makes this sound, and it is not
merely a structural convenience.  A loop-carried variable — one the loop does
reassign, such as a counter or accumulator — is always defined at least twice:
once by its seed before the loop and once by the back-edge.  Its seed therefore
fails this test, and its uses inside the loop body are left as registers.  That
is required: a use in the loop body executes again after the back-edge has
overwritten the register, so the seed's constant no longer describes the value
read.  Folding such a use would replace the loop-carried value with its initial
value on every iteration.

This rule is deliberately weaker than "no label between the load and the use",
which the coalesce_constants pass uses.  That rule is too strong here: a
LOAD_CONST in a straight-line prefix is not re-executed on a loop back-edge but
still dominates every use inside the loop, because it is the register's only
definition.  Folding must still recognise that case to reach a loop variable
that the loop never reassigns.

Note that this pass cannot remove a loop counter or accumulator.  Their values
change on each iteration, so no constant describes them at their uses.  Reducing
the cost of an induction variable is a loop transformation (strength reduction
or unrolling), not a constant-folding one.

A defining instruction is removed only when every use of its register has been
folded.  Because folding requires a unique definition, the definition's
lifetime is the whole function, so "every use" is simply every use of the
register id.  Removal runs to a fixed point: removing a MOVE that carried a
constant removes a use of the MOVE's source, which can in turn make the
source's own definition removable.

The whole pass runs to a fixed point, not a single sweep.  Converting a MOVE
creates a new LOAD_CONST, and that load is a constant definition in its own
right: a later MOVE from the converted register can be converted in turn, and a
duplicate load of a value already loaded can be folded into its uses.  A single
sweep would miss both, because its foldability analysis is computed before any
rewriting happens.  Each round therefore analyses the instruction list produced
by the previous round, and the pass stops when a round folds nothing.

Evaluating a builtin
--------------------
Operand folding rewrites a builtin's operands from registers to constants but
leaves the builtin itself in place, so the VM executes an operation whose result
is already known.  A comparison of two literals is the clearest case: it costs
an instruction and a branch on a value decided at compile time.  Once every
operand of a builtin is a constant, the builtin is evaluated and replaced by a
LOAD_CONST of the result.

Evaluation is delegated to the AST constant folder rather than reimplemented
here, because that folder is the project's single source of truth for what
folding a given builtin means and a second implementation would be free to drift
from it.

The pass is a pure transformation: it returns a new MenaiVCodeFunction and
never mutates its input.
"""

from collections.abc import Callable

from menai.ast.menai_ast import (
    MenaiASTBoolean,
    MenaiASTComplex,
    MenaiASTFloat,
    MenaiASTInteger,
    MenaiASTNode,
    MenaiASTString,
)
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.menai_builtin_registry import BUILTINS
from menai.vcode.menai_vcode import (
    MenaiVCodeBuiltin,
    MenaiVCodeCall,
    MenaiVCodeFunction,
    MenaiVCodeInstr,
    MenaiVCodeJump,
    MenaiVCodeJumpIfFalse,
    MenaiVCodeJumpIfTrue,
    MenaiVCodeLabel,
    MenaiVCodeLoadConst,
    MenaiVCodeMakeClosure,
    MenaiVCodeMakeEnum,
    MenaiVCodeMove,
    MenaiVCodeOperand,
    MenaiVCodePatchClosure,
    MenaiVCodeRaise,
    MenaiVCodeReg,
    MenaiVCodeReturn,
    MenaiVCodeReturnIf,
    MenaiVCodeSwitchEnum,
    MenaiVCodeSwitchInteger,
    MenaiVCodeTailCall,
)
from menai.menai_value import (
    MenaiBoolean,
    MenaiComplex,
    MenaiFloat,
    MenaiInteger,
    MenaiString,
    MenaiValue,
)
from menai.vcode.menai_vcode_allocator import _defs_uses


def fold_constants(func: MenaiVCodeFunction) -> MenaiVCodeFunction:
    """
    Fold constant loads into builtin operands where the opcode permits it.

    For each MenaiVCodeBuiltin whose opcode permits a constant operand in a
    given source position, when that operand's register has a unique
    constant definition — a LOAD_CONST, a capture-less MAKE_CLOSURE, or a MOVE
    from another constant register — the operand is rewritten to a constant and
    the defining instruction is removed if no uses of the register remain.

    Args:
        func: The VCode function to optimise (before slot allocation).

    Returns:
        A new MenaiVCodeFunction with constant operands folded.
        Returns func unchanged if no folding applies.
    """
    while True:
        func, changed = _fold_once(func)
        if not changed:
            break

    # A round that folds nothing returns its input unchanged, so a function on
    # which no folding applies is returned as the original object.
    return func


def _fold_once(func: MenaiVCodeFunction) -> tuple[MenaiVCodeFunction, bool]:
    """
    Run one round of constant folding over func.

    A round analyses the instruction list, rewrites every foldable use, and
    removes the definitions that no longer have an unfolded use.  Converting a
    MOVE to a LOAD_CONST creates a *new* constant definition that the next
    round's analysis can see, so a chain of moves is converted one link per
    round.  The caller repeats until a round reports no change.

    Args:
        func: The VCode function to fold one round of.

    Returns:
        (func', changed) where changed is False when the round folded nothing.
    """
    instrs = func.instrs

    # reg_id → {instruction index: constant operand} for every definition of the
    # register that assigns it a compile-time constant.  A register may have
    # several definitions (a loop-carried variable has a seed and a back-edge),
    # and only some of them need be constant.
    const_defs: dict[int, dict[int, MenaiVCodeOperand]] = {}

    for idx, instr in enumerate(instrs):
        definition = _constant_definition(instr)
        if definition is not None:
            dst, operand = definition
            const_defs.setdefault(dst.id, {})[idx] = operand

    if not const_defs:
        return func, False

    # Propagate foldability along MOVE edges.  A MOVE whose source is constant
    # at that point is itself a constant definition of its destination.  This
    # runs to a fixed point so a chain of moves is followed to its constant
    # source, and it is positional: the source must be constant at the move's
    # own index, not merely somewhere in the function.
    reaching = _ReachingDefinitions(instrs)
    changed = True
    while changed:
        changed = False
        for idx, instr in enumerate(instrs):
            if not isinstance(instr, MenaiVCodeMove):
                continue

            src_operand = reaching.constant_at(const_defs, instr.src.id, idx)
            if src_operand is None:
                continue

            defs = const_defs.setdefault(instr.dst.id, {})
            if defs.get(idx) == src_operand:
                continue

            defs[idx] = src_operand
            changed = True

    if not const_defs:
        return func, False

    # Fold every foldable operand use, recording the instruction index of each
    # fold so removal can tell which uses survive.
    folded_use_indices: dict[int, set[int]] = {}
    new_instrs: list[MenaiVCodeInstr] = []
    changed = False
    for idx, instr in enumerate(instrs):
        def resolve(reg_id: int, _idx: int = idx) -> MenaiVCodeOperand | None:
            return reaching.constant_at(const_defs, reg_id, _idx)

        new_instr, folded_regs = _fold_instr(instr, resolve)
        for reg_id in folded_regs:
            folded_use_indices.setdefault(reg_id, set()).add(idx)

        new_instrs.append(new_instr)
        changed = changed or bool(folded_regs)

    # A builtin whose operands are now all constants can be evaluated outright,
    # replacing the instruction with a constant load.  This is what removes a
    # comparison of two literals from the emitted code: operand folding alone
    # leaves the comparison in place, to be executed by the VM on a value that
    # is already known.
    new_instrs = _evaluate_constant_builtins(new_instrs)

    if not changed:
        return func, False

    # A constant definition is removed when no unfolded use can observe it.
    # Removing a MOVE removes a use of the MOVE's source, which can make the
    # source's definition removable in turn, so this runs to a fixed point.
    #
    # A definition may be removed only when every use either was folded, or has
    # at least one *other* definition that reaches it and is not itself being
    # removed.  Removing the last definition that reaches a use would leave
    # that use reading a register nothing defines, which the slot allocator
    # rejects.  The check is against the surviving set, so a definition is only
    # dropped once the uses it uniquely serves are gone.
    use_indices: dict[int, list[int]] = {}
    for idx, instr in enumerate(instrs):
        _, uses = _defs_uses(instr)
        for reg_id in uses:
            if reg_id in const_defs:
                use_indices.setdefault(reg_id, []).append(idx)

    remove_indices: set[int] = set()

    # A loop-carried variable is defined once per back-edge.  Removing one of
    # those definitions does not make the variable redundant: the definitions
    # are the arms of a phi, and each arm carries the value along its own path.
    # Dropping an arm silently changes the value the join observes on that
    # path, so a loop-carried register's definitions are never removed.
    loop_carried = set(func.loop_param_reg_ids)

    changed = True
    while changed:
        changed = False
        for reg_id, defs in const_defs.items():
            if reg_id in loop_carried:
                continue

            for def_idx in defs:
                if def_idx in remove_indices:
                    continue

                folded = folded_use_indices.get(reg_id, set())
                if all(
                    use_idx in folded
                    or use_idx in remove_indices
                    or reaching.has_surviving_alternative(
                        reg_id, def_idx, use_idx, remove_indices
                    )
                    for use_idx in use_indices.get(reg_id, [])
                ):
                    remove_indices.add(def_idx)
                    changed = True

    final_instrs = [
        instr for idx, instr in enumerate(new_instrs) if idx not in remove_indices
    ]

    return (
        MenaiVCodeFunction(
            instrs=tuple(final_instrs),
            params=func.params,
            free_vars=func.free_vars,
            param_reg_ids=func.param_reg_ids,
            free_var_reg_ids=func.free_var_reg_ids,
            loop_param_reg_ids=func.loop_param_reg_ids,
            hoisted_reg_ids=func.hoisted_reg_ids,
            is_variadic=func.is_variadic,
            binding_name=func.binding_name,
            reg_count=func.reg_count,
            source_line=func.source_line,
            source_file=func.source_file,
        ),
        True,
    )


def _fold_operand(
    operand: MenaiVCodeOperand,
    resolve: Callable[[int], MenaiVCodeOperand | None],
) -> tuple[MenaiVCodeOperand, int | None]:
    """
    Return operand, folded to a constant if its register is constant at this
    use, together with the folded register's id.

    The register id is None when no fold occurred.  An operand that is already
    a constant, or whose register is not foldable, is returned unchanged.
    """
    if operand.is_const() or operand.reg is None:
        return operand, None

    const_operand = resolve(operand.reg.id)
    if const_operand is None:
        return operand, None

    return const_operand, operand.reg.id


def _fold_instr(
    instr: MenaiVCodeInstr,
    resolve: Callable[[int], MenaiVCodeOperand | None],
) -> tuple[MenaiVCodeInstr, set[int]]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten
    and folded is the set of register ids that were folded.

    Only the instruction types that carry foldable operands are handled; every
    other instruction is returned unchanged.  A builtin's foldable positions
    come from its opcode's const_mask; the other types have a fixed foldable
    position (a call's callee, a patch's value, a return's value).

    `resolve` maps a register id to the constant it holds at this instruction,
    or None when the register is not constant there.  It is positional: the
    same register may resolve to a constant at one use and not at another.
    """
    if isinstance(instr, MenaiVCodeMove):
        return _fold_move(instr, resolve)

    if isinstance(instr, MenaiVCodeBuiltin):
        return _fold_builtin(instr, resolve)

    if isinstance(instr, MenaiVCodeCall):
        func, folded = _fold_operand(instr.func, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeCall(dst=instr.dst, func=func, args=instr.args), {folded}

    if isinstance(instr, MenaiVCodeTailCall):
        func, folded = _fold_operand(instr.func, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeTailCall(func=func, args=instr.args), {folded}

    if isinstance(instr, MenaiVCodePatchClosure):
        value, folded = _fold_operand(instr.value, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodePatchClosure(
            closure=instr.closure,
            capture_index=instr.capture_index,
            value=value,
        ), {folded}

    if isinstance(instr, MenaiVCodeMakeClosure):
        folded_captures: set[int] = set()
        new_captures: list[MenaiVCodeOperand] = []
        for capture in instr.captures:
            new_capture, folded_id = _fold_operand(capture, resolve)
            new_captures.append(new_capture)
            if folded_id is not None:
                folded_captures.add(folded_id)

        if not folded_captures:
            return instr, set()

        return MenaiVCodeMakeClosure(
            dst=instr.dst,
            function=instr.function,
            captures=tuple(new_captures),
            needs_patching=instr.needs_patching,
        ), folded_captures

    if isinstance(instr, MenaiVCodeReturn):
        value, folded = _fold_operand(instr.value, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeReturn(value=value), {folded}

    if isinstance(instr, MenaiVCodeMakeEnum):
        enum_type, folded = _fold_operand(instr.enum_type, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeMakeEnum(
            dst=instr.dst,
            enum_type=enum_type,
            variant_index=instr.variant_index,
        ), {folded}

    if isinstance(instr, MenaiVCodeReturnIf):
        value, folded = _fold_operand(instr.value, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeReturnIf(
            cond=instr.cond, value=value, when_true=instr.when_true,
        ), {folded}

    if isinstance(instr, MenaiVCodeRaise):
        message, folded = _fold_operand(instr.message, resolve)
        if folded is None:
            return instr, set()

        return MenaiVCodeRaise(message=message), {folded}

    return instr, set()


def _fold_builtin(
    instr: MenaiVCodeBuiltin,
    resolve: Callable[[int], MenaiVCodeOperand | None],
) -> tuple[MenaiVCodeBuiltin, set[int]]:
    """
    Return (instr', folded) where instr' has any foldable operands rewritten
    and folded is the set of register ids that were folded.

    An operand is foldable when its register is constant at this instruction
    and the opcode's const_mask permits a constant in that position.
    """
    const_mask = BUILTINS[instr.op].opcode.const_mask()
    if const_mask == 0:
        return instr, set()

    new_args: list[MenaiVCodeOperand] = []
    folded: set[int] = set()
    for position, arg in enumerate(instr.args):
        if not (const_mask >> position) & 1:
            new_args.append(arg)
            continue

        new_arg, folded_id = _fold_operand(arg, resolve)
        if folded_id is not None:
            folded.add(folded_id)

        new_args.append(new_arg)

    if not folded:
        return instr, set()

    return MenaiVCodeBuiltin(dst=instr.dst, op=instr.op, args=tuple(new_args)), folded


def _fold_move(
    instr: MenaiVCodeMove,
    resolve: Callable[[int], MenaiVCodeOperand | None],
) -> tuple[MenaiVCodeInstr, set[int]]:
    """
    Return (instr', folded) where a move of a constant register is rewritten to
    a constant load, and folded is the set containing the move's source id.

    A move has a register destination and no constant-operand form, so the
    constant cannot be folded into the instruction the way a builtin operand is.
    The move is instead replaced by a MenaiVCodeLoadConst of the same constant,
    which is the same substitution: `dst = src`, where `src` holds a known
    constant, is `dst = <constant>`.

    The source register is reported as folded so that the removal phase can
    delete its definition once this move was its last use.  The destination is
    left as a register: it may be read by instructions that cannot take a
    constant, such as the back-edge of a loop the destination carries.

    A move whose source is not foldable is returned unchanged.  The
    is_phi_move flag is not carried over: it exists for the slot allocator to
    coalesce a single-use arm value into the phi result's slot, and a constant
    load needs no such coalescing.
    """
    const_operand = resolve(instr.src.id)
    if const_operand is None or const_operand.value is None:
        return instr, set()

    return MenaiVCodeLoadConst(dst=instr.dst, value=const_operand.value), {instr.src.id}


_AST_FOLDER = MenaiASTConstantFolder()


def _value_to_ast(value: MenaiValue) -> MenaiASTNode | None:
    """
    Return the AST literal node for value, or None if it has no literal form.

    Only the scalar types the AST folder's fold functions accept are converted.
    A value with no literal form yields None, and the caller leaves the
    enclosing builtin unfolded.
    """
    if isinstance(value, MenaiInteger):
        return MenaiASTInteger(value.value)

    if isinstance(value, MenaiFloat):
        return MenaiASTFloat(value.value)

    if isinstance(value, MenaiComplex):
        return MenaiASTComplex(value.value)

    if isinstance(value, MenaiBoolean):
        return MenaiASTBoolean(value.value)

    if isinstance(value, MenaiString):
        return MenaiASTString(value.value)

    return None


def _ast_to_value(node: MenaiASTNode) -> MenaiValue | None:
    """Return the MenaiValue for an AST literal node, or None."""
    if isinstance(node, MenaiASTInteger):
        return MenaiInteger(node.value)

    if isinstance(node, MenaiASTFloat):
        return MenaiFloat(node.value)

    if isinstance(node, MenaiASTComplex):
        return MenaiComplex(node.value)

    if isinstance(node, MenaiASTBoolean):
        return MenaiBoolean(node.value)

    if isinstance(node, MenaiASTString):
        return MenaiString(node.value)

    return None


def _evaluate_builtin(op: str, args: tuple[MenaiValue, ...]) -> MenaiValue | None:
    """
    Return the value of op applied to args, or None if it cannot be folded.

    The builtin must be one the AST folder folds, and every argument must have
    a literal form the folder's fold functions understand.

    A builtin that may raise is never evaluated.  The AST folder declines those
    too, but the check is repeated here because this pass runs on a program the
    folder has already walked: a raising builtin whose operands only became
    constant during inlining or operand folding has never been considered by
    the folder, and evaluating it would turn a runtime error into a silently
    folded value.
    """
    if f'${op}' not in MenaiASTConstantFolder.FOLDABLE_BUILTINS:
        return None

    info = BUILTINS.get(op)
    if info is None or info.may_raise:
        return None

    ast_args: list[MenaiASTNode] = []
    for arg in args:
        node = _value_to_ast(arg)
        if node is None:
            return None

        ast_args.append(node)

    fold = _AST_FOLDER._builtin_jump_table.get(f'${op}')  # pylint: disable=protected-access
    if fold is None:
        return None

    try:
        result = fold(ast_args)

    except (ArithmeticError, ValueError):
        return None

    if result is None:
        return None

    return _ast_to_value(result)


def _evaluate_constant_builtins(
    instrs: list[MenaiVCodeInstr],
) -> list[MenaiVCodeInstr]:
    """
    Replace each builtin with all-constant operands by a constant load.

    A builtin whose every operand is a constant operand is evaluated and
    rewritten to a MenaiVCodeLoadConst of the result.  A builtin that cannot be
    evaluated — because the AST folder does not fold it, an operand has no
    literal form, or the evaluation raised — is left unchanged.
    """
    result: list[MenaiVCodeInstr] = []
    for instr in instrs:
        if not isinstance(instr, MenaiVCodeBuiltin):
            result.append(instr)
            continue

        args: list[MenaiValue] = []
        for arg in instr.args:
            if arg.value is None:
                break

            args.append(arg.value)

        if len(args) != len(instr.args):
            result.append(instr)
            continue

        value = _evaluate_builtin(instr.op, tuple(args))
        if value is None:
            result.append(instr)
            continue

        result.append(MenaiVCodeLoadConst(dst=instr.dst, value=value))

    return result


class _ReachingDefinitions:
    """
    Answers which definition of a register reaches a given instruction.

    VCode is a flat instruction list in reverse-post-order with forward jumps
    plus explicit back-edges to a loop entry label.  A register may be defined
    more than once — a loop-carried variable has a seed before the loop and a
    definition on the back-edge — so "the value of r at instruction i" depends
    on position, not merely on the set of definitions of r.

    The question this class answers is: can the definition at index `d` be the
    one that produced the value read at index `i`?  Two routes exist:

    - Forward: `d < i` and no label between them is a jump target.  Control can
      only fall through from `d` to `i`, so `d` is the reaching definition.
      A label between them may be entered from elsewhere, so a definition
      before it does not necessarily reach past it.
    - Backward: a back-edge jumps from some index after `i` to a label at or
      before `i`, and `d` lies between that label and the jump.  The loop body
      then re-executes, so `d` reaches `i` on the next iteration.

    A definition inside a loop body does *not* reach a use that precedes it
    within the same iteration, which is what makes it safe to fold a seed
    without folding the variable's uses inside the loop.
    """

    def __init__(self, instrs: tuple[MenaiVCodeInstr, ...]) -> None:
        self._instrs = instrs

        # Register id → number of instructions that define it.
        self._def_counts: dict[int, int] = {}

        # Instruction index → label name defined there.
        self._label_at: dict[int, str] = {}
        # Label name → instruction index it appears at.
        self._label_index: dict[str, int] = {}
        # Label name → indices of instructions that jump to it.
        self._jump_sources: dict[str, list[int]] = {}

        for idx, instr in enumerate(instrs):
            defs, _ = _defs_uses(instr)
            for reg_id in defs:
                self._def_counts[reg_id] = self._def_counts.get(reg_id, 0) + 1

            if isinstance(instr, MenaiVCodeLabel):
                self._label_at[idx] = instr.name
                self._label_index[instr.name] = idx
                continue

            for label in self._targets(instr):
                self._jump_sources.setdefault(label, []).append(idx)

    @staticmethod
    def _targets(instr: MenaiVCodeInstr) -> tuple[str, ...]:
        """Return the label names instr can jump to."""
        if isinstance(instr, (MenaiVCodeJump, MenaiVCodeJumpIfTrue, MenaiVCodeJumpIfFalse)):
            return (instr.label,)

        if isinstance(instr, MenaiVCodeSwitchInteger):
            return (*instr.labels, instr.default_label)

        if isinstance(instr, MenaiVCodeSwitchEnum):
            return (*instr.labels, instr.default_label)

        return ()

    def _entered_before(self, idx: int, limit: int) -> bool:
        """
        Return True if the label at idx can be entered from before `limit`.

        A label is a point control can arrive at from elsewhere.  It only
        interrupts a fall-through from an earlier instruction to a later one
        when some jump into it originates before that later instruction: a jump
        from beyond it is a back-edge that closes a loop starting at the label,
        and cannot be how control arrived at the later instruction on the path
        being considered.
        """
        name = self._label_at.get(idx)
        if name is None:
            return False

        return any(source < limit for source in self._jump_sources.get(name, ()))

    def reaches(self, def_idx: int, use_idx: int) -> bool:
        """
        Return True if a definition at def_idx can reach use_idx.

        Only position is considered here; the caller supplies the definitions.
        """
        if def_idx == use_idx:
            return False

        if def_idx < use_idx:
            return not any(
                self._entered_before(idx, use_idx) for idx in range(def_idx + 1, use_idx)
            )

        # def_idx > use_idx: reachable only through a back-edge.  The jump must
        # originate at or after the definition and target a label at or before
        # the use, and the definition must lie inside the loop body that the
        # back-edge closes — between the target label and the jump.  A
        # definition outside that span is not re-executed by the back-edge, so
        # the jump does not carry it to the use.
        for name, sources in self._jump_sources.items():
            target = self._label_index.get(name)
            if target is None or target > use_idx:
                continue

            if target < def_idx and any(source >= def_idx for source in sources):
                return True

        return False

    def definition_reaches(self, reg_id: int, def_idx: int, use_idx: int) -> bool:
        """
        Return True if the definition at def_idx can be observed at use_idx.

        A register with exactly one definition reaches every use, whatever lies
        between them: no other instruction can have written the register, so
        the sole definition is the only value the use can read.  The positional
        test is only needed to disambiguate between competing definitions.
        """
        if self._def_counts.get(reg_id, 0) == 1:
            return def_idx != use_idx

        return self.reaches(def_idx, use_idx)

    def reaches_by_fallthrough(self, def_idx: int, use_idx: int) -> bool:
        """
        Return True if def_idx reaches use_idx by falling straight through.

        This is a stronger condition than `reaches`: the definition precedes
        the use with no jump target between them, so control reaches the use
        from the definition on the direct path.  A definition that reaches a
        use only through a back-edge does not satisfy this, because the
        back-edge covers later iterations only — the first iteration still
        arrives from the fall-through definition.
        """
        if def_idx >= use_idx:
            return False

        return not any(
            self._entered_before(idx, use_idx) for idx in range(def_idx + 1, use_idx)
        )

    def has_surviving_alternative(
        self,
        reg_id: int,
        def_idx: int,
        use_idx: int,
        removed: set[int],
    ) -> bool:
        """
        Return True if some other definition reaching use_idx will survive.

        This is the condition under which def_idx is safe to remove: the use
        still has a definition to read.  Every definition of the register is
        considered, not only the constant ones, because a use reached by a
        non-constant definition is still covered by it.  `removed` are the
        indices already scheduled for removal.

        The alternative must reach the use by fall-through.  A definition that
        reaches it only through a back-edge covers later iterations but not the
        first, so it cannot stand in for a definition on the entry path.
        """
        for other_idx, other_instr in enumerate(self._instrs):
            if other_idx == def_idx or other_idx in removed:
                continue

            other_defs, _ = _defs_uses(other_instr)
            if reg_id not in other_defs:
                continue

            if self.reaches_by_fallthrough(other_idx, use_idx):
                return True

        return False

    def constant_at(
        self,
        const_defs: dict[int, dict[int, MenaiVCodeOperand]],
        reg_id: int,
        use_idx: int,
    ) -> MenaiVCodeOperand | None:
        """
        Return the constant reg_id holds at use_idx, or None if not constant.

        The register is constant at the use only when exactly one of its
        constant definitions reaches the use and no non-constant definition
        reaches it.  A non-constant definition is one present in the
        instruction list's def set but absent from const_defs.
        """
        defs = const_defs.get(reg_id)
        if not defs:
            return None

        reaching = [
            operand
            for def_idx, operand in defs.items()
            if self.definition_reaches(reg_id, def_idx, use_idx)
        ]
        if len(reaching) != 1:
            return None

        if self._has_reaching_non_constant(reg_id, defs, use_idx):
            return None

        return reaching[0]

    def _has_reaching_non_constant(
        self,
        reg_id: int,
        const_defs: dict[int, MenaiVCodeOperand],
        use_idx: int,
    ) -> bool:
        """Return True if a non-constant definition of reg_id reaches use_idx."""
        for def_idx, instr in enumerate(self._instrs):
            if def_idx in const_defs:
                continue

            defs, _ = _defs_uses(instr)
            if reg_id not in defs:
                continue

            if self.definition_reaches(reg_id, def_idx, use_idx):
                return True

        return False


def _constant_definition(
    instr: MenaiVCodeInstr,
) -> tuple[MenaiVCodeReg, MenaiVCodeOperand] | None:
    """
    Return (destination, constant) for an instruction that defines a constant.

    Two instruction types define a constant directly: a LOAD_CONST, and a
    capture-less MAKE_CLOSURE (a closure with no captures and no patching is a
    constant MenaiFunction).  Every other instruction yields None.

    The destination register is returned alongside the constant so the caller
    does not have to re-narrow the instruction to reach its `dst` field.
    """
    if isinstance(instr, MenaiVCodeLoadConst):
        return instr.dst, MenaiVCodeOperand.of_const(instr.value)

    if isinstance(instr, MenaiVCodeMakeClosure):
        if instr.captures or instr.needs_patching:
            return None

        return instr.dst, MenaiVCodeOperand.of_function(instr.function)

    return None
