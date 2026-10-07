"""
Control Flow Graph (CFG) data structures for the Menai compiler.

This module defines the SSA-form CFG IR that sits between the IR tree
(menai_ir.py) and the backend code generators.  The code generators
consume this representation.
"""

import itertools
from collections.abc import Callable
from dataclasses import dataclass

from menai.menai_value import MenaiValue, MenaiStructType, MenaiEnumType


@dataclass
class MenaiCFGValue:
    """
    An SSA value — the result of exactly one instruction.

    `id` is unique within the enclosing MenaiCFGFunction.
    `hint` is a human-readable label for debugging (source name, "if_result",
    "call_result", etc.).  It carries no semantic weight.
    """
    id: int
    hint: str = ""

    def __str__(self) -> str:
        if self.hint:
            return f"%{self.id}({self.hint})"

        return f"%{self.id}"

    def __repr__(self) -> str:
        return str(self)

    # MenaiCFGValue objects are used as dict keys (e.g. in phi nodes).
    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MenaiCFGValue):
            return self.id == other.id

        return NotImplemented


@dataclass
class MenaiCFGConstInstr:
    """
    %result = <literal value>

    Covers all constant types: integer, float, complex, string, boolean,
    none, empty list, and quoted values.
    """
    result: MenaiCFGValue
    value: MenaiValue


@dataclass
class MenaiCFGParamInstr:
    """
    %result = param <index>

    Represents a lambda parameter.  `index` is the 0-based position in the
    parameter list.  The VM codegen lowers this to ENTER + LOAD_VAR.
    """
    result: MenaiCFGValue
    index: int
    param_name: str


@dataclass
class MenaiCFGFreeVarInstr:
    """
    %result = free_var <index>

    Loads a captured free variable from the closure's capture list.
    `index` is the position in the combined (sibling + outer) free_vars list
    on the enclosing MenaiCFGFunction.  The VM codegen lowers this to
    LOAD_VAR with the appropriate slot offset.
    """
    result: MenaiCFGValue
    index: int
    var_name: str


@dataclass
class MenaiCFGBuiltinInstr:
    """
    %result = <builtin_op> [%arg, ...]

    A direct builtin operation (opcode-backed).  `op` is the builtin name as
    it appears in the builtin registry (e.g. 'integer+', 'list-first').
    The VM codegen maps `op` to the corresponding Opcode.

    Also covers the variadic BUILD_OPS ('list', 'dict') and the special-cased
    builtins with optional arguments ('range', 'integer->complex', etc.).
    """
    result: MenaiCFGValue
    op: str
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGCallInstr:
    """
    %result = call %func [%arg, ...]

    A non-tail function call.  `func` is the SSA value holding the callable.
    """
    result: MenaiCFGValue
    func: MenaiCFGValue
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGApplyInstr:
    """
    %result = apply %func %arg_list

    A non-tail apply (calls func with arg_list as a Menai list of arguments).
    Lowered to the APPLY opcode by the VM codegen.
    """
    result: MenaiCFGValue
    func: MenaiCFGValue
    arg_list: MenaiCFGValue


@dataclass
class MenaiCFGMakeClosureInstr:
    """
    %result = make_closure <function> [%capture, ...]

    Creates a closure from a nested MenaiCFGFunction and a list of captured
    SSA values.  `captures` is ordered: sibling free vars first, then outer
    free vars, matching the free_vars list on `function`.

    If `captures` is empty the VM codegen may emit LOAD_CONST with a
    pre-built MenaiFunction instead of MAKE_CLOSURE.
    """
    result: MenaiCFGValue
    function: 'MenaiCFGFunction'
    captures: tuple[MenaiCFGValue, ...]
    needs_patching: bool = False
    # When True, the VM must create a mutable closure object (MAKE_CLOSURE)
    # even if `captures` is empty, because PATCH_CLOSURE instructions will
    # fill in sibling captures after all closures in the letrec are created.
    # The total capture slot count is len(function.free_vars).


