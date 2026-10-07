"""
Menai Compiler - Orchestrates the complete compilation pipeline.

This is the main entry point for compiling Menai source code to bytecode.
It chains together all compilation passes in the correct order.
"""


from collections.abc import Sequence
from typing import Protocol, TypeVar

from menai.ast.menai_ast import MenaiASTNode
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver, MenaiASTModuleLoader
from menai.ast.menai_ast_optimization_pass import MenaiASTOptimizationPass
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_binding_injector import MenaiASTBindingInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.bytecode.menai_bytecode import CodeObject
from menai.bytecode.menai_bytecode_builder import MenaiBytecodeBuilder
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_branch_const_prop import MenaiCFGBranchConstProp
from menai.cfg.menai_cfg_collapse_phi_chains import MenaiCFGCollapsePhiChains
from menai.cfg.menai_cfg_dead_captures import MenaiCFGDeadCaptures
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion
from menai.cfg.menai_cfg_interproc_type_analysis import MenaiCFGInterprocTypeAnalysis
from menai.cfg.menai_cfg_licm import MenaiCFGLICM
from menai.cfg.menai_cfg_loop_rotation import MenaiCFGLoopRotation
from menai.cfg.menai_cfg_order_exception_blocks import MenaiCFGOrderExceptionBlocks
from menai.cfg.menai_cfg_predicate_fold import MenaiCFGPredicateFold
from menai.cfg.menai_cfg_simplify_blocks import MenaiCFGSimplifyBlocks
from menai.cfg.menai_cfg_struct_instance_fold import MenaiCFGStructInstanceFold
from menai.cfg.menai_cfg_switch_dispatch import MenaiCFGSwitchDispatch
from menai.cfg.menai_cfg_enum_switch_dispatch import MenaiCFGEnumSwitchDispatch
from menai.cfg.menai_cfg import MenaiCFGFunction
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext, MenaiCFGOptimizationPass
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiValue
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder


_Tree = TypeVar("_Tree")


class _OptimizationPass(Protocol[_Tree]):
    """A pass that transforms a tree and reports whether it changed."""

    def optimize(self, tree: _Tree) -> tuple[_Tree, bool]:
        """Transform the tree, returning it with a changed flag."""


