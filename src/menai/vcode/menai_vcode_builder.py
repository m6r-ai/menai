"""
VCode builder — lowers a MenaiCFGFunction (SSA CFG) to MenaiVCodeFunction.

This is the first pass of the VM backend.  It takes the fully optimised SSA
CFG and produces a flat, phi-free linear IR ready for slot allocation,
peephole optimisation, and bytecode emission.

Lowering steps performed for each function
------------------------------------------
1. Compute reverse post-order (RPO) over the reachable CFG blocks.
2. For each block in RPO:
   a. Emit a label for the block.
   b. Emit VCode instructions for each CFG instruction.
   c. Emit phi-elimination moves: for each phi in each *successor* block,
      emit a MenaiVCodeMove copying this block's incoming value into the
      phi result register, immediately before the block's jump/branch.
   d. Emit the block terminator as VCode jumps/returns.
3. Omit the label for the entry block (no predecessor jumps to it by label).
4. Omit unconditional jumps to the immediately following block (fall-through).

Phi elimination
---------------
For each phi instruction  %result = phi [(%v_a, block_A), (%v_b, block_B)]
in a join block J, we insert:
  - At the end of block_A (before its terminator):  MOVE %result ← %v_a
  - At the end of block_B (before its terminator):  MOVE %result ← %v_b

These moves are emitted *after* all other instructions in the predecessor
block but *before* the jump/branch terminator, so that the source value is
still live and the destination is written exactly once on each path.

SSA value → virtual register mapping
--------------------------------------
MenaiCFGValue ids are reused directly as MenaiVCodeReg ids.  Since CFG
values are unique within a function and VCode registers are unique within a
function, this is a safe 1:1 mapping with no renaming required.

Nested functions
----------------
Each MenaiCFGMakeClosureInstr references a nested MenaiCFGFunction.  The
builder recurses to produce a nested MenaiVCodeFunction, which is embedded
in the MenaiVCodeMakeClosure instruction.
"""


from menai.cfg.menai_cfg import (
    MenaiCFGApplyInstr,
    MenaiCFGBlock,
    MenaiCFGBranchTerm,
    MenaiCFGBuiltinInstr,
    MenaiCFGCallInstr,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGGuardInstr,
    MenaiCFGJumpTerm,
    MenaiCFGMakeClosureInstr,
    MenaiCFGParamInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGMakeStructInstr,
    MenaiCFGMakeEnumInstr,
    MenaiCFGMakeListInstr,
    MenaiCFGMakeVectorInstr,
    MenaiCFGMakeSetInstr,
    MenaiCFGMakeDictInstr,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructWithIndexedInstr,
    MenaiCFGPhiInstr,
    MenaiCFGRaiseTerm,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGSwitchTerm,
    MenaiCFGTailApplyTerm,
    MenaiCFGTailCallTerm,
    MenaiCFGValue,
    blocks_by_id,
    result_id_in_instr,
    value_ids_in_instr,
    value_ids_in_term,
)
from menai.vcode.menai_vcode import (
    MenaiVCodeApply,
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
    MenaiVCodeMove,
    MenaiVCodeOperand,
    MenaiVCodePatchClosure,
    MenaiVCodeMakeStruct,
    MenaiVCodeMakeEnum,
    MenaiVCodeMakeList,
    MenaiVCodeMakeVector,
    MenaiVCodeMakeSet,
    MenaiVCodeMakeDict,
    MenaiVCodeStructGetIndexed,
    MenaiVCodeStructWithIndexed,
    MenaiVCodeRaise,
    MenaiVCodeSwitch,
    MenaiVCodeGuard,
    MenaiVCodeReg,
    MenaiVCodeReturn,
    MenaiVCodeTailApply,
    MenaiVCodeTailCall,
)
from menai.menai_value import MenaiInteger