@dataclass
class MenaiCFGMakeStructInstr:
    """
    %result = make_struct <struct_type> [%field, ...]

    Constructs a new MenaiStruct of type `struct_type` from a list of field
    values.  `struct_type` is known at compile time and is stored directly on
    the instruction rather than being loaded into a register.  The VM codegen
    lowers this to MAKE_STRUCT, staging the type descriptor and field values
    into the outgoing zone.
    """
    result: MenaiCFGValue
    struct_type: MenaiStructType
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGMakeEnumInstr:
    """
    %result = make_enum <enum_type> <variant_index>

    Constructs a new MenaiEnum of type `enum_type` at variant `variant_index`.
    Both the enum type descriptor and the variant index are known at compile time
    and are stored directly on the instruction rather than being loaded into
    registers.  The VM codegen lowers this to MAKE_ENUM, staging the type
    descriptor into the outgoing zone.
    """
    result: MenaiCFGValue
    enum_type: MenaiEnumType
    variant_index: int


@dataclass
class MenaiCFGMakeListInstr:
    """
    %result = make_list [%elem, ...]

    Constructs a new MenaiList from a flat list of element values known at
    compile time to be N elements.  The VM codegen lowers this to MAKE_LIST,
    staging element values into the outgoing zone and allocating the list in
    a single call.
    """
    result: MenaiCFGValue
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGMakeVectorInstr:
    """
    %result = make_vector [%elem, ...]

    Constructs a new MenaiVector from a flat list of element values known at
    compile time to be N elements.  The VM codegen lowers this to MAKE_VECTOR,
    staging element values into the outgoing zone and allocating the vector in
    a single call.
    """
    result: MenaiCFGValue
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGMakeSetInstr:
    """
    %result = make_set [%elem, ...]

    Constructs a new MenaiSet from a flat list of element values.  The VM
    codegen lowers this to MAKE_SET, staging element values into the outgoing
    zone and allocating the set in a single call.
    """
    result: MenaiCFGValue
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGMakeDictInstr:
    """
    %result = make_dict [(%key, %val), ...]

    Constructs a new MenaiDict from a flat list of key-value pairs.  The VM
    codegen lowers this to MAKE_DICT, staging pairs into the outgoing zone
    (k0, v0, k1, v1, ...) and allocating the dict in a single call.
    """
    result: MenaiCFGValue
    pairs: tuple[tuple[MenaiCFGValue, MenaiCFGValue], ...]


@dataclass
class MenaiCFGStructGetIndexedInstr:
    """
    %result = struct_get_indexed %struct, index

    Reads the field at compile-time index `index` from `struct`.  Emitted by the
    interprocedural type analysis when it resolves a name-based struct-get to a
    constant index.  This is a compiler-internal optimisation target: it has no
    builtin name and is not reachable from source.  The VM codegen lowers it to
    STRUCT_GET_INDEXED.
    """
    result: MenaiCFGValue
    struct: MenaiCFGValue
    index: int


@dataclass
class MenaiCFGStructWithIndexedInstr:
    """
    %result = struct_with_indexed %struct, index, %value

    Returns a new struct with the field at compile-time index `index` set to
    `value`.  Emitted by the interprocedural type analysis when it resolves a
    name-based struct-with to a constant index.  This is a compiler-internal
    optimisation target: it has no builtin name and is not reachable from
    source.  The VM codegen lowers it to STRUCT_WITH_INDEXED.
    """
    result: MenaiCFGValue
    struct: MenaiCFGValue
    index: int
    value: MenaiCFGValue


@dataclass
class MenaiCFGPatchClosureInstr:
    """
    patch_closure %closure, capture_index, %value

    Installs `value` into capture slot `capture_index` of `closure`.
    Used exclusively during letrec initialisation to break mutual-recursion
    cycles.  The VM codegen lowers this to PATCH_CLOSURE.

    This instruction has no result (it is a side-effecting mutation of the
    closure object).  It is the only instruction in the CFG that does not
    produce an SSA value, but because Menai closures are the only mutable
    objects (and only during initialisation), this does not violate the
    SSA invariant for values.
    """
    closure: MenaiCFGValue
    capture_index: int
    value: MenaiCFGValue


@dataclass
class MenaiCFGGuardInstr:
    """
    Guard: assert that `value` has type `expected_type` at runtime.

    If the runtime type does not match, the VM raises MENAI_ERR_TYPE_MISMATCH.
    Otherwise the instruction is a no-op — it does not produce a new value.

    Inserted by the CFG type propagation pass.  The operational opcodes
    (INTEGER_ADD, FLOAT_MUL, etc.) rely on guards having already verified
    operand types and skip their own type checks for performance.

    The guard does not define a result value — it validates the existing
    SSA value in place.  After a guard executes, the type propagation pass
    marks the value's type as known for subsequent instructions.
    """
    value: MenaiCFGValue
    expected_type: str


