"""
Tests that the IR model is immutable and that IR passes do not mutate input.

The IR is a value: a pass returns a new tree rather than mutating the tree it
was given.  This is what makes a pass safe to run more than once and what makes
the IR a viable interface for passes written in Menai (see ADR-0034).

Two properties are checked:

1. Model immutability — a node's sequence fields are tuples, so they cannot be
   mutated in place.
2. Pass purity — running a pass leaves the input tree structurally identical.
"""

import pytest

from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.ir.menai_ir import (
    MenaiIRBuildList,
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRExpr,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRVariable,
)
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiInteger


_SOURCES = {
    "const-branch": "(lambda (x) (if (integer? x) 1 2))",
    "two-loops": """
        (let ((a (letrec ((f (lambda (n acc)
                               (if (integer=? n 0) acc (f (integer- n 1) (integer+ acc n))))))
                   (f 10 0)))
              (b (letrec ((g (lambda (m s)
                               (if (integer=? m 0) s (g (integer- m 1) (integer* s m))))))
                   (g 10 1))))
          (integer+ a b))
    """,
    "closure": """
        (let ((x 1))
          (lambda (y) (integer+ x y)))
    """,
    "dead-binding": """
        (lambda (x)
          (let ((unused 1)
                (used 2))
            (integer+ x used)))
    """,
}


def _build_ir(source: str) -> MenaiIRExpr:
    """Compile source to IR, running the front end."""
    tokens = MenaiLexer().lex(source)
    ast = MenaiASTBuilder().build(tokens, source, "")
    ast = MenaiASTSemanticAnalyzer().analyze(ast, source)
    ast = MenaiASTModuleResolver(None).resolve_program(ast)
    desugarer = MenaiASTDesugarer()
    desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
    ast = desugarer.desugar(ast)
    ast = MenaiASTPreludeInjector.wrap(ast)
    ast = MenaiASTConstantFolder().optimize(ast)
    return MenaiIRBuilder().build(ast)


def _snapshot(node: MenaiIRExpr) -> str:
    """
    Render an IR tree to a string capturing its value-level structure.

    Any change to a node's fields, or to the shape of the tree, changes this
    string.  Uses a deterministic rendering (dataclass reprs recurse; no
    addresses appear because IR nodes are dataclasses).
    """
    return repr(node)


class TestModelIsImmutable:
    """
    An IR node is a value: its sequence fields are tuples, so they cannot be
    mutated in place.
    """

    def test_sequence_field_is_a_tuple(self):
        """A node's sequence field is a tuple, so it cannot be mutated in place."""
        node = MenaiIRCall(
            func_plan=MenaiIRVariable(name="f"),
            arg_plans=(MenaiIRVariable(name="x"),),
            is_tail_call=False,
            is_builtin=False,
            builtin_name=None,
        )

        assert isinstance(node.arg_plans, tuple)
        assert not hasattr(node.arg_plans, "append")

    def test_let_bindings_is_a_tuple(self):
        """A let node's bindings sequence is a tuple."""
        node = MenaiIRLet(
            bindings=(("x", MenaiIRConstant(value=MenaiInteger(1))),),
            body_plan=MenaiIRVariable(name="x"),
            in_tail_position=False,
        )

        assert isinstance(node.bindings, tuple)
        assert not hasattr(node.bindings, "append")

    def test_build_list_elements_is_a_tuple(self):
        """A build-list node's element sequence is a tuple."""
        node = MenaiIRBuildList(
            element_plans=(MenaiIRConstant(value=MenaiInteger(1)),),
        )

        assert isinstance(node.element_plans, tuple)
        assert not hasattr(node.element_plans, "append")


class TestPassesDoNotMutateInput:
    """A pass must leave the IR tree it was handed unchanged."""

    @pytest.mark.parametrize(
        "name,pass_,source_key",
        [
            ("LetrecToLoop", MenaiIRLetrecToLoop(), "two-loops"),
            ("Inliner", MenaiIRInliner(), "closure"),
            ("Optimizer", MenaiIROptimizer(), "dead-binding"),
            ("Optimizer-if", MenaiIROptimizer(), "const-branch"),
        ],
        ids=["LetrecToLoop", "Inliner", "Optimizer", "Optimizer-if"],
    )
    def test_pass_does_not_mutate_input(self, name, pass_, source_key):
        """
        Running a pass leaves the input tree structurally identical.

        The pass may return a new tree; it must not modify the one it received.
        """
        ir = _build_ir(_SOURCES[source_key])
        before = _snapshot(ir)

        pass_.optimize(ir)

        assert _snapshot(ir) == before, f"{name} mutated its input IR tree"