class MenaiVCodeBuilder:
    """
    Lowers a MenaiCFGFunction to a MenaiVCodeFunction.

    Usage::

        vcode = MenaiVCodeBuilder().build(cfg_function)
    """

    def __init__(self) -> None:
        self._reg_cache: dict[int, MenaiVCodeReg] = {}

    def build(self, func: MenaiCFGFunction) -> MenaiVCodeFunction:
        """
        Lower the top-level MenaiCFGFunction to a MenaiVCodeFunction.

        Args:
            func: The fully optimised CFG function (top-level module body).

        Returns:
            A MenaiVCodeFunction ready for slot allocation and emission.
        """
        return self._lower_function(func)

    def _lower_function(self, func: MenaiCFGFunction) -> MenaiVCodeFunction:
        """Lower one CFG function (top-level or nested lambda) to VCode."""
        # Reset the register cache — reg ids are only unique within a function.
        self._reg_cache = {}

        by_id = blocks_by_id(func)
        rpo = self._rpo(func)

        # Pre-compute phi moves: for each block, the list of (dst, src) moves
        # to emit before the block's terminator, one per phi in each successor.
        phi_moves: dict[int, list[tuple[MenaiVCodeReg, MenaiVCodeReg]]] = {
            block.id: [] for block in rpo
        }

        # Pre-compute label strings — each block's label is needed in multiple
        # places (phi-move pre-computation and terminator emission).
        labels: dict[int, str] = {block.id: self._label(block) for block in rpo}

        # Pre-compute param and free-var register lookups from the entry block,
        # so SelfLoopTerm handling can do O(1) lookups instead of linear scans.
        param_regs: dict[int, MenaiVCodeReg] = {}
        param_reg_ids: list[int] = []
        freevar_regs: dict[str, MenaiVCodeReg] = {}
        free_var_reg_ids: list[int] = []
        for instr in func.blocks[0].instrs:
            if isinstance(instr, MenaiCFGParamInstr):
                param_regs[instr.index] = self._reg(instr.result)
                param_reg_ids.append(self._reg(instr.result).id)

            elif isinstance(instr, MenaiCFGFreeVarInstr):
                freevar_regs[instr.var_name] = self._reg(instr.result)
                free_var_reg_ids.append(self._reg(instr.result).id)

        # Collect register IDs of values defined outside a loop but used
        # inside it.  Such a value must survive across the back-edge, so the
        # slot allocator treats it as permanently live.  Each loop is handled
        # independently because a function may contain several loops (e.g. an
        # inlined higher-order function's loop alongside the caller's own),
        # each with its own preamble.
        hoisted_reg_ids: list[int] = []
        seen_headers: set[int] = set()
        for block in func.blocks:
            term = block.terminator
            if not isinstance(term, MenaiCFGSelfLoopTerm) or term.target is None:
                continue

            header = term.target
            if header in seen_headers:
                continue

            seen_headers.add(header)
            for reg_id in self._hoisted_value_ids(func, by_id[header]):
                if reg_id not in hoisted_reg_ids:
                    hoisted_reg_ids.append(reg_id)

        # Loop-carried variables of a MenaiIRLoop back-edge.  Each param_val is
        # a phi result in the loop-entry block, written by the back-edge move
        # and read at the top of the next iteration.  They are collected
        # separately from hoisted values because the slot allocator treats them
        # like function params: fixed slots, live from the start of the
        # function, never reused.
        loop_param_reg_ids: list[int] = []
        for block in func.blocks:
            term = block.terminator
            if isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is not None:
                for param_val in term.param_vals:
                    reg_id = self._reg(param_val).id
                    if reg_id not in loop_param_reg_ids:
                        loop_param_reg_ids.append(reg_id)

        for block in rpo:
            term = block.terminator
            successors: list[int] = []
            if isinstance(term, MenaiCFGJumpTerm):
                successors = [term.target]

            elif isinstance(term, MenaiCFGBranchTerm):
                successors = [term.true_block, term.false_block]

            elif isinstance(term, MenaiCFGSwitchTerm):
                successors = [t for t in term.targets if t is not None]
                successors.append(term.default_block)

            elif isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is not None:
                # A MenaiIRLoop back-edge carries its loop-carried updates
                # directly: param_vals[i] is the loop-carried variable and
                # args[i] is its new value.  Emit one move per pair rather than
                # deriving them from the target block's phis, so the updates
                # survive a retarget that points the self-loop at a block with
                # no phis (loop rotation targets a copy of the test).  A
                # function-level self-loop (param_vals is None) updates the
                # function's param registers directly and contributes no phi
                # moves.
                for param_val, arg_val in zip(term.param_vals, term.args):
                    dst = self._reg(param_val)
                    src = self._reg(arg_val)
                    phi_moves[block.id].append((dst, src))

                successors = []

            for succ_id in successors:
                for instr in by_id[succ_id].instrs:
                    if not isinstance(instr, MenaiCFGPhiInstr):
                        break

                    for inc_val, inc_pred in instr.incoming:
                        if inc_pred == block.id:
                            dst = self._reg(instr.result)
                            src = self._reg(inc_val)
                            phi_moves[block.id].append((dst, src))

        # Emit instructions for each block in RPO order.
        instrs: list[MenaiVCodeInstr] = []
        # Seed the register counter above every SSA value id in the function.
        # Lowering allocates synthetic registers (e.g. the field-index constant
        # for struct-get-indexed / struct-with-indexed) with ids of
        # max_reg_id + 1.  Seeding from the function-wide maximum guarantees
        # those ids cannot collide with any SSA value id, including ids that
        # are only reached later in the RPO walk.
        max_reg_id = self._max_value_id(func)

        for i, block in enumerate(rpo):
            next_block = rpo[i + 1] if i + 1 < len(rpo) else None

            # Emit a label for every block except the entry block.
            # The entry block has no predecessor that jumps to it by label.
            if i > 0:
                instrs.append(MenaiVCodeLabel(name=labels[block.id]))

            # Emit non-terminator instructions.
            for cfg_instr in block.instrs:
                if isinstance(cfg_instr, MenaiCFGPhiInstr):
                    # Phis are eliminated — their results are written by moves
                    # in predecessor blocks.  Track the max reg id for the
                    # phi result so the allocator knows about it.
                    max_reg_id = max(max_reg_id, cfg_instr.result.id)
                    continue

                max_reg_id = self._lower_instr(cfg_instr, instrs, max_reg_id)

            # Emit patch instructions.
            for patch in block.patch_instrs:
                vi = MenaiVCodePatchClosure(
                    closure=self._reg(patch.closure),
                    capture_index=patch.capture_index,
                    value=MenaiVCodeOperand.of_reg(self._reg(patch.value)),
                )
                instrs.append(vi)
                max_reg_id = max(max_reg_id, patch.closure.id, patch.value.id)

            # Emit phi-elimination moves before the terminator.
            for dst, src in phi_moves[block.id]:
                if dst.id != src.id:
                    instrs.append(MenaiVCodeMove(dst=dst, src=src, is_phi_move=True))

                max_reg_id = max(max_reg_id, dst.id, src.id)

            # Emit the terminator.
            term = block.terminator
            assert term is not None, f"VCodeBuilder: block {block.id} has no terminator"

            if isinstance(term, MenaiCFGReturnTerm):
                instrs.append(MenaiVCodeReturn(value=MenaiVCodeOperand.of_reg(self._reg(term.value))))
                max_reg_id = max(max_reg_id, term.value.id)

            elif isinstance(term, MenaiCFGJumpTerm):
                target = term.target
                # Omit the jump if the target is the immediately next block.
                if next_block is None or next_block.id != target:
                    instrs.append(MenaiVCodeJump(label=labels[target]))

            elif isinstance(term, MenaiCFGBranchTerm):
                cond = self._reg(term.cond)
                max_reg_id = max(max_reg_id, term.cond.id)
                next_id = next_block.id if next_block is not None else -1

                if next_id == term.false_block:
                    # False block falls through — emit JUMP_IF_TRUE to true block.
                    instrs.append(MenaiVCodeJumpIfTrue(cond=cond, label=labels[term.true_block]))

                elif next_id == term.true_block:
                    # True block falls through — emit JUMP_IF_FALSE to false block.
                    instrs.append(MenaiVCodeJumpIfFalse(cond=cond, label=labels[term.false_block]))

                else:
                    # Neither falls through — emit conditional + unconditional jump.
                    instrs.append(MenaiVCodeJumpIfFalse(cond=cond, label=labels[term.false_block]))
                    instrs.append(MenaiVCodeJump(label=labels[term.true_block]))

            elif isinstance(term, MenaiCFGTailCallTerm):
                instrs.append(MenaiVCodeTailCall(
                    func=MenaiVCodeOperand.of_reg(self._reg(term.func)),
                    args=tuple(self._reg(a) for a in term.args),
                ))
                max_reg_id = max(max_reg_id, term.func.id,
                                 *(a.id for a in term.args) if term.args else [-1])

            elif isinstance(term, MenaiCFGTailApplyTerm):
                instrs.append(MenaiVCodeTailApply(
                    func=self._reg(term.func),
                    arg_list=self._reg(term.arg_list),
                ))
                max_reg_id = max(max_reg_id, term.func.id, term.arg_list.id)

            elif isinstance(term, MenaiCFGSelfLoopTerm):
                # Self-loop: jump back to the loop entry.  When the loop has
                # been rotated (the terminator carries a target), the jump
                # targets that block's own label.  Otherwise it targets the
                # function entry via the "__entry__" sentinel, which the
                # bytecode emitter resolves to instruction index 0.
                for arg in term.args:
                    max_reg_id = max(max_reg_id, arg.id)

                if term.param_vals is not None:
                    # MenaiIRLoop back-edge.  The loop-carried variables are phi
                    # results in the loop-entry block; the back-edge arm of each
                    # phi is emitted as a phi move before this terminator (the
                    # loop-entry block is a phi-move successor of this block).
                    # Here we only track the register ids and emit the jump.
                    for param_val in term.param_vals:
                        max_reg_id = max(max_reg_id, param_val.id)

                    assert term.target is not None
                    instrs.append(MenaiVCodeJump(label=labels[term.target], is_self_loop=True))

                else:
                    # Function-level self-loop: move args into the function's
                    # param registers.  Always emitting these (even for
                    # unchanged params, where src == dst) is essential for
                    # correct liveness: the allocator uses the instruction list
                    # to compute last-use indices, so a param whose slot was
                    # reused mid-body must have a use recorded here or its slot
                    # will be freed too early.  The peephole pass eliminates any
                    # move that resolves to the same slot.
                    #
                    # A variadic function has one rest parameter that receives
                    # the excess arguments as a list, so its args do not map
                    # positionally onto its param registers.  The fixed prefix
                    # moves positionally; the rest are packed into a fresh list
                    # which is moved into the rest-param register.  This mirrors
                    # the packing call_setup performs for a variadic call, which
                    # a self-loop jump cannot use because it does not go through
                    # a call.
                    #
                    # The moves are a parallel assignment, so the bytecode
                    # builder must see them as one contiguous group of MOVE
                    # instructions.  The packing MAKE_LIST is emitted before the
                    # group so that it reads the rest arguments before any move
                    # overwrites a param register they might name.
                    if func.is_variadic:
                        min_arity = len(func.params) - 1

                    else:
                        min_arity = len(func.params)

                    if func.is_variadic:
                        rest_args = tuple(self._reg(a) for a in term.args[min_arity:])
                        rest_reg = MenaiVCodeReg(id=max_reg_id + 1)
                        max_reg_id = rest_reg.id
                        instrs.append(MenaiVCodeMakeList(dst=rest_reg, args=rest_args))

                    for idx, arg_val in enumerate(term.args[:min_arity]):
                        param_reg = param_regs[idx]
                        arg_reg = self._reg(arg_val)
                        instrs.append(MenaiVCodeMove(dst=param_reg, src=arg_reg))

                    if func.is_variadic:
                        instrs.append(MenaiVCodeMove(dst=param_regs[min_arity], src=rest_reg))

                    # Emit self-moves for free vars.  Free vars do not appear
                    # in the self-loop args (they are captured and never
                    # reassigned), but their slots must remain live to the
                    # back-edge for the same reason as params above.
                    for free_var in func.free_vars:
                        fv_reg = freevar_regs[free_var]
                        instrs.append(MenaiVCodeMove(dst=fv_reg, src=fv_reg))
                        max_reg_id = max(max_reg_id, fv_reg.id)

                    jump_label = labels[term.target] if term.target is not None else "__entry__"

                    instrs.append(MenaiVCodeJump(label=jump_label, is_self_loop=True))

            elif isinstance(term, MenaiCFGRaiseTerm):
                msg_reg = self._reg(term.message)
                instrs.append(MenaiVCodeRaise(message=MenaiVCodeOperand.of_reg(msg_reg)))
                max_reg_id = max(max_reg_id, term.message.id)

            elif isinstance(term, MenaiCFGSwitchTerm):
                src_reg = self._reg(term.value)
                default = labels[term.default_block]
                instrs.append(MenaiVCodeSwitch(
                    src=src_reg,
                    min=term.min,
                    labels=tuple(labels[t] if t is not None else default for t in term.targets),
                    default_label=default,
                ))
                max_reg_id = max(max_reg_id, term.value.id)

            else:
                raise TypeError(
                    f"MenaiVCodeBuilder: unhandled terminator {type(term).__name__}"
                )

        return MenaiVCodeFunction(
            instrs=tuple(instrs),
            params=tuple(func.params),
            free_vars=tuple(func.free_vars),
            param_reg_ids=tuple(param_reg_ids),
            free_var_reg_ids=tuple(free_var_reg_ids),
            loop_param_reg_ids=tuple(loop_param_reg_ids),
            hoisted_reg_ids=tuple(hoisted_reg_ids),
            is_variadic=func.is_variadic,
            binding_name=func.binding_name,
            reg_count=max_reg_id + 1,
            source_line=func.source_line,
            source_file=func.source_file,
        )

    def _lower_instr(
        self,
        instr: object,
        instrs: list[MenaiVCodeInstr],
        max_reg_id: int,
    ) -> int:
        """
        Lower a single CFG instruction, appending to `instrs`.

        Returns the updated max_reg_id.
        """
        if isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr)):
            # Params and free vars occupy fixed slots assigned by the allocator.
            # No instruction needed — their registers are pre-assigned.
            return max_reg_id

        if isinstance(instr, MenaiCFGConstInstr):
            dst = self._reg(instr.result)
            instrs.append(MenaiVCodeLoadConst(dst=dst, value=instr.value))
            return max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGBuiltinInstr):
            dst = self._reg(instr.result)
            operands = tuple(MenaiVCodeOperand.of_reg(self._reg(a)) for a in instr.args)
            instrs.append(MenaiVCodeBuiltin(dst=dst, op=instr.op, args=operands))
            if not operands:
                return max(max_reg_id, dst.id)

            return max(max_reg_id, dst.id, *(o.reg.id for o in operands if o.reg is not None))

        if isinstance(instr, MenaiCFGCallInstr):
            dst = self._reg(instr.result)
            func_reg = self._reg(instr.func)
            args = tuple(self._reg(a) for a in instr.args)
            instrs.append(MenaiVCodeCall(dst=dst, func=MenaiVCodeOperand.of_reg(func_reg), args=args))
            return max(max_reg_id, dst.id, func_reg.id, *(r.id for r in args)) if args else max(max_reg_id, dst.id, func_reg.id)

        if isinstance(instr, MenaiCFGApplyInstr):
            dst = self._reg(instr.result)
            func_reg = self._reg(instr.func)
            arg_list = self._reg(instr.arg_list)
            instrs.append(MenaiVCodeApply(dst=dst, func=func_reg, arg_list=arg_list))
            return max(max_reg_id, dst.id, func_reg.id, arg_list.id)

        if isinstance(instr, MenaiCFGMakeClosureInstr):
            dst = self._reg(instr.result)
            capture_regs = tuple(self._reg(c) for c in instr.captures)
            captures = tuple(MenaiVCodeOperand.of_reg(r) for r in capture_regs)
            child_vcode = self._lower_function(instr.function)
            instrs.append(MenaiVCodeMakeClosure(
                dst=dst, function=child_vcode, captures=captures, needs_patching=instr.needs_patching
            ))
            return max(max_reg_id, dst.id, *(r.id for r in capture_regs)) if capture_regs else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGPatchClosureInstr):
            closure = self._reg(instr.closure)
            value = self._reg(instr.value)
            instrs.append(MenaiVCodePatchClosure(
                closure=closure,
                capture_index=instr.capture_index,
                value=MenaiVCodeOperand.of_reg(value),
            ))
            return max(max_reg_id, closure.id, value.id)

        if isinstance(instr, MenaiCFGMakeStructInstr):
            dst = self._reg(instr.result)
            args = tuple(self._reg(a) for a in instr.args)
            instrs.append(MenaiVCodeMakeStruct(dst=dst, struct_type=instr.struct_type, args=args))
            return max(max_reg_id, dst.id, *(r.id for r in args)) if args else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGMakeEnumInstr):
            dst = self._reg(instr.result)
            instrs.append(MenaiVCodeMakeEnum(dst=dst, enum_type=instr.enum_type, variant_index=instr.variant_index))
            return max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGStructGetIndexedInstr):
            dst = self._reg(instr.result)
            struct_reg = self._reg(instr.struct)
            index = MenaiVCodeOperand.of_const(MenaiInteger(value=instr.index))
            instrs.append(MenaiVCodeStructGetIndexed(dst=dst, struct=struct_reg, index=index))
            return max(max_reg_id, dst.id, struct_reg.id)

        if isinstance(instr, MenaiCFGStructWithIndexedInstr):
            dst = self._reg(instr.result)
            struct_reg = self._reg(instr.struct)
            value_reg = self._reg(instr.value)
            index = MenaiVCodeOperand.of_const(MenaiInteger(value=instr.index))
            instrs.append(MenaiVCodeStructWithIndexed(
                dst=dst, struct=struct_reg, index=index, value=value_reg,
            ))
            return max(max_reg_id, dst.id, struct_reg.id, value_reg.id)

        if isinstance(instr, MenaiCFGMakeListInstr):
            dst = self._reg(instr.result)
            args = tuple(self._reg(a) for a in instr.args)
            instrs.append(MenaiVCodeMakeList(dst=dst, args=args))
            return max(max_reg_id, dst.id, *(r.id for r in args)) if args else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGMakeVectorInstr):
            dst = self._reg(instr.result)
            args = tuple(self._reg(a) for a in instr.args)
            instrs.append(MenaiVCodeMakeVector(dst=dst, args=args))
            return max(max_reg_id, dst.id, *(r.id for r in args)) if args else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGMakeSetInstr):
            dst = self._reg(instr.result)
            args = tuple(self._reg(a) for a in instr.args)
            instrs.append(MenaiVCodeMakeSet(dst=dst, args=args))
            return max(max_reg_id, dst.id, *(r.id for r in args)) if args else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGMakeDictInstr):
            dst = self._reg(instr.result)
            pairs = tuple((self._reg(k), self._reg(v)) for k, v in instr.pairs)
            instrs.append(MenaiVCodeMakeDict(dst=dst, pairs=pairs))
            all_regs = [r for k, v in pairs for r in (k, v)]
            return max(max_reg_id, dst.id, *(r.id for r in all_regs)) if all_regs else max(max_reg_id, dst.id)

        if isinstance(instr, MenaiCFGGuardInstr):
            value_reg = self._reg(instr.value)
            instrs.append(MenaiVCodeGuard(value=value_reg, expected_type=instr.expected_type))
            return max(max_reg_id, value_reg.id)

        raise TypeError(
            f"MenaiVCodeBuilder: unhandled instruction {type(instr).__name__}"
        )

    def _reg(self, value: MenaiCFGValue) -> MenaiVCodeReg:
        """Convert a CFG SSA value to a VCode virtual register."""
        reg = self._reg_cache.get(value.id)
        if reg is None:
            reg = MenaiVCodeReg(id=value.id, hint=value.hint)
            self._reg_cache[value.id] = reg

        return reg

    def _max_value_id(self, func: MenaiCFGFunction) -> int:
        """
        Return the maximum SSA value id used anywhere in func, or -1 if none.

        Lowering allocates synthetic registers with ids of max_reg_id + 1, so
        seeding the register counter from this value keeps those ids above every
        SSA value id in the function.
        """
        highest = -1
        for block in func.blocks:
            for instr in block.instrs:
                result_id = result_id_in_instr(instr)
                if result_id is not None:
                    highest = max(highest, result_id)

                for value_id in value_ids_in_instr(instr):
                    highest = max(highest, value_id)

            if block.terminator is not None:
                for value_id in value_ids_in_term(block.terminator):
                    highest = max(highest, value_id)

        return highest

    def _label(self, block: MenaiCFGBlock) -> str:
        """Return the label string for a CFG block."""
        return f"__{block.id}_{block.label}__"

    def _rpo(self, func: MenaiCFGFunction) -> list[MenaiCFGBlock]:
        """
        Return reachable blocks in reverse post-order.

        A self-loop back-edge (SelfLoopTerm) is normally a back-edge to the
        entry block and is handled by emitting JUMP __entry__, so it is not
        followed.  When the self-loop has an explicit target (set by LICM or
        loop rotation), the target is a real block that may be reachable only
        through the back-edge, so it is followed here to keep it live.

        The result is stably partitioned so that blocks the CFG marks as
        exception blocks come last.  A block is an exception block when it
        appears after every non-exception block in func.blocks -- the ordering
        MenaiCFGOrderExceptionBlocks establishes by moving raise-terminated
        blocks to the end.  Reading the partition from func.blocks rather than
        testing the terminator keeps that pass the single statement of which
        blocks are exception blocks.  Keeping them last in the emission order
        places the cold exception instructions after the hot normal path,
        improving instruction cache locality on the path that actually runs.
        """
        visited: set = set()
        post_order: list[MenaiCFGBlock] = []
        by_id = blocks_by_id(func)

        def dfs(block_id: int) -> None:
            if block_id in visited:
                return

            visited.add(block_id)
            block = by_id[block_id]
            term = block.terminator
            if isinstance(term, MenaiCFGJumpTerm):
                dfs(term.target)

            elif isinstance(term, MenaiCFGBranchTerm):
                dfs(term.true_block)
                dfs(term.false_block)

            elif isinstance(term, MenaiCFGSwitchTerm):
                for t in term.targets:
                    if t is not None:
                        dfs(t)

                dfs(term.default_block)

            elif isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
                dfs(term.target)

            post_order.append(block)

        dfs(func.entry().id)
        post_order.reverse()

        exception_ids = self._exception_block_ids(func)
        if not exception_ids:
            return post_order

        normal = [b for b in post_order if b.id not in exception_ids]
        exception = [b for b in post_order if b.id in exception_ids]
        return normal + exception

    def _exception_block_ids(self, func: MenaiCFGFunction) -> set[int]:
        """
        Return the ids of the function's exception blocks.

        A block is an exception block when it appears after every block that
        is not an exception block in func.blocks.  MenaiCFGOrderExceptionBlocks
        produces exactly this shape by stably moving raise-terminated blocks to
        the end of the list, so the trailing run of the list is the exception
        set.  The entry block is never an exception block: it is pinned first
        by the pass and must stay first in the emission order.  When the
        function has no such trailing run (the pass did not run, or there are
        no raise blocks) the set is empty and no reordering occurs.
        """
        tail_start = len(func.blocks)
        while tail_start > 0:
            term = func.blocks[tail_start - 1].terminator
            if not isinstance(term, MenaiCFGRaiseTerm):
                break

            tail_start -= 1

        entry = func.entry()
        return {
            block.id for block in func.blocks[tail_start:]
            if block is not entry
        }

    def _hoisted_value_ids(
        self,
        func: MenaiCFGFunction,
        header: MenaiCFGBlock,
    ) -> list[int]:
        """
        Return the SSA value ids of instructions defined outside the loop but
        used inside it.  These values are not reassigned by the loop's
        back-edge, but they must survive it, so their slots must stay live —
        like free vars.

        The loop region is the set of blocks reachable from the loop header.
        A value is hoisted when it is defined in a block outside that region
        and used in a block inside it.  Values defined in the loop region are
        not included: they are recomputed each iteration.

        A phi arm is not a use in the block that holds the phi.  Phi
        elimination materialises each arm as a move in the arm's predecessor
        block, so an arm supplied by a predecessor outside the region is used
        outside the region and must not count as a loop-internal use.  Only
        arms whose predecessor is itself inside the region are loop-internal.

        A self-loop terminator whose target is outside the region is an
        enclosing loop's back-edge that merely lives in this loop's exit
        block.  Its args belong to the enclosing loop, so they are not uses by
        this loop and must not count as loop-internal.

        Param and free-var definitions are excluded — the allocator handles
        those separately.
        """
        region = self._loop_region_ids(func, header)

        # Collect value ids defined outside the loop region.
        preamble_ids: set[int] = set()
        for block in func.blocks:
            if block.id in region:
                continue

            for instr in block.instrs:
                result = getattr(instr, 'result', None)
                if result is None:
                    continue

                # Skip params and free vars — they are handled separately.
                if isinstance(instr, (MenaiCFGParamInstr, MenaiCFGFreeVarInstr)):
                    continue

                preamble_ids.add(result.id)

        if not preamble_ids:
            return []

        # Collect value ids used in the loop region.
        used_ids: set[int] = set()
        for block in func.blocks:
            if block.id not in region:
                continue

            for instr in block.instrs:
                if isinstance(instr, MenaiCFGPhiInstr):
                    for inc_val, inc_pred in instr.incoming:
                        if inc_pred in region:
                            used_ids.add(inc_val.id)

                    continue

                used_ids.update(value_ids_in_instr(instr))

            term = block.terminator
            if term is not None:
                # A self-loop terminator whose target is outside the region is
                # an enclosing loop's back-edge that merely lives in this
                # loop's exit block.  Its args are the enclosing loop's
                # loop-carried updates, not uses by this loop, so they must not
                # be counted here: doing so would mark an enclosing loop's
                # back-edge value as hoisted for this loop and pin it to a
                # permanently-live slot.
                if (
                    isinstance(term, MenaiCFGSelfLoopTerm)
                    and term.target is not None
                    and term.target not in region
                ):
                    continue

                used_ids.update(value_ids_in_term(term))

        return sorted(preamble_ids & used_ids)

    def _loop_region_ids(self, func: MenaiCFGFunction, header: MenaiCFGBlock) -> set[int]:
        """
        Return the ids of the blocks that make up the loop headed by *header*.

        The region is the header plus every block reachable from it by
        following ordinary edges (jump, branch, switch).  A self-loop edge is
        followed only when its target is already inside the region.

        That restriction is what makes the region correct for nested and
        sequential loops.  A loop's own back-edge targets its header, which is
        in the region, so it is followed.  A nested loop's back-edge targets a
        block inside the region, so it is followed too.  An *enclosing* loop's
        back-edge targets a block outside the region (a block that precedes
        this header), so it is not followed -- otherwise the whole enclosing
        loop would be pulled into a nested loop's region, and a value defined
        in the enclosing loop but used in the nested loop would be wrongly
        treated as loop-local rather than hoisted.
        """
        by_id = blocks_by_id(func)
        region: set[int] = set()
        stack = [header.id]
        while stack:
            block_id = stack.pop()
            if block_id in region:
                continue

            region.add(block_id)
            block = by_id[block_id]
            term = block.terminator
            if isinstance(term, MenaiCFGJumpTerm):
                stack.append(term.target)

            elif isinstance(term, MenaiCFGBranchTerm):
                stack.append(term.true_block)
                stack.append(term.false_block)

            elif isinstance(term, MenaiCFGSwitchTerm):
                stack.extend(t for t in term.targets if t is not None)
                stack.append(term.default_block)

            elif isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
                if term.target in region:
                    stack.append(term.target)

        return region