@dataclass
class MenaiCFGPhiInstr:
    """
    %result = phi [(%value_from_block, block), ...]

    Standard SSA phi node.  Each entry pairs an incoming SSA value with the
    id of the predecessor block it comes from.  For Menai, phi nodes appear
    only at `if` join points (one phi per if expression).

    VM codegen: both predecessor blocks leave their value on the stack, so
    the phi emits no instructions — the join block simply continues.

    Native codegen: maps directly to an LLVM phi instruction.
    """
    result: MenaiCFGValue
    incoming: tuple[tuple[MenaiCFGValue, int], ...]


# Union of all non-terminator instruction types.
# MenaiCFGPatchClosureInstr is intentionally excluded — it has no result and
# is stored in MenaiCFGBlock.patch_instrs rather than MenaiCFGBlock.instrs.
MenaiCFGInstr = (  # pylint: disable=invalid-name
    MenaiCFGConstInstr
    | MenaiCFGParamInstr
    | MenaiCFGFreeVarInstr
    | MenaiCFGBuiltinInstr
    | MenaiCFGCallInstr
    | MenaiCFGApplyInstr
    | MenaiCFGMakeStructInstr
    | MenaiCFGMakeEnumInstr
    | MenaiCFGMakeListInstr
    | MenaiCFGMakeVectorInstr
    | MenaiCFGMakeSetInstr
    | MenaiCFGMakeDictInstr
    | MenaiCFGStructGetIndexedInstr
    | MenaiCFGStructWithIndexedInstr
    | MenaiCFGMakeClosureInstr
    | MenaiCFGPatchClosureInstr
    | MenaiCFGGuardInstr
    | MenaiCFGPhiInstr
)


@dataclass
class MenaiCFGJumpTerm:
    """Unconditional jump to the block with id `target`."""
    target: int


@dataclass
class MenaiCFGBranchTerm:
    """
    Conditional branch on `cond`.

    Jumps to `true_block` if cond is truthy, `false_block` otherwise.  Both are
    block ids.
    Lowered to JUMP_IF_FALSE by the VM codegen.
    """
    cond: MenaiCFGValue
    true_block: int
    false_block: int


@dataclass
class MenaiCFGSwitchTerm:
    """
    Dense integer switch on `value`.

    Lowered to the SWITCH_INTEGER opcode by the VM codegen.  `targets[i]` is the
    id of the block jumped to when the scrutinee equals `min + i`; entries may be
    None, meaning that value falls through to `default_block`.  The scrutinee is
    guaranteed integer (an integer guard is inserted by MenaiCFGGuardInsertion
    when the type is not statically known), so no runtime type dispatch is needed.
    """
    value: MenaiCFGValue
    min: int
    targets: tuple[int | None, ...]
    default_block: int


@dataclass
class MenaiCFGSwitchEnumTerm:
    """
    Dense enum switch on `value`.

    Lowered to the SWITCH_ENUM opcode by the VM codegen.  `targets[i]` is the id
    of the block jumped to when the scrutinee is the variant at index i.  Unlike
    the integer switch there is no `min` and no None holes: enum variant indices
    are dense (0..nvariants-1) by construction, so the table always covers the
    whole range.  The scrutinee is guaranteed enum (an enum guard is inserted by
    MenaiCFGGuardInsertion when the type is not statically known), so no runtime
    type dispatch is needed.
    """
    value: MenaiCFGValue
    targets: tuple[int, ...]
    default_block: int


@dataclass
class MenaiCFGReturnTerm:
    """Return `value` from the current function."""
    value: MenaiCFGValue


@dataclass
class MenaiCFGTailCallTerm:
    """
    Tail call to `func` with `args`.

    Lowered to TAIL_CALL by the VM codegen.
    """
    func: MenaiCFGValue
    args: tuple[MenaiCFGValue, ...]


@dataclass
class MenaiCFGTailApplyTerm:
    """
    Tail apply: call `func` with `arg_list` as a Menai list.

    Lowered to TAIL_APPLY by the VM codegen.
    """
    func: MenaiCFGValue
    arg_list: MenaiCFGValue


