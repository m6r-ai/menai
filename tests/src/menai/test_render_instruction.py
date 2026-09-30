"""
Tests for the shared instruction and metadata rendering helpers.

The disassembler and the annotated trace both render code objects through these
helpers, so that a traced function and its disassembly present the same metadata
tables, instruction formatting, annotations, jump-target markers, and
control-flow spacing.  These tests pin the shared behaviour down at the helper
level, independent of either tool.
"""

from menai.bytecode.menai_bytecode import CodeObject, Instruction, Opcode
from menai.menai_value import MenaiInteger, MenaiString
from menai_render.menai_render_instruction import (
    render_code_metadata,
    render_instruction_lines,
)


def _code(
    instructions: list[Instruction] | None = None,
    constants: tuple = (),
    code_objects: tuple[CodeObject, ...] = (),
    jump_tables: tuple = (),
    param_names: tuple[str, ...] = (),
    param_count: int = 0,
    free_vars: tuple[str, ...] = (),
    local_count: int = 0,
    name: str = "<test>",
) -> CodeObject:
    """Build a code object for rendering tests."""
    return CodeObject(
        instructions=instructions if instructions is not None else [Instruction(opcode=Opcode.RETURN, src0=0)],
        constants=constants,
        code_objects=code_objects,
        jump_tables=jump_tables,
        param_names=param_names,
        param_count=param_count,
        free_vars=free_vars,
        local_count=local_count,
        name=name,
    )


class TestMetadataSections:
    """Each metadata table is emitted only when the code object has that data."""

    def test_empty_metadata_renders_nothing(self):
        assert render_code_metadata(_code()) == []

    def test_constants_section(self):
        code = _code(constants=(MenaiInteger(1), MenaiString("hi")))
        lines = render_code_metadata(code)
        assert "Constants: 2" in lines
        assert "    k0: integer 1" in lines
        assert '    k1: string "hi"' in lines

    def test_nested_code_objects_section(self):
        nested = _code(name="child")
        code = _code(code_objects=(nested,))
        lines = render_code_metadata(code)
        assert "Code Objects: 1" in lines
        assert any("x0: child" in line for line in lines)

    def test_jump_tables_section(self):
        code = _code(jump_tables=((0, 9, [3, 6, 9]),))
        lines = render_code_metadata(code)
        assert "Jump Tables: 1" in lines
        header = next(line for line in lines if "jt0:" in line)
        assert "min=0  default=@9  span=0..2" in header
        assert any("0 : @3" in line for line in lines)
        assert any("_ : @9  (default)" in line for line in lines)

    def test_inputs_section(self):
        code = _code(param_names=("x", "y"), param_count=2, local_count=2)
        lines = render_code_metadata(code)
        assert "Inputs: 2" in lines
        assert "    i0: 'x'" in lines
        assert "    i1: 'y'" in lines

    def test_captured_section(self):
        code = _code(free_vars=("offset",), local_count=1)
        lines = render_code_metadata(code)
        assert "Captured: 1" in lines
        assert "    c0: 'offset'" in lines

    def test_locals_count_excludes_params_and_captures(self):
        code = _code(param_names=("x",), param_count=1, free_vars=("c",), local_count=4)
        lines = render_code_metadata(code)
        assert "Locals: 2" in lines

    def test_locals_section_omitted_when_zero(self):
        code = _code(param_names=("x",), param_count=1, local_count=1)
        lines = render_code_metadata(code)
        assert not any(line.startswith("Locals:") for line in lines)

    def test_indent_is_applied_to_every_line(self):
        code = _code(constants=(MenaiInteger(1),))
        lines = render_code_metadata(code, indent="  ")
        assert all(line.startswith("  ") for line in lines)


class TestInstructionLines:
    """The instruction listing marks jump targets and separates control flow."""

    def test_one_line_per_instruction(self):
        code = _code(instructions=[
            Instruction(opcode=Opcode.LOAD_NONE, dest=0),
            Instruction(opcode=Opcode.RETURN, src0=0),
        ])
        lines = render_instruction_lines(code, lambda _i, _instr: "")
        assert len(lines) == 2

    def test_prefix_is_prepended_to_each_line(self):
        code = _code(instructions=[Instruction(opcode=Opcode.RETURN, src0=0)])
        lines = render_instruction_lines(code, lambda _i, _instr: "PFX ")
        assert lines[0].startswith("PFX ")

    def test_jump_target_is_marked_and_preceded_by_blank(self):
        code = _code(instructions=[
            Instruction(opcode=Opcode.JUMP, src0=2),
            Instruction(opcode=Opcode.LOAD_NONE, dest=0),
            Instruction(opcode=Opcode.RETURN, src0=0),
        ])
        lines = render_instruction_lines(code, lambda _i, _instr: "")
        marked_index = next(i for i, ln in enumerate(lines) if "\u25ba" in ln)
        assert lines[marked_index - 1] == ""

    def test_control_flow_opcode_followed_by_blank(self):
        code = _code(instructions=[
            Instruction(opcode=Opcode.RETURN, src0=0),
            Instruction(opcode=Opcode.JUMP_IF_FALSE, src0=0, src1=3),
            Instruction(opcode=Opcode.LOAD_NONE, dest=0),
            Instruction(opcode=Opcode.RETURN, src0=0),
        ])
        lines = render_instruction_lines(code, lambda _i, _instr: "")
        jump_index = next(i for i, ln in enumerate(lines) if "JUMP_IF_FALSE" in ln)
        assert lines[jump_index + 1] == ""

    def test_no_blank_after_control_flow_when_next_is_jump_target(self):
        """A control-flow opcode directly before a jump target adds no extra blank."""
        code = _code(instructions=[
            Instruction(opcode=Opcode.JUMP_IF_FALSE, src0=0, src1=1),
            Instruction(opcode=Opcode.RETURN, src0=0),
        ])
        lines = render_instruction_lines(code, lambda _i, _instr: "")
        jump_index = next(i for i, ln in enumerate(lines) if "JUMP_IF_FALSE" in ln)
        assert lines[jump_index + 1] == ""
        assert "\u25ba" in lines[jump_index + 2]

    def test_dim_predicate_greys_whole_line(self):
        code = _code(instructions=[Instruction(opcode=Opcode.RETURN, src0=0)])
        lines = render_instruction_lines(
            code,
            lambda _i, _instr: "",
            color=True,
            dim_predicate=lambda _i, _instr: True,
        )
        assert lines[0].startswith("\033[90m")

    def test_dim_predicate_defaults_to_no_dimming(self):
        code = _code(instructions=[Instruction(opcode=Opcode.RETURN, src0=0)])
        lines = render_instruction_lines(code, lambda _i, _instr: "", color=True)
        assert not lines[0].startswith("\033[90m")

    def test_indent_is_applied_to_instruction_lines(self):
        code = _code(instructions=[Instruction(opcode=Opcode.RETURN, src0=0)])
        lines = render_instruction_lines(code, lambda _i, _instr: "", indent="  ")
        assert lines[0].startswith("  ")
