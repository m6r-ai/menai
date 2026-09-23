"""
Instruction-level rendering shared by the bytecode rendering tools.

These helpers turn a packed bytecode instruction into a human-readable,
annotated line.  They are used by the disassembler and by the instruction
tracer so that both render instructions identically.
"""

from collections.abc import Iterator

from menai.menai_value import MenaiValue
from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode, reg_name, unpack_instruction

_MAX_CONSTANT_LENGTH = 64


# Opcodes after which a blank line is emitted, so that control-flow boundaries
# are visually separated in a listing.
CONTROL_FLOW_OPCODES = frozenset({
    Opcode.JUMP_IF_FALSE, Opcode.JUMP_IF_TRUE, Opcode.CALL, Opcode.APPLY, Opcode.SWITCH_INTEGER,
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


def annotate_instruction(instr: Instruction, code: CodeObject) -> str:
    """Add annotation to instruction showing what it does."""
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

        # Name the value being patched in: use the closure's own name if the
        # value register also holds a known closure, otherwise use reg_name.
        value_closure_name = None
        for scan_instr in instructions(code):
            if scan_instr.opcode == Opcode.MAKE_CLOSURE and scan_instr.dest == instr.src2:
                value_closure_name = clean_name(code.code_objects[scan_instr.src0].name)
                break

        lhs_closure = closure_name or reg_name(instr.src0, code)
        lhs_capture = f"'{free_var_name}'" if free_var_name else f"capture[{instr.src1}]"
        rhs_sym = value_closure_name
        rhs_reg = reg_name(instr.src2, code)
        rhs = f"'{rhs_sym}'" if rhs_sym else rhs_reg
        annotation = f"  ; '{lhs_closure}'.{lhs_capture} = {rhs}"

    elif opcode == Opcode.RAISE_ERROR:
        if src0 < len(code.constants):
            msg = code.constants[src0]
            annotation = f"  ; Raise error: {format_constant(msg)[:40]}"

    elif opcode == Opcode.SWITCH_INTEGER:
        if instr.src1 < len(code.jump_tables):
            t_min, t_default, targets = code.jump_tables[instr.src1]
            hi = t_min + len(targets) - 1
            annotation = f"  ; see jt{instr.src1}: {t_min}..{hi} -> arms, else @{t_default}"

    return annotation


def format_instruction(instr: Instruction, index: int, code: CodeObject) -> str:
    """Format an instruction with symbolic register names derived from code."""
    instr_str = f"{index:4}: {instr.format(code)}"
    # Pad to fixed width so annotations align; 48 chars covers the longest opcodes
    return instr_str.ljust(48)