@dataclass
class MenaiCFGSelfLoopTerm:
    """
    Direct self-recursive tail call.

    The callee is the enclosing function itself, or (when ``param_vals`` is
    set) a MenaiIRLoop whose loop-carried variables are being updated.  `args`
    are the new argument values, in parameter order.  The VM codegen lowers
    this to JUMP (after storing args into the destination slots).

    `target` is the block the self-loop jumps to.  When None (the default),
    the self-loop targets the entry block (block 0).  When set (by the type
    propagation pass's loop-invariant guard hoisting, by loop rotation, or by
    the CFG builder's MenaiIRLoop lowering), the self-loop targets a
    loop-entry block that skips hoisted guards in the preamble.

    `param_vals` is the list of destination SSA values for the back-edge
    moves.  When None (the default), the VCode builder uses the function's
    parameter registers (from MenaiCFGParamInstr in the entry block).  When
    set (by the CFG builder's MenaiIRLoop lowering), these are the
    loop-carried variable SSA values that the back-edge moves write to.
    """
    args: tuple[MenaiCFGValue, ...]
    param_vals: tuple['MenaiCFGValue', ...] | None = None
    target: int | None = None


@dataclass
class MenaiCFGRaiseTerm:
    """
    Raise a runtime error with a value from a register.

    Lowered to RAISE_ERROR by the VM codegen.
    """
    message: MenaiCFGValue


# Union of all terminator types.
MenaiCFGTerminator = (  # pylint: disable=invalid-name
    MenaiCFGJumpTerm
    | MenaiCFGBranchTerm
    | MenaiCFGSwitchTerm
    | MenaiCFGSwitchEnumTerm
    | MenaiCFGReturnTerm
    | MenaiCFGTailCallTerm
    | MenaiCFGTailApplyTerm
    | MenaiCFGSelfLoopTerm
    | MenaiCFGRaiseTerm
)


@dataclass
class MenaiCFGBlock:
    """
    A basic block: a maximal straight-line sequence of instructions with a
    single entry point and a single exit (the terminator).

    Fields
    ------
    id          : unique integer within the enclosing MenaiCFGFunction (0 = entry)
    label       : human-readable name for debugging ("entry", "then_0", etc.)
    instrs      : non-terminator instructions, in emission order
    patch_instrs: MenaiCFGPatchClosureInstr instructions for letrec fixup,
                  emitted after `instrs` but before the terminator
    terminator  : the block's single exit instruction (set by the builder)

    A block is an immutable value.  A pass that changes a block constructs a
    new one; it must not modify an existing block.  Predecessors are not stored
    on the block: they are derived from the terminators by `predecessors`.
    """
    id: int
    label: str
    instrs: tuple[MenaiCFGInstr, ...] = ()
    patch_instrs: tuple[MenaiCFGPatchClosureInstr, ...] = ()
    terminator: MenaiCFGTerminator | None = None

    def __repr__(self) -> str:
        lines = [f"block {self.id} ({self.label}):"]
        for instr in self.instrs:
            lines.append(f"  {_fmt_instr(instr)}")

        for patch in self.patch_instrs:
            lines.append(f"  patch_closure {patch.closure} [{patch.capture_index}] = {patch.value}")

        if self.terminator is not None:
            lines.append(f"  {_fmt_term(self.terminator)}")

        return "\n".join(lines)


_fact_key_counter = itertools.count(1)


