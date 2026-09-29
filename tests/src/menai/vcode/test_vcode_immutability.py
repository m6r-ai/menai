"""
Tests that the VCode model is immutable and that VCode passes do not mutate input.

VCode is a value: a pass returns a new function rather than mutating the one it
was given.  This is what makes a pass safe to run more than once and what makes
VCode a viable interface for passes written in Menai (see ADR-0034).

Two properties are checked:

1. Model immutability — a function's and an instruction's sequence fields are
   tuples, so they cannot be mutated in place.
2. Pass purity — running a pass leaves the input function structurally identical.

The source for each pass is chosen so that the pass actually has something to
do; a pass that finds nothing to change would not exercise the mutation paths
it uses when it fires.
"""

import pytest

from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.menai_compiler import MenaiCompiler
from menai.vcode.menai_vcode import (
    MenaiVCodeBuiltin,
    MenaiVCodeFunction,
    MenaiVCodeReg,
)
from menai.vcode.menai_vcode_allocator import allocate_slots
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder
from menai.vcode.menai_vcode_peephole import (
    coalesce_constants,
    peephole,
    schedule_self_loop_moves,
)


_SOURCES = {
    "const-branch": "(lambda (x) (if (integer? x) 1 2))",
    "match-switch": """
        (lambda (n)
          (match n (0 100) (1 200) (2 300) (_ 0)))
    """,
    "two-loops": """
        (let ((a (letrec ((f (lambda (n acc)
                               (if (integer=? n 0) acc (f (integer- n 1) (integer+ acc n))))))
                   (f 10 0)))
              (b (letrec ((g (lambda (m s)
                               (if (integer=? m 0) s (g (integer- m 1) (integer* s m))))))
                   (g 10 1))))
          (integer+ a b))
    """,
    "repeated-const": """
        (lambda (x)
          (integer+ (integer+ 1000 x) (integer+ 1000 x)))
    """,
    "tail-loop": """
        (letrec ((f (lambda (n acc)
                      (if (integer=? n 0)
                          acc
                          (f (integer- n 1) (integer+ acc 1))))))
          (f 10 0))
    """,
}


def _build_vcode(source: str) -> MenaiVCodeFunction:
    """Compile source to VCode, running the front end, IR passes, and CFG passes."""
    tokens = MenaiLexer().lex(source)
    ast = MenaiASTBuilder().build(tokens, source, "")
    ast = MenaiASTSemanticAnalyzer().analyze(ast, source)
    ast = MenaiASTModuleResolver(None).resolve_program(ast)
    desugarer = MenaiASTDesugarer()
    desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
    ast = desugarer.desugar(ast)
    ast = MenaiASTPreludeInjector.wrap(ast)
    ast = MenaiASTConstantFolder().optimize(ast)
    ir = MenaiIRBuilder().build(ast)

    compiler = MenaiCompiler()
    for p in compiler.ir_passes:
        while True:
            ir, changed = p.optimize(ir)
            if not changed:
                break

    cfg = MenaiCFGBuilder().build(ir)
    context = MenaiCFGContext()
    for p in compiler.cfg_passes:
        while True:
            cfg, changed = p.optimize(cfg, context)
            if not changed:
                break

    return MenaiVCodeBuilder().build(cfg)


def _snapshot(func: MenaiVCodeFunction) -> str:
    """
    Render a VCode function to a string capturing its value-level structure.

    Any in-place change to the instruction list, a sequence field on an
    instruction, or the function's own sequence fields changes this string.
    """
    parts = [repr(i) for i in func.instrs]
    parts.append(f"params={func.params}")
    parts.append(f"free_vars={func.free_vars}")
    parts.append(f"param_reg_ids={func.param_reg_ids}")
    parts.append(f"free_var_reg_ids={func.free_var_reg_ids}")
    parts.append(f"loop_param_reg_ids={func.loop_param_reg_ids}")
    parts.append(f"hoisted_reg_ids={func.hoisted_reg_ids}")
    return "\n".join(parts)


class TestModelIsImmutable:
    """
    A VCode function and its instructions are values: their sequence fields are
    tuples, so they cannot be mutated in place.
    """

    def test_function_instrs_is_a_tuple(self):
        """A VCode function's instruction list is a tuple."""
        func = _build_vcode(_SOURCES["const-branch"])

        assert isinstance(func.instrs, tuple)
        assert not hasattr(func.instrs, "append")

    def test_function_params_is_a_tuple(self):
        """A VCode function's parameter list is a tuple."""
        func = _build_vcode(_SOURCES["const-branch"])

        assert isinstance(func.params, tuple)
        assert not hasattr(func.params, "append")

    def test_instruction_args_is_a_tuple(self):
        """An instruction's argument sequence is a tuple."""
        reg = MenaiVCodeReg(id=0)
        instr = MenaiVCodeBuiltin(dst=reg, op="integer+", args=(reg, reg))

        assert isinstance(instr.args, tuple)
        assert not hasattr(instr.args, "append")


class TestPassesDoNotMutateInput:
    """A pass must leave the VCode function it was handed unchanged."""

    @pytest.mark.parametrize(
        "name,run,source_key",
        [
            ("ScheduleSelfLoopMoves", lambda f: schedule_self_loop_moves(f), "tail-loop"),
            ("CoalesceConstants", lambda f: coalesce_constants(f), "repeated-const"),
            ("Peephole", lambda f: peephole(f, allocate_slots(f)), "const-branch"),
            ("Peephole-switch", lambda f: peephole(f, allocate_slots(f)), "match-switch"),
            ("AllocateSlots", lambda f: allocate_slots(f), "two-loops"),
        ],
        ids=[
            "ScheduleSelfLoopMoves",
            "CoalesceConstants",
            "Peephole",
            "Peephole-switch",
            "AllocateSlots",
        ],
    )
    def test_pass_does_not_mutate_input(self, name, run, source_key):
        """
        Running a pass leaves the input function structurally identical.

        The pass may return a new function; it must not modify the one it
        received.
        """
        func = _build_vcode(_SOURCES[source_key])
        before = _snapshot(func)

        run(func)

        assert _snapshot(func) == before, f"{name} mutated its input VCode"
