"""
Tests for internal compiler error reporting.

A failed invariant inside a compiler pass is not the program's fault.  If it
escapes as a raw AssertionError the user cannot tell whether they wrote bad code
or hit a compiler bug, and may waste time rewriting valid code.  These tests pin
the distinction between a user-facing diagnostic and a compiler fault.
"""

import pytest

from menai import (
    MenaiCodegenError,
    MenaiError,
    MenaiEvalError,
    MenaiInternalError,
)
from menai.menai_compiler import MenaiCompiler
from menai.vcode.menai_vcode import MenaiVCodeReg
from menai.vcode.menai_vcode_allocator import SlotMap


class TestInternalErrorFormatting:
    """The message must be unmistakable and internally consistent."""

    def test_banner_states_it_is_a_compiler_bug(self):
        """The first line must say plainly that this is not the user's fault."""
        err = MenaiInternalError(
            message="register r0(x) has no assigned slot",
            phase="vcode slot allocation",
            expression="(letrec ((x (integer+ x 1))) x)",
        )
        text = str(err)

        assert "INTERNAL COMPILER ERROR" in text
        assert "not in your program" in text
        assert "vcode slot allocation" in text
        assert "(letrec ((x (integer+ x 1))) x)" in text

    def test_positional_wording_matches_layout(self):
        """
        The expression is printed above Context and Suggestion, so text
        referring to it must say "above", never "below".
        """
        err = MenaiInternalError(
            message="boom",
            phase="vcode lowering",
            expression="(integer+ 1 2)",
            context="The source above may be perfectly valid.",
        )
        text = str(err)

        assert "below" not in text
        assert text.index("Expression being compiled") < text.index("Context:")

    def test_expression_is_separated_from_following_lines(self):
        """
        A blank line after the expression keeps it visually distinct from the
        Context and Suggestion lines that follow.
        """
        err = MenaiInternalError(
            message="boom",
            expression="(integer+ 1 2)",
            context="some context",
        )
        text = str(err)

        assert "(integer+ 1 2)\n\nContext:" in text

    def test_is_a_menai_error(self):
        """Catchable as MenaiError, like every other Menai diagnostic."""
        assert issubclass(MenaiInternalError, MenaiError)

    def test_distinct_from_codegen_error(self):
        """
        MenaiCodegenError means the program cannot be encoded — a user-facing
        condition with a real fix.  MenaiInternalError means the compiler broke.
        Conflating them would mislabel one as the other.
        """
        assert not issubclass(MenaiInternalError, MenaiCodegenError)
        assert not issubclass(MenaiCodegenError, MenaiInternalError)

    def test_has_default_suggestion(self):
        """A report needs actionable guidance even when the raiser gives none."""
        err = MenaiInternalError(message="boom")
        assert "not caused by your program" in str(err)