@dataclass
class MenaiCFGFunction:
    """
    The CFG for a single lambda (or the top-level module body).

    `blocks` is ordered with the entry block first.  The builder appends
    blocks in construction order; the VM codegen performs its own traversal.

    Fields
    ------
    blocks       : all basic blocks, entry block at index 0
    params       : parameter names, in order (parallel to param_count)
    free_vars    : captured variable names, sibling free vars first then outer
                   free vars, matching the capture order in MakeClosureInstr
    is_variadic  : True if the last parameter is a rest parameter
    binding_name : the name this lambda is bound to, if any (for self-loop
                   detection and debug names)
    source_line  : source line where the lambda is defined
    source_file  : source file where the lambda is defined

    A function is an immutable value.  A pass that changes a function
    constructs a new one.  Per-value type facts are analysis output, not part
    of the program structure, so they are not stored here; they travel
    alongside the CFG in the pass context.

    `fact_key` is the stable identity that keys that analysis output.  It is
    assigned once, at construction, and preserved by `dataclasses.replace`, so
    a function rebuilt by a pass keeps the identity its facts were recorded
    under.  It must not be derived from `id()`, which is only valid while the
    object is alive and can be reused by a later object once it is freed.
    """
    blocks: tuple[MenaiCFGBlock, ...] = ()
    params: tuple[str, ...] = ()
    free_vars: tuple[str, ...] = ()
    is_variadic: bool = False
    binding_name: str | None = None
    source_line: int = 0
    source_file: str = ""
    fact_key: int | None = None

    def __post_init__(self) -> None:
        """Assign a stable identity when one was not carried in by a rebuild."""
        if self.fact_key is None:
            self.fact_key = next(_fact_key_counter)

    def entry(self) -> MenaiCFGBlock:
        """The entry block (always the first block)."""
        return self.blocks[0]

    def param_count(self) -> int:
        """Get the number of parameters (length of the params list)."""
        return len(self.params)

    def __repr__(self) -> str:
        name = self.binding_name or "<lambda>"
        lines = [f"MenaiCFGFunction {name}({', '.join(self.params)}):"]
        if self.free_vars:
            lines.append(f"  free_vars: {self.free_vars}")

        for block in self.blocks:
            lines.append(repr(block))

        return "\n".join(lines)


def _fmt_values(vs: tuple[MenaiCFGValue, ...]) -> str:
    """Format a list of CFG values as a bracketed comma-separated string."""
    return "[" + ", ".join(str(v) for v in vs) + "]"


def _fmt_instr(instr: MenaiCFGInstr) -> str:
    """One-line human-readable representation of a non-terminator instruction."""
    if isinstance(instr, MenaiCFGConstInstr):
        return f"{instr.result} = const {instr.value!r}"

    if isinstance(instr, MenaiCFGParamInstr):
        return f"{instr.result} = param {instr.index} ({instr.param_name!r})"

    if isinstance(instr, MenaiCFGFreeVarInstr):
        return f"{instr.result} = free_var {instr.index} ({instr.var_name!r})"

    if isinstance(instr, MenaiCFGBuiltinInstr):
        return f"{instr.result} = builtin {instr.op!r} {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGCallInstr):
        return f"{instr.result} = call {instr.func} {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGApplyInstr):
        return f"{instr.result} = apply {instr.func} {instr.arg_list}"

    if isinstance(instr, MenaiCFGMakeClosureInstr):
        name = instr.function.binding_name or "<lambda>"
        return f"{instr.result} = make_closure {name!r} {_fmt_values(instr.captures)}"

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return f"{instr.result} = make_struct {instr.struct_type.name!r} {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGMakeEnumInstr):
        return f"{instr.result} = make_enum {instr.enum_type.name!r} {instr.variant_index}"

    if isinstance(instr, MenaiCFGMakeListInstr):
        return f"{instr.result} = make_list {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGMakeVectorInstr):
        return f"{instr.result} = make_vector {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return f"{instr.result} = make_set {_fmt_values(instr.args)}"

    if isinstance(instr, MenaiCFGMakeDictInstr):
        pairs_str = ", ".join(f"({k}, {v})" for k, v in instr.pairs)
        return f"{instr.result} = make_dict [{pairs_str}]"

    if isinstance(instr, MenaiCFGStructGetIndexedInstr):
        return f"{instr.result} = struct_get_indexed {instr.struct} [{instr.index}]"

    if isinstance(instr, MenaiCFGStructWithIndexedInstr):
        return f"{instr.result} = struct_with_indexed {instr.struct} [{instr.index}] = {instr.value}"

    if isinstance(instr, MenaiCFGPatchClosureInstr):
        return f"patch_closure {instr.closure} [{instr.capture_index}] = {instr.value}"

    if isinstance(instr, MenaiCFGGuardInstr):
        return f"guard {instr.value} is {instr.expected_type}"

    if isinstance(instr, MenaiCFGPhiInstr):
        parts = ", ".join(f"{v} <- block{b}" for v, b in instr.incoming)
        return f"{instr.result} = phi [{parts}]"

    return f"<unknown instr {type(instr).__name__}>"


