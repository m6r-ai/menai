"""
Instruction-level rendering shared by the bytecode rendering tools.

These helpers turn a code object's metadata and packed bytecode instructions
into human-readable, annotated lines.  They are used by the disassembler and by
the instruction tracer so that both render a code object identically: the same
metadata tables, the same instruction formatting, and the same annotations.
"""

from collections.abc import Callable, Iterator

from menai.menai_value import MenaiFunction, MenaiValue
from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode, reg_name, unpack_instruction
from menai_render.menai_render_colour import cyan, green, grey

_MAX_CONSTANT_LENGTH = 64
_SECTION_WIDTH = 70


# Opcodes after which a blank line is emitted, so that control-flow boundaries
# are visually separated in a listing.
CONTROL_FLOW_OPCODES = frozenset({
    Opcode.JUMP_IF_FALSE, Opcode.JUMP_IF_TRUE, Opcode.CALL, Opcode.APPLY, Opcode.SWITCH_INTEGER,
    Opcode.RETURN_IF_FALSE, Opcode.RETURN_IF_TRUE,
})


def jump_targets(code: CodeObject) -> set[int]:
    """
    Return the instruction indices that are jump targets in a code object.

    A JUMP carries its target in src0; JUMP_IF_FALSE and JUMP_IF_TRUE carry it
    in src1.  SWITCH_INTEGER targets come from its jump table — every arm target
    plus the default.
    """
    switch_targets = {
        t
        for instr in instructions(code)
        if instr.opcode == Opcode.SWITCH_INTEGER and instr.src1 < len(code.jump_tables)
        for t in (*code.jump_tables[instr.src1][2], code.jump_tables[instr.src1][1])
    }
    return {
        instr.src1 if instr.opcode in (Opcode.JUMP_IF_FALSE, Opcode.JUMP_IF_TRUE)
        else instr.src0
        for instr in instructions(code)
        if instr.opcode in (Opcode.JUMP, Opcode.JUMP_IF_FALSE, Opcode.JUMP_IF_TRUE)
    } | switch_targets


def instructions(code: CodeObject) -> Iterator[Instruction]:
    """Yield unpacked Instruction objects from a CodeObject's packed instruction array."""
    for word in code.instructions:
        yield unpack_instruction(word)


def format_constant(const: object) -> str:
    """
    Format a constant for display.

    Menai values are shown as their Menai type name followed by the value's
    canonical Menai display form (its describe() form).  This keeps every
    constant self-identifying: a symbol is shown as a symbol rather than as a
    bare name, and a struct type is shown with its name and fields.  Long values
    are truncated.
    """
    if isinstance(const, str):
        if len(const) > _MAX_CONSTANT_LENGTH:
            return f'"{const[:_MAX_CONSTANT_LENGTH - 3]}..."'

        return f'"{const}"'

    if isinstance(const, MenaiValue):
        val_str = f"{const.type_name()} {const.describe()}"
        if len(val_str) > _MAX_CONSTANT_LENGTH:
            return f'{val_str[:_MAX_CONSTANT_LENGTH - 3]}...'

        return val_str

    return str(const)


def clean_name(name: str) -> str:
    """Strip the '(N param[s])' suffix the bytecode builder appends to closure names."""
    if '(' in name:
        return name[:name.index('(')].strip()

    return name


def _tagged_operands(instr: Instruction) -> Iterator[tuple[int, int]]:
    """
    Yield (position, constant-pool index) for each constant source operand.

    A source position is a constant when the instruction's tag bit for that
    position is set; the field then indexes the constant pool rather than the
    register file.  Position 0 is src0, 1 is src1, 2 is src2.
    """
    for position, value in enumerate((instr.src0, instr.src1, instr.src2)):
        if (instr.tag >> position) & 1:
            yield position, value