class MenaiCompiler:
    """
    Main compiler pass manager.
    """

    def __init__(
        self,
        module_loader: MenaiASTModuleLoader | None = None,
    ):
        """
        Initialize compiler with all passes.

        Args:
            module_loader: Optional module loader for resolving imports.
        """
        self._module_loader = module_loader

        self._lexer = MenaiLexer()

        self._ast_builder = MenaiASTBuilder()
        self._ast_semantic_analyzer = MenaiASTSemanticAnalyzer()
        self._ast_module_resolver = MenaiASTModuleResolver(module_loader)
        self._ast_prelude_injector = MenaiASTPreludeInjector()
        self._ast_desugarer = MenaiASTDesugarer()
        self.ast_passes: list[MenaiASTOptimizationPass] = [
            MenaiASTConstantFolder(),
        ]
        self._ir_builder = MenaiIRBuilder()
        self.ir_passes: list[MenaiIROptimizationPass] = [
            # Dead-binding elimination runs before the inliner and the
            # letrec-to-loop conversion.  The prelude is spliced into every
            # program as a letrec, but a typical program reaches only a few of
            # its bindings.  Pruning the unreachable ones first means the two
            # expensive passes below walk the reachable program rather than the
            # whole prelude.  Neither pass can make a dead binding live, so the
            # result is unchanged.
            MenaiIROptimizer(),
            MenaiIRLetrecToLoop(),
            MenaiIRInliner(),
            MenaiIROptimizer(),
        ]
        self._cfg_builder = MenaiCFGBuilder()
        self.cfg_passes: list[MenaiCFGOptimizationPass] = [
            MenaiCFGCollapsePhiChains(),
            MenaiCFGBranchConstProp(),
            MenaiCFGSimplifyBlocks(),
            MenaiCFGSwitchDispatch(),
            MenaiCFGEnumSwitchDispatch(),
            MenaiCFGInterprocTypeAnalysis(),
            MenaiCFGStructInstanceFold(),
            MenaiCFGPredicateFold(),
            MenaiCFGGuardInsertion(),
            MenaiCFGLICM(),
            MenaiCFGLoopRotation(),
            MenaiCFGDeadCaptures(),
            MenaiCFGOrderExceptionBlocks(),
        ]
        self._vcode_builder = MenaiVCodeBuilder()
        self._bytecode_builder = MenaiBytecodeBuilder()

    def compile_to_resolved_ast(
        self, source: str, source_file: str = "", is_program: bool = True
    ) -> MenaiASTNode:
        """
        Compile source to fully resolved AST.

        This runs the front-end compilation stages:
        - Lexing
        - Parsing
        - Semantic analysis
        - Module resolution (including recursive module compilation)

        The result is a fully resolved AST ready for desugaring and backend compilation.

        Args:
            source: Menai source code as a string
            source_file: Source file name for tracking origin of AST nodes
            is_program: True when compiling a program directly, False when
                loading a module.  A directly-compiled program's top-level
                export form is lowered to a dict of exports; a loaded module's
                export form is preserved for the importer to consume.

        Returns:
            Fully resolved AST (all imports replaced with module ASTs)
        """
        tokens = self._lexer.lex(source)
        ast = self._ast_builder.build(tokens, source, source_file)
        checked_ast = self._ast_semantic_analyzer.analyze(ast, source)
        if is_program:
            resolved_ast = self._ast_module_resolver.resolve_program(checked_ast)

        else:
            resolved_ast = self._ast_module_resolver.resolve(checked_ast)

        return resolved_ast

    def compile(
        self,
        source: str,
        name: str = "<module>",
        inject: tuple[str, MenaiValue] | None = None,
    ) -> CodeObject:
        """
        Compile Menai source code to bytecode.

        This is the main entry point that runs the complete pipeline.

        Args:
            source: Menai source code as a string
            name: Optional name for the code object (e.g. filename)
            inject: Optional (binding name, value) pair.  When given, the
                program is wrapped in a single lexical binding of that name
                holding the value, one layer above the prelude and one layer
                below the program.

        Returns:
            Compiled bytecode ready for execution
        """
        resolved_ast = self.compile_to_resolved_ast(source, name)
        cfg = self._compile_to_cfg(resolved_ast, inject)

        vcode = self._vcode_builder.build(cfg)
        bytecode = self._bytecode_builder.build(vcode, name)
        return bytecode

    def compile_to_cfg(
        self,
        source: str,
        inject: tuple[str, MenaiValue] | None = None,
    ) -> MenaiCFGFunction:
        """
        Compile Menai source code to a CFG, stopping before VCode.

        This runs the complete pipeline up to and including the CFG
        optimisation passes.  It is the seam for inspecting the optimised
        CFG without lowering it further.

        Args:
            source: Menai source code as a string
            inject: Optional (binding name, value) pair, as for compile.

        Returns:
            The optimised CFG for the program
        """
        resolved_ast = self.compile_to_resolved_ast(source)
        return self._compile_to_cfg(resolved_ast, inject)

    def _compile_to_cfg(
        self,
        resolved_ast: MenaiASTNode,
        inject: tuple[str, MenaiValue] | None,
    ) -> MenaiCFGFunction:
        """
        Run the desugar-through-CFG stages on a resolved AST.

        Args:
            resolved_ast: A fully resolved AST from compile_to_resolved_ast.
            inject: Optional (binding name, value) pair, as for compile.

        Returns:
            The optimised CFG for the program
        """
        # The user program is desugared on its own and then wrapped in the
        # prelude's cached desugared bindings.  The prelude is identical for
        # every compilation, so desugaring it once and reusing the result
        # avoids re-desugaring it on every compile.  The program's temporary
        # counter starts above the prelude's so generated names cannot collide.
        self._ast_desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
        desugared_program = self._ast_desugarer.desugar(resolved_ast)

        # The host binding sits above the prelude and below the program, so the
        # program sees both and the host binding shadows nothing in the prelude.
        if inject is not None:
            desugared_program = MenaiASTBindingInjector.wrap(desugared_program, *inject)

        desugared_ast = MenaiASTPreludeInjector.wrap(desugared_program)

        for ast_pass in self.ast_passes:
            desugared_ast = ast_pass.optimize(desugared_ast)

        ir = self._ir_builder.build(desugared_ast)

        ir = self._run_optimization_passes(ir, self.ir_passes)

        cfg = self._cfg_builder.build(ir)
        cfg = self._run_cfg_passes(cfg)

        return cfg

    def _run_cfg_passes(self, cfg: MenaiCFGFunction) -> MenaiCFGFunction:
        """
        Run the CFG pass list to per-pass fixed points, threading the context.

        The context carries cross-pass state (the type facts produced by the
        interprocedural analysis).  It is created once per compilation.
        """
        context = MenaiCFGContext()
        for pass_ in self.cfg_passes:
            while True:
                cfg, changed = pass_.optimize(cfg, context)
                if not changed:
                    break

        return cfg

    def _run_optimization_passes(
        self,
        tree: _Tree,
        passes: Sequence[_OptimizationPass[_Tree]],
    ) -> _Tree:
        """
        Run each pass to its own fixed point, in list order.

        A pass is called repeatedly until it reports no change, then the next
        pass runs.  A pass is never revisited once it has converged, so a pass
        that has nothing left to do is not re-run by a later pass's change.

        This is deliberately not a whole-list fixed point: re-running the whole
        list until a full sweep reports no change would re-run every converged
        pass on every sweep, including the whole-program type analysis, and
        roughly doubles CFG optimisation time for no additional benefit.  Each
        pass's transformation is monotone or idempotent, so running each to its
        own fixed point reaches the same result.

        Args:
            tree: The IR tree or CFG root to optimize.
            passes: The passes to run, in order.

        Returns:
            The optimized tree.
        """
        for pass_ in passes:
            while True:
                tree, changed = pass_.optimize(tree)
                if not changed:
                    break

        return tree