def _fmt_term(term: MenaiCFGTerminator) -> str:
    """One-line human-readable representation of a terminator."""
    if isinstance(term, MenaiCFGJumpTerm):
        return f"jump block{term.target}"

    if isinstance(term, MenaiCFGBranchTerm):
        return (f"branch {term.cond} → block{term.true_block} / "
                f"block{term.false_block}")

    if isinstance(term, MenaiCFGSwitchTerm):
        arms = ", ".join(
            f"{term.min + i}: block{t}" if t is not None else f"{term.min + i}: default"
            for i, t in enumerate(term.targets)
        )
        return f"switch {term.value} min={term.min} [{arms}] default=block{term.default_block}"

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        arms = ", ".join(
            f"{i}: block{t}" for i, t in enumerate(term.targets)
        )
        return f"switch-enum {term.value} [{arms}] default=block{term.default_block}"

    if isinstance(term, MenaiCFGReturnTerm):
        return f"return {term.value}"

    if isinstance(term, MenaiCFGTailCallTerm):
        return f"tail_call {term.func} {_fmt_values(term.args)}"

    if isinstance(term, MenaiCFGTailApplyTerm):
        return f"tail_apply {term.func} {term.arg_list}"

    if isinstance(term, MenaiCFGSelfLoopTerm):
        target = f" → block{term.target}" if term.target is not None else ""
        return f"self_loop{_fmt_values(term.args)}{target}"

    if isinstance(term, MenaiCFGRaiseTerm):
        return f"raise {term.message}"

    return f"<unknown term {type(term).__name__}>"


def blocks_by_id(func: MenaiCFGFunction) -> dict[int, MenaiCFGBlock]:
    """Return a map from block id to block for every block in `func`."""
    return {block.id: block for block in func.blocks}


def successor_ids(term: MenaiCFGTerminator | None) -> list[int]:
    """
    Return the ids of the blocks a terminator can transfer control to.

    A `MenaiCFGSelfLoopTerm` with no target transfers control to the entry
    block (id 0); callers that need that edge must account for it, so the
    entry-block edge is included here as id 0.
    """
    if term is None:
        return []

    if isinstance(term, MenaiCFGJumpTerm):
        return [term.target]

    if isinstance(term, MenaiCFGBranchTerm):
        return [term.true_block, term.false_block]

    if isinstance(term, MenaiCFGSwitchTerm):
        targets = [t for t in term.targets if t is not None]
        targets.append(term.default_block)
        return targets

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        targets = list(term.targets)
        targets.append(term.default_block)
        return targets

    if isinstance(term, MenaiCFGSelfLoopTerm):
        return [term.target if term.target is not None else 0]

    return []


def predecessors(func: MenaiCFGFunction, block: MenaiCFGBlock) -> list[MenaiCFGBlock]:
    """
    Return the blocks that have an edge to `block`.

    Derived from the terminators rather than stored, so it cannot go stale.
    The result is in block-list order.  A block is not its own predecessor
    unless it has an explicit self-edge (a `MenaiCFGSelfLoopTerm` targeting
    itself is not possible, but a jump to itself is).
    """
    return [
        candidate
        for candidate in func.blocks
        if block.id in successor_ids(candidate.terminator)
    ]


def predecessors_by_block(func: MenaiCFGFunction) -> dict[int, list[MenaiCFGBlock]]:
    """
    Map each block id in a function to the blocks that have an edge to it.

    This is `predecessors` for every block at once.  `predecessors` rescans the
    whole block list per query, so a pass that queries it for every block on
    every iteration of a fixed point pays O(blocks) per query.  Building the map
    once makes each query O(predecessors).

    The relation is derived from the terminators, so it is a pure function of
    the function's blocks and cannot go stale while the block list is unchanged.
    """
    result: dict[int, list[MenaiCFGBlock]] = {block.id: [] for block in func.blocks}
    for candidate in func.blocks:
        for successor_id in successor_ids(candidate.terminator):
            if successor_id in result:
                result[successor_id].append(candidate)

    return result