class _FailingStage:
    """A backend stage that fails the way an invariant violation would."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def build(self, *args, **kwargs):
        raise self._exc


class TestCompilerBoundary:
    """Backend faults must be converted; everything else must pass through."""

    def test_assertion_becomes_internal_error(self):
        compiler = MenaiCompiler()
        compiler._vcode_builder = _FailingStage(
            AssertionError("SlotMap: register r0(x) has no assigned slot")
        )

        with pytest.raises(MenaiInternalError) as exc_info:
            compiler.compile("(integer+ 1 2)")

        err = exc_info.value
        assert err.phase == "vcode lowering"
        assert err.expression == "(integer+ 1 2)"
        assert "has no assigned slot" in str(err)

    def test_attribute_error_becomes_internal_error(self):
        """A pass producing a shape the next cannot consume is a compiler fault."""
        compiler = MenaiCompiler()
        compiler._vcode_builder = _FailingStage(
            AttributeError("'str' object has no attribute 'instrs'")
        )

        with pytest.raises(MenaiInternalError):
            compiler.compile("(integer+ 1 2)")

    def test_type_error_becomes_internal_error(self):
        compiler = MenaiCompiler()
        compiler._vcode_builder = _FailingStage(TypeError("unexpected operand type"))

        with pytest.raises(MenaiInternalError):
            compiler.compile("(integer+ 1 2)")

    def test_deliberate_menai_error_passes_through(self):
        """
        A pass raising a real Menai diagnostic must not be relabelled as an
        internal error — that would hide genuine user-facing messages.
        """
        compiler = MenaiCompiler()
        compiler._vcode_builder = _FailingStage(
            MenaiEvalError(message="a real user-facing diagnostic")
        )

        with pytest.raises(MenaiEvalError, match="a real user-facing diagnostic"):
            compiler.compile("(integer+ 1 2)")

    def test_environmental_errors_are_not_relabelled(self):
        """
        RecursionError is environmental, not a compiler fault.  Relabelling it
        as "compiler bug" would mislead.
        """
        compiler = MenaiCompiler()
        compiler._vcode_builder = _FailingStage(
            RecursionError("maximum recursion depth exceeded")
        )

        with pytest.raises(RecursionError):
            compiler.compile("(integer+ 1 2)")

    def test_valid_program_compiles_unchanged(self):
        """The boundary must not disturb the normal path."""
        compiler = MenaiCompiler()
        code = compiler.compile("(integer+ 1 2)")
        assert code is not None

    def test_cfg_stage_is_wrapped_on_the_main_compile_path(self):
        """
        Regression guard: compile() must route the CFG stage through the
        boundary.  It originally called _compile_to_cfg directly, so every
        assert in the CFG builder escaped as a raw AssertionError on the main
        path even though compile_to_cfg() was protected.
        """
        import menai.cfg.menai_cfg_builder as cfg_builder

        original = cfg_builder.MenaiCFGBuilder._build_let

        def _boom(self, *args, **kwargs):
            assert False, "genuine assert inside CFG builder"

        cfg_builder.MenaiCFGBuilder._build_let = _boom
        try:
            compiler = MenaiCompiler()
            with pytest.raises(MenaiInternalError) as exc_info:
                compiler.compile("(let ((x 1)) x)")

            assert "desugaring, IR construction, and CFG optimisation" in (
                exc_info.value.phase
            )

        finally:
            cfg_builder.MenaiCFGBuilder._build_let = original

    def test_front_end_stage_is_wrapped(self):
        """A fault in lexing/parsing/module resolution is also a compiler fault."""
        compiler = MenaiCompiler()
        compiler._lexer = _FailingStage(AssertionError("lexer invariant broken"))

        with pytest.raises(MenaiInternalError) as exc_info:
            compiler.compile("(integer+ 1 2)")

        assert "lexing" in exc_info.value.phase

    def test_compile_to_resolved_ast_is_wrapped(self):
        """
        Regression guard: compile_to_resolved_ast is public and was reachable
        without the boundary, so a front-end assert escaped raw.
        """
        compiler = MenaiCompiler()
        compiler._lexer = _FailingStage(AssertionError("lexer invariant broken"))

        with pytest.raises(MenaiInternalError):
            compiler.compile_to_resolved_ast("(integer+ 1 2)")


class TestModuleLoadingBoundary:
    """
    load_module and reload_module are public and compile module source
    independently of compile(), so they need the same protection.
    """

    @staticmethod
    def _menai_with_stdlib():
        from menai import Menai
        from menai.menai import Menai as MenaiClass

        return Menai(module_path=[str(MenaiClass.stdlib_path())])

    def test_reload_module_does_not_leak_assertion(self):
        """
        Regression guard: reload_module bypasses the module cache, so it really
        compiles — and it used to leak a raw AssertionError from the front end.
        """
        import menai.ast.menai_ast_builder as ast_builder
        menai = self._menai_with_stdlib()
        original = ast_builder.MenaiASTBuilder.build

        def _boom(self, *args, **kwargs):
            assert False, "injected AST builder assert"

        ast_builder.MenaiASTBuilder.build = _boom
        try:
            with pytest.raises(MenaiInternalError) as exc_info:
                menai.reload_module("json-decode")

            assert "module resolution" in exc_info.value.phase

        finally:
            ast_builder.MenaiASTBuilder.build = original


class TestSlotMapGuard:
    """
    The slot allocator's final guard must not rely on `assert`.

    `assert` is stripped under `python -O`, which would let an undefined
    register reach the VM and corrupt execution silently.
    """

    def test_raises_internal_error_not_assertion(self):
        slot_map = SlotMap(slots={}, slot_count=0, local_count=0)

        with pytest.raises(MenaiInternalError, match="has no assigned slot"):
            slot_map.slot_of(MenaiVCodeReg(id=99, hint="ghost"))

    def test_error_names_the_phase(self):
        slot_map = SlotMap(slots={}, slot_count=0, local_count=0)

        with pytest.raises(MenaiInternalError) as exc_info:
            slot_map.slot_of(MenaiVCodeReg(id=99, hint="ghost"))

        assert exc_info.value.phase == "vcode slot allocation"

    def test_returns_slot_when_present(self):
        slot_map = SlotMap(slots={7: 3}, slot_count=4, local_count=4)
        assert slot_map.slot_of(MenaiVCodeReg(id=7, hint="ok")) == 3
