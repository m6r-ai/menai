"""
Tests that CFG optimisation passes do not mutate their input.

A pass must treat its input CFG as an immutable value: it may return a new
CFG, but the CFG it was handed must be unchanged afterwards.  This is what
makes a pass safe to run more than once and what makes the CFG a viable
interface for passes written in Menai.

Each test compiles a small program to a CFG, snapshots the CFG structurally,
runs one pass over it, and asserts the snapshot is unchanged.  The snapshot is
a deep, value-level rendering (block ids, labels, instruction reprs, terminator
reprs, and the block list order), so any in-place change to blocks, instruction
lists, terminators, or the block list is detected.

The source for each pass is chosen so that the pass actually has something to
do; a pass that finds nothing to change would not exercise the mutation paths
it uses when it fires.

MenaiCFGPredicateFold is not covered here.  It folds a predicate only when its
argument's type fact is locally proven, and reaching that state through the
full pipeline requires the fact to survive the passes that run before it.  Its
behaviour is covered by test_cfg_predicate_fold.py instead.
"""

import pytest

from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.cfg.menai_cfg import MenaiCFGFunction
from menai.cfg.menai_cfg_branch_const_prop import MenaiCFGBranchConstProp
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_collapse_phi_chains import MenaiCFGCollapsePhiChains
from menai.cfg.menai_cfg_dead_captures import MenaiCFGDeadCaptures
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion
from menai.cfg.menai_cfg_licm import MenaiCFGLICM
from menai.cfg.menai_cfg_loop_rotation import MenaiCFGLoopRotation
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext
from menai.cfg.menai_cfg_order_exception_blocks import MenaiCFGOrderExceptionBlocks
from menai.cfg.menai_cfg_simplify_blocks import MenaiCFGSimplifyBlocks
from menai.cfg.menai_cfg_switch_dispatch import MenaiCFGSwitchDispatch
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.menai_compiler import MenaiCompiler


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
    "loop-licm": """
        (letrec ((f (lambda (n acc)
                      (if (integer=? n 0)
                          acc
                          (f (integer- n 1) (integer+ acc (integer+ 1 2)))))))
          (f 10 0))
    """,
    "dead-capture": """
        (letrec ((f (lambda (n)
                      (if (integer=? n 0)
                          n
                          (f (integer- n 1))))))
          (lambda (x) (integer+ (f x) 1)))
    """,
    "raise": """
        (lambda (x) (if (integer=? x 0) (error "zero") x))
    """,
}


def _build_cfg(source: str) -> MenaiCFGFunction:
    """Compile source to a CFG, running the front end and IR passes."""
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

    return MenaiCFGBuilder().build(ir)


def _snapshot(func: MenaiCFGFunction) -> str:
    """
    Render a CFG to a string capturing its value-level structure.

    Any in-place change to the block list, a block's instruction list,
    patch_instrs, terminator, or a terminator's targets changes this string.
    """
    parts = []
    for b in func.blocks:
        instrs = " | ".join(repr(i) for i in b.instrs)
        patches = " | ".join(repr(p) for p in b.patch_instrs)
        term = repr(b.terminator) if b.terminator is not None else "None"
        parts.append(f"b{b.id}[{b.label}] instrs={instrs} patch={patches} term={term}")

    return "\n".join(parts)


_PASS_CASES = [
    ("CollapsePhiChains", MenaiCFGCollapsePhiChains(), "match-switch"),
    ("BranchConstProp", MenaiCFGBranchConstProp(), "const-branch"),
    ("SimplifyBlocks", MenaiCFGSimplifyBlocks(), "const-branch"),
    ("SwitchDispatch", MenaiCFGSwitchDispatch(), "match-switch"),
    ("GuardInsertion", MenaiCFGGuardInsertion(), "const-branch"),
    ("LICM", MenaiCFGLICM(), "loop-licm"),
    ("LoopRotation", MenaiCFGLoopRotation(), "two-loops"),
    ("DeadCaptures", MenaiCFGDeadCaptures(), "dead-capture"),
    ("OrderExceptionBlocks", MenaiCFGOrderExceptionBlocks(), "raise"),
]


class TestPassesDoNotMutateInput:
    """A pass must leave the CFG it was handed unchanged."""

    @pytest.mark.parametrize(
        "name,pass_,source_key",
        _PASS_CASES,
        ids=[c[0] for c in _PASS_CASES],
    )
    def test_pass_does_not_mutate_input(self, name, pass_, source_key):
        """
        Running a pass leaves the input CFG structurally identical.

        The pass may return a new CFG; it must not modify the one it received.
        """
        func = _build_cfg(_SOURCES[source_key])
        before = _snapshot(func)

        pass_.optimize(func, MenaiCFGContext())

        assert _snapshot(func) == before, f"{name} mutated its input CFG"