def remap_term(
    term: MenaiCFGTerminator | None,
    remap_block: Callable[[int], int],
) -> MenaiCFGTerminator | None:
    """Return a new terminator with all block-id references remapped."""
    if term is None:
        return None

    if isinstance(term, MenaiCFGJumpTerm):
        new_target = remap_block(term.target)
        if new_target == term.target:
            return term

        return MenaiCFGJumpTerm(target=new_target)

    if isinstance(term, MenaiCFGBranchTerm):
        new_true = remap_block(term.true_block)
        new_false = remap_block(term.false_block)
        if new_true == term.true_block and new_false == term.false_block:
            return term

        return MenaiCFGBranchTerm(
            cond=term.cond,
            true_block=new_true,
            false_block=new_false,
        )

    if isinstance(term, MenaiCFGSwitchTerm):
        new_targets = tuple(remap_block(t) if t is not None else None for t in term.targets)
        new_default = remap_block(term.default_block)
        if new_targets == term.targets and new_default == term.default_block:
            return term

        return MenaiCFGSwitchTerm(
            value=term.value,
            min=term.min,
            targets=new_targets,
            default_block=new_default,
        )

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        new_enum_targets = tuple(remap_block(t) for t in term.targets)
        new_enum_default = remap_block(term.default_block)
        if new_enum_targets == term.targets and new_enum_default == term.default_block:
            return term

        return MenaiCFGSwitchEnumTerm(
            value=term.value,
            targets=new_enum_targets,
            default_block=new_enum_default,
        )

    if isinstance(term, MenaiCFGSelfLoopTerm) and term.target is not None:
        new_target = remap_block(term.target)
        if new_target == term.target:
            return term

        return MenaiCFGSelfLoopTerm(args=term.args, param_vals=term.param_vals, target=new_target)

    return term


def value_ids_in_instr(instr: 'MenaiCFGInstr') -> list[int]:
    """Return all input value ids referenced by an instruction."""
    if isinstance(instr, MenaiCFGBuiltinInstr):
        return [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGCallInstr):
        return [instr.func.id] + [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGApplyInstr):
        return [instr.func.id, instr.arg_list.id]

    if isinstance(instr, MenaiCFGMakeClosureInstr):
        return [c.id for c in instr.captures]

    if isinstance(instr, MenaiCFGMakeStructInstr):
        return [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGMakeEnumInstr):
        return []

    if isinstance(instr, MenaiCFGMakeListInstr):
        return [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGMakeVectorInstr):
        return [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGMakeSetInstr):
        return [a.id for a in instr.args]

    if isinstance(instr, MenaiCFGMakeDictInstr):
        return [i.id for k, v in instr.pairs for i in (k, v)]

    if isinstance(instr, MenaiCFGStructGetIndexedInstr):
        return [instr.struct.id]

    if isinstance(instr, MenaiCFGStructWithIndexedInstr):
        return [instr.struct.id, instr.value.id]

    if isinstance(instr, MenaiCFGPatchClosureInstr):
        return [instr.closure.id, instr.value.id]

    if isinstance(instr, MenaiCFGGuardInstr):
        return [instr.value.id]

    if isinstance(instr, MenaiCFGPhiInstr):
        return [val.id for val, _ in instr.incoming]

    # MenaiCFGConstInstr, MenaiCFGParamInstr, MenaiCFGFreeVarInstr: no input
    # value references here.
    return []


def result_id_in_instr(instr: 'MenaiCFGInstr') -> int | None:
    """
    Return the id of the SSA value instr defines, or None if it defines none.

    Every instruction type in MenaiCFGInstr defines a result except
    MenaiCFGGuardInstr, which validates an existing value in place, and
    MenaiCFGPatchClosureInstr, which mutates a closure during letrec fixup.
    """
    if isinstance(instr, (MenaiCFGGuardInstr, MenaiCFGPatchClosureInstr)):
        return None

    return instr.result.id


def value_ids_in_term(term: 'MenaiCFGTerminator') -> list[int]:
    """Return all input value ids referenced by a terminator."""
    if isinstance(term, MenaiCFGReturnTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGBranchTerm):
        return [term.cond.id]

    if isinstance(term, MenaiCFGSwitchTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGSwitchEnumTerm):
        return [term.value.id]

    if isinstance(term, MenaiCFGTailCallTerm):
        return [term.func.id] + [a.id for a in term.args]

    if isinstance(term, MenaiCFGTailApplyTerm):
        return [term.func.id, term.arg_list.id]

    if isinstance(term, MenaiCFGSelfLoopTerm):
        return [a.id for a in term.args]

    if isinstance(term, MenaiCFGRaiseTerm):
        return [term.message.id]

    # MenaiCFGJumpTerm: no value references.
    return []