def _constant_operand_annotation(instr: Instruction, code: CodeObject) -> str:
    """
    Annotate the constants held in an instruction's tagged source operands.

    Each distinct constant-pool index among the tagged positions is rendered
    with its value, in first-occurrence order; a constant referenced by more
    than one position is shown once.  Returns an empty string when the
    instruction has no tagged source operands.
    """
    seen: set[int] = set()
    parts: list[str] = []
    for _position, index in _tagged_operands(instr):
        if index in seen or index >= len(code.constants):
            continue

        seen.add(index)
        parts.append(format_constant(code.constants[index]))

    if not parts:
        return ""

    return "  ; " + ", ".join(parts)


def _callee_annotation(instr: Instruction, code: CodeObject) -> str:
    """
    Annotate a CALL or TAIL_CALL whose callee is a constant.

    The callee is the src0 operand; when it is tagged it indexes a constant
    MenaiFunction, and the annotation names the function being called.  Returns
    an empty string when the callee is a register.
    """
    if not (instr.tag & 1) or instr.src0 >= len(code.constants):
        return ""

    const = code.constants[instr.src0]
    if isinstance(const, MenaiFunction) and const.name:
        return f"  ; calls '{clean_name(const.name)}'"

    return f"  ; calls {format_constant(const)}"


def annotate_instruction(instr: Instruction, code: CodeObject) -> str:
    """
    Add annotation to instruction showing what it does.

    Opcodes whose operands are dedicated (a constant-pool index, a code-object
    index, a jump-table index) are annotated by name.  Any remaining source
    operand that holds a constant rather than a register — a position whose tag
    bit is set — is annotated with the constant's value, so a folded constant is
    visible wherever it occurs.
    """
    opcode = instr.opcode
    src0 = instr.src0

    annotation = ""

    if opcode == Opcode.LOAD_CONST:
        if src0 < len(code.constants):
            const = code.constants[src0]
            const_str = format_constant(const)
            if len(const_str) > 40:
                const_str = const_str[:37] + "..."

            annotation = f"  ; {const_str}"

    elif opcode == Opcode.LOAD_NONE:
        annotation = "  ; #none"

    elif opcode in (Opcode.LOAD_TRUE, Opcode.LOAD_FALSE):
        val = "#t" if opcode == Opcode.LOAD_TRUE else "#f"
        annotation = f"  ; {val}"

    elif opcode == Opcode.LOAD_EMPTY_LIST:
        annotation = "  ; []"

    elif opcode == Opcode.MAKE_CLOSURE:
        if instr.src0 < len(code.code_objects):
            nested = code.code_objects[instr.src0]
            name = nested.name or f"<lambda-{src0}>"
            loc_parts = []
            if nested.source_file:
                loc_parts.append(nested.source_file)

            if nested.source_line and nested.source_line > 0:
                loc_parts.append(f"line {nested.source_line}")

            line_info = f" at {':'.join(loc_parts)}" if loc_parts else ""
            annotation = f"  ; closure for '{clean_name(name)}'{line_info}"

    elif opcode == Opcode.PATCH_CLOSURE:
        # src0 = closure register, src1 = capture index, src2 = value register.
        # Scan for the MAKE_CLOSURE that produced each register so we can name
        # the closure and the free-var being filled.
        closure_name = None
        free_var_name = None
        for scan_instr in instructions(code):
            if scan_instr.opcode == Opcode.MAKE_CLOSURE and scan_instr.dest == instr.src0:
                nested = code.code_objects[scan_instr.src0]
                closure_name = clean_name(nested.name) if nested.name else reg_name(instr.src0, code)
                if instr.src1 < len(nested.free_vars):
                    free_var_name = nested.free_vars[instr.src1]

                break

        # Name the value being patched in: a constant operand is shown by value,
        # a register holding a known closure by the closure's name, and any other
        # register by its symbolic name.
        if (instr.tag >> 2) & 1 and instr.src2 < len(code.constants):
            rhs = format_constant(code.constants[instr.src2])

        else:
            value_closure_name = None
            for scan_instr in instructions(code):
                if scan_instr.opcode == Opcode.MAKE_CLOSURE and scan_instr.dest == instr.src2:
                    value_closure_name = clean_name(code.code_objects[scan_instr.src0].name)
                    break

            rhs = f"'{value_closure_name}'" if value_closure_name else reg_name(instr.src2, code)

        lhs_closure = closure_name or reg_name(instr.src0, code)
        lhs_capture = f"'{free_var_name}'" if free_var_name else f"capture[{instr.src1}]"
        annotation = f"  ; '{lhs_closure}'.{lhs_capture} = {rhs}"

    elif opcode == Opcode.RAISE_ERROR:
        if (instr.tag & 1) and src0 < len(code.constants):
            msg = code.constants[src0]
            annotation = f"  ; Raise error: {format_constant(msg)[:40]}"

    elif opcode == Opcode.SWITCH_INTEGER:
        if instr.src1 < len(code.jump_tables):
            t_min, t_default, targets = code.jump_tables[instr.src1]
            hi = t_min + len(targets) - 1
            annotation = f"  ; see jt{instr.src1}: {t_min}..{hi} -> arms, else @{t_default}"

    elif opcode in (Opcode.CALL, Opcode.TAIL_CALL):
        annotation = _callee_annotation(instr, code)

    if not annotation:
        annotation = _constant_operand_annotation(instr, code)

    return annotation


