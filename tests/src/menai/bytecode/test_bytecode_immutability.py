"""
Tests that the bytecode model is immutable and that the builder does not mutate input.

A CodeObject is a value: its sequence fields are tuples, so they cannot be
mutated in place.  The bytecode builder is a pure function of the VCode it is
handed: it returns a new CodeObject and must not modify the VCode function it
was given.  This is what makes the layer a viable interface for code generation
written in Menai (see ADR-0034).

Two properties are checked:

1. Model immutability — a CodeObject's sequence fields are tuples.
2. Builder purity — building bytecode leaves the input VCode function
   structurally identical.
"""

import pytest

from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.bytecode.menai_bytecode import CodeObject
from menai.bytecode.menai_bytecode_builder import MenaiBytecodeBuilder
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiInteger
from menai.vcode.menai_vcode import MenaiVCodeFunction
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder


_SOURCES = {
    "const-branch": "(lambda (x) (if (integer? x) 1 2))",
    "match-switch": """
        (lambda (n)
          (match n (0 100) (1 200) (2 300) (_ 0)))
    """,
    "closures": """
        (let ((x 1))
          (lambda (y) (integer+ x y)))
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

    Any in-place change to the instruction list, an instruction's sequence
    fields, or the function's own sequence fields changes this string.
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
    A CodeObject is a value: its sequence fields are tuples, so they cannot be
    mutated in place.
    """

    def test_constants_is_a_tuple(self):
        """A code object's constant pool is a tuple."""
        code = CodeObject(
            instructions=[],
            constants=(MenaiInteger(1),),
            code_objects=(),
        )

        assert isinstance(code.constants, tuple)
        assert not hasattr(code.constants, "append")

    def test_code_objects_is_a_tuple(self):
        """A code object's nested-code-object sequence is a tuple."""
        code = CodeObject(instructions=[], constants=(), code_objects=())

        assert isinstance(code.code_objects, tuple)
        assert not hasattr(code.code_objects, "append")

    def test_free_vars_and_param_names_are_tuples(self):
        """A code object's free-var and param-name sequences are tuples."""
        code = CodeObject(instructions=[], constants=(), code_objects=())

        assert isinstance(code.free_vars, tuple)
        assert isinstance(code.param_names, tuple)
        assert not hasattr(code.free_vars, "append")
        assert not hasattr(code.param_names, "append")

    def test_jump_tables_is_a_tuple(self):
        """A code object's jump-table sequence is a tuple."""
        code = CodeObject(instructions=[], constants=(), code_objects=())

        assert isinstance(code.jump_tables, tuple)
        assert not hasattr(code.jump_tables, "append")


class TestBuilderDoesNotMutateInput:
    """The bytecode builder must leave the VCode function it was handed unchanged."""

    @pytest.mark.parametrize(
        "source_key",
        ["const-branch", "match-switch", "closures", "two-loops"],
        ids=["const-branch", "match-switch", "closures", "two-loops"],
    )
    def test_build_does_not_mutate_input(self, source_key):
        """
        Building bytecode leaves the input VCode function structurally identical.

        The builder returns a new CodeObject; it must not modify the VCode
        function it received.
        """
        func = _build_vcode(_SOURCES[source_key])
        before = _snapshot(func)

        MenaiBytecodeBuilder().build(func, "test")

        assert _snapshot(func) == before, "bytecode builder mutated its input VCode"
