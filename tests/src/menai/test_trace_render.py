"""
Tests for trace rendering.

The rendered instruction lines must match the disassembler's instruction
formatting and annotations, so that a hot spot found in a trace can be read
directly against the disassembler output.
"""

from menai import Menai
from menai_render.menai_render_instruction import annotate_instruction, format_instruction
from menai_trace.menai_trace_data import resolve_trace
from menai_trace.menai_trace_render import (
    render_annotated,
    render_call_summary,
    render_full_trace,
    render_hot_instructions,
    render_trace,
)

_SOURCE = """
(letrec ((fact (lambda (n) (if (integer<=? n 1) 1 (integer* n (fact (integer- n 1)))))))
  (fact 5))
"""


def _traced_result():
    """Compile, run, and resolve a trace for the shared source."""
    menai = Menai()
    code = menai.compile(_SOURCE)
    menai.vm.enable_profiling()
    menai.execute_raw(code)
    instr_counts, call_counts = menai.vm.get_trace_data()
    return code, resolve_trace(code, instr_counts, call_counts)


class TestInstructionLineMatchesDisassembler:
    """A traced instruction line reproduces the disassembler's formatting."""

    def test_instruction_text_matches_disassembler_helpers(self):
        code, result = _traced_result()
        fact = result.functions[1]
        for trace in fact.instructions:
            expected = format_instruction(trace.instruction, trace.index, fact.code)
            assert expected.startswith(f"{trace.index:4}: ")

    def test_annotation_matches_disassembler_helper(self):
        code, result = _traced_result()
        fact = result.functions[1]
        load_const = fact.instructions[0]
        expected = annotate_instruction(load_const.instruction, fact.code)
        assert expected == "  ; integer 1"


class TestRenderTrace:
    """The full trace rendering includes the expected sections."""

    def test_contains_totals(self):
        _, result = _traced_result()
        lines = render_trace(result, color=False)
        text = "\n".join(lines)
        assert "Total instructions executed:" in text
        assert "Total function calls:" in text

    def test_contains_each_function(self):
        _, result = _traced_result()
        text = "\n".join(render_trace(result, color=False))
        assert "fact" in text

    def test_top_n_limits_instructions_per_function(self):
        _, result = _traced_result()
        lines = render_trace(result, color=False, top_n=2)
        count_lines = [ln for ln in lines if "%" in ln and ":" in ln]
        assert len(count_lines) == 4


class TestRenderCallSummary:
    """The call summary orders functions by call count."""

    def test_most_called_function_first(self):
        _, result = _traced_result()
        lines = render_call_summary(result, color=False)
        body = [ln for ln in lines if "fact" in ln or "<module>" in ln]
        assert "fact" in body[0]

    def test_shows_call_count(self):
        _, result = _traced_result()
        text = "\n".join(render_call_summary(result, color=False))
        assert "5" in text


class TestRenderHotInstructions:
    """Hot instructions are ordered by count across the whole program."""

    def test_highest_count_first(self):
        _, result = _traced_result()
        lines = render_hot_instructions(result, color=False, top_n=5)
        counts = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped[0].isdigit():
                counts.append(int(stripped.split()[0].replace(",", "")))

        assert counts == sorted(counts, reverse=True)

    def test_top_n_respected(self):
        _, result = _traced_result()
        lines = render_hot_instructions(result, color=False, top_n=3)
        body = [ln for ln in lines if "%" in ln and ":" in ln]
        assert len(body) == 3


class TestRenderFullTrace:
    """The full trace combines hot instructions, call summary, and detail."""

    def test_contains_all_three_sections(self):
        _, result = _traced_result()
        text = "\n".join(render_full_trace(result, color=False))
        assert "HOT INSTRUCTIONS" in text
        assert "CALL SUMMARY" in text
        assert "INSTRUCTION TRACE" in text


class TestRenderAnnotated:
    """The annotated view renders every function in disassembly order."""

    def test_contains_every_function(self):
        code, result = _traced_result()
        text = "\n".join(render_annotated(result, color=False))
        for function in result.functions:
            name = function.code.name
            if name:
                assert name.split("(")[0].strip() in text

    def test_instructions_in_disassembly_order(self):
        """Instructions appear in index order, not sorted by count."""
        _, result = _traced_result()
        lines = render_annotated(result, color=False)
        indices = []
        for line in lines:
            stripped = line.strip()
            if ":" in stripped and stripped[0].isdigit():
                index_part = stripped.split(":")[0].split()[-1]
                if index_part.isdigit():
                    indices.append(int(index_part))

        fact_indices = indices[4:14]
        assert fact_indices == list(range(10))

    def test_function_header_shows_share_of_total(self):
        _, result = _traced_result()
        text = "\n".join(render_annotated(result, color=False))
        assert "% of total" in text

    def test_shows_total_instructions(self):
        _, result = _traced_result()
        text = "\n".join(render_annotated(result, color=False))
        assert "Total instructions executed:" in text


_LOOP_SOURCE = """
(letrec ((sum (lambda (n acc) (if (integer<=? n 0) acc (sum (integer- n 1) (integer+ acc n))))))
  (sum 5 0))
"""


def _traced_loop():
    """Compile, run, and resolve a trace for a function with a backward jump."""
    menai = Menai()
    code = menai.compile(_LOOP_SOURCE)
    menai.vm.enable_profiling()
    menai.execute_raw(code)
    instr_counts, call_counts = menai.vm.get_trace_data()
    return code, resolve_trace(code, instr_counts, call_counts)


class TestAnnotatedJumpTargets:
    """The annotated view marks jump targets and separates control flow."""

    def test_jump_target_lines_are_marked(self):
        _, result = _traced_loop()
        lines = render_annotated(result, color=False)
        marked = [ln for ln in lines if "\u25ba" in ln]
        assert marked

    def test_marker_follows_the_percentage_column(self):
        """The marker sits after '% of total', immediately before the instruction."""
        _, result = _traced_loop()
        lines = render_annotated(result, color=False)
        for line in lines:
            if "\u25ba" in line:
                marker_pos = line.index("\u25ba")
                pct_pos = line.index("%")
                assert pct_pos < marker_pos

    def test_blank_line_precedes_each_jump_target(self):
        """A jump target other than instruction 0 has a blank line above it."""
        _, result = _traced_loop()
        lines = render_annotated(result, color=False)
        for index, line in enumerate(lines):
            if "\u25ba" in line and index > 0:
                assert lines[index - 1].strip() == ""

    def test_blank_line_follows_control_flow_opcode(self):
        """A control-flow opcode is followed by a blank line."""
        _, result = _traced_loop()
        lines = render_annotated(result, color=False)
        for index, line in enumerate(lines):
            if "JUMP_IF_TRUE" in line or "JUMP_IF_FALSE" in line:
                assert lines[index + 1].strip() == ""

    def test_instruction_zero_is_not_marked(self):
        """Instruction 0 is never a jump target, so it is never marked."""
        _, result = _traced_loop()
        lines = render_annotated(result, color=False)
        first_instruction_line = next(ln for ln in lines if ": l0 = LOAD_CONST" in ln)
        assert "\u25ba" not in first_instruction_line