def format_instruction(instr: Instruction, index: int, code: CodeObject) -> str:
    """Format an instruction with symbolic register names derived from code."""
    instr_str = f"{index:4}: {instr.format(code)}"
    # Pad to fixed width so annotations align; 48 chars covers the longest opcodes
    return instr_str.ljust(48)


def render_code_metadata(code: CodeObject, indent: str = "", color: bool = False) -> list[str]:
    """
    Render a code object's metadata tables as lines.

    Emits, in order and only when non-empty: the nested code objects table, the
    constants table, the jump tables, the inputs (parameters), the captures
    (free variables), and the local count.  Each section is headed by a
    green title and closed by a grey rule, matching the disassembler's layout so
    that the disassembler and the annotated trace present identical metadata.

    Args:
        code:   The code object whose metadata is rendered.
        indent: Prefix prepended to every line (used for nested code objects).
        color:  Whether to emit ANSI colour codes.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    rule = grey(f"{indent}{'-' * _SECTION_WIDTH}", color)

    if code.code_objects:
        lines.append(f"{indent}{green('Code Objects: ' + str(len(code.code_objects)), color)}")
        lines.append(rule)
        for i, nested in enumerate(code.code_objects):
            nested_name = clean_name(nested.name) if nested.name else f"<lambda-{i}>"
            loc_parts = []
            if nested.source_file:
                loc_parts.append(nested.source_file)

            if nested.source_line and nested.source_line > 0:
                loc_parts.append(f"line {nested.source_line}")

            loc_str = f" [{':'.join(loc_parts)}]" if loc_parts else ""
            coid = f"x{i}"
            lines.append(f"{indent}{cyan(f'{coid:>6}: {nested_name}{loc_str}', color)}")

        lines.append(rule)

    if code.constants:
        lines.append(f"{indent}{green('Constants: ' + str(len(code.constants)), color)}")
        lines.append(rule)
        for i, const in enumerate(code.constants):
            const_str = format_constant(const)
            cid = f"k{i}"
            lines.append(f"{indent}{cyan(f'{cid:>6}: {const_str}', color)}")

        lines.append(rule)

    if code.jump_tables:
        lines.append(f"{indent}{green('Jump Tables: ' + str(len(code.jump_tables)), color)}")
        lines.append(rule)
        for j, (t_min, t_default, targets) in enumerate(code.jump_tables):
            hi = t_min + len(targets) - 1
            jid = f"jt{j}"
            lines.append(
                f"{indent}{cyan(f'{jid:>6}: min={t_min}  default=@{t_default}  span={t_min}..{hi}', color)}"
            )
            for slot, target in enumerate(targets):
                value = t_min + slot
                if target == t_default:
                    lines.append(f"{indent}{cyan(f'       _ : @{target}  (default)', color)}")

                else:
                    lines.append(f"{indent}{cyan(f'{value:>7} : @{target}', color)}")

        lines.append(rule)

    param_count = code.param_count
    if param_count:
        lines.append(f"{indent}{green('Inputs: ' + str(code.param_count), color)}")
        lines.append(rule)
        for i, pname in enumerate(code.param_names):
            rid = f"i{i}"
            label = f"{rid:>6}: '{pname}'"
            lines.append(f"{indent}{cyan(label, color)}")

        lines.append(rule)

    capture_count = len(code.free_vars)
    if capture_count:
        lines.append(f"{indent}{green('Captured: ' + str(len(code.free_vars)), color)}")
        lines.append(rule)
        for i, fname in enumerate(code.free_vars):
            rid = f"c{i}"
            label = f"{rid:>6}: '{fname}'"
            lines.append(f"{indent}{cyan(label, color)}")

        lines.append(rule)

    locals_count = code.local_count - param_count - capture_count
    if locals_count:
        lines.append(f"{indent}{green('Locals: ' + str(locals_count), color)}")
        lines.append(rule)

    return lines


def render_instruction_lines(
    code: CodeObject,
    line_prefix: Callable[[int, Instruction], str],
    indent: str = "",
    color: bool = False,
    dim_predicate: Callable[[int, Instruction], bool] | None = None,
) -> list[str]:
    """
    Render a code object's instruction listing as lines.

    Emits one line per instruction in instruction order.  A jump-target
    instruction is preceded by a blank line and marked with a leading arrow
    placed after any per-line prefix; a control-flow opcode is followed by a
    blank line unless the next instruction is itself a jump target (which
    inserts its own blank line above).  The annotation column is coloured green.

    The per-line prefix is supplied by the caller so that the disassembler can
    emit a bare listing while the annotated trace prepends an execution-count
    and percentage column.  The marker and blank-line placement are identical in
    both cases, so a traced instruction line lines up with the corresponding
    disassembly line.

    Args:
        code:        The code object whose instructions are rendered.
        line_prefix: Called with (index, instruction) and returns the leading
                     column text for that line (may be empty).
        indent:      Prefix prepended to every line (used for nested code objects).
        color:       Whether to emit ANSI colour codes.
        dim_predicate: When given, called with (index, instruction); a line for
                     which it returns True is rendered entirely in grey, taking
                     precedence over the annotation colour.  Used by the
                     annotated trace to dim instructions that never executed.

    Returns:
        The rendered lines.
    """
    lines: list[str] = []
    targets = jump_targets(code)

    for i, instr in enumerate(instructions(code)):
        is_target = i in targets
        if is_target and i > 0:
            lines.append(f"{indent}")

        annotation = annotate_instruction(instr, code)
        instr_str = format_instruction(instr, i, code)
        prefix = line_prefix(i, instr)

        # For jump target lines, prepend "► " so the marker sits flush after the
        # prefix and all subsequent columns remain aligned with non-target lines.
        target_marker = "\u25ba " if is_target else "  "

        if dim_predicate is not None and dim_predicate(i, instr):
            lines.append(grey(f"{indent}{prefix}{target_marker}{instr_str}{annotation}", color))

        elif annotation:
            lines.append(f"{indent}{prefix}{target_marker}{instr_str}{green(annotation, color)}")

        else:
            lines.append(f"{indent}{prefix}{target_marker}{instr_str}")

        # Blank line after a control flow opcode, unless the next instruction is
        # already a jump target (which will insert its own blank line above).
        if instr.opcode in CONTROL_FLOW_OPCODES and (i + 1) not in targets:
            lines.append(f"{indent}")

    return lines
