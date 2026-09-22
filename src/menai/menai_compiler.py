"""
Menai Compiler - Orchestrates the complete compilation pipeline.

This is the main entry point for compiling Menai source code to bytecode.
It chains together all compilation passes in the correct order.
"""


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
from menai.cfg.menai_cfg_branch_const_prop import MenaiCFGBranchConstProp
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_collapse_phi_chains import MenaiCFGCollapsePhiChains
from menai.cfg.menai_cfg_dead_captures import MenaiCFGDeadCaptures
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion
from menai.cfg.menai_cfg_interproc_type_analysis import MenaiCFGInterprocTypeAnalysis
from menai.cfg.menai_cfg_licm import MenaiCFGLICM
from menai.cfg.menai_cfg_loop_rotation import MenaiCFGLoopRotation
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGOptimizationPass
from menai.cfg.menai_cfg_simplify_blocks import MenaiCFGSimplifyBlocks
from menai.cfg.menai_cfg_switch_dispatch import MenaiCFGSwitchDispatch
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.menai_value import MenaiValue
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder


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
        self.module_loader = module_loader

        self.lexer = MenaiLexer()

        self.ast_builder = MenaiASTBuilder()
        self.ast_semantic_analyzer = MenaiASTSemanticAnalyzer()
        self.ast_module_resolver = MenaiASTModuleResolver(module_loader)
        self.ast_prelude_injector = MenaiASTPreludeInjector()
        self.ast_desugarer = MenaiASTDesugarer()
        self.ast_passes: list[MenaiASTOptimizationPass] = [
            MenaiASTConstantFolder(),
        ]
        self.ir_builder = MenaiIRBuilder()
        self._ir_inliner = MenaiIRInliner()
        self.ir_passes: list[MenaiIROptimizationPass] = [
            self._ir_inliner,
            MenaiIROptimizer(),
        ]
        self.cfg_builder = MenaiCFGBuilder()
        self._interproc_type_analysis = MenaiCFGInterprocTypeAnalysis()
        self.cfg_passes: list[MenaiCFGOptimizationPass] = [
            MenaiCFGCollapsePhiChains(),
            MenaiCFGBranchConstProp(),
            MenaiCFGSimplifyBlocks(),
            MenaiCFGSwitchDispatch(),
            self._interproc_type_analysis,
            MenaiCFGGuardInsertion(),
            MenaiCFGLICM(),
            MenaiCFGLoopRotation(),
            MenaiCFGDeadCaptures(),
        ]
        self.vcode_builder = MenaiVCodeBuilder()
        self.bytecode_builder = MenaiBytecodeBuilder()

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
        tokens = self.lexer.lex(source)
        ast = self.ast_builder.build(tokens, source, source_file)
        checked_ast = self.ast_semantic_analyzer.analyze(ast, source)
        if is_program:
            resolved_ast = self.ast_module_resolver.resolve_program(checked_ast)

        else:
            resolved_ast = self.ast_module_resolver.resolve(checked_ast)

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

        # The user program is desugared on its own and then wrapped in the
        # prelude's cached desugared bindings.  The prelude is identical for
        # every compilation, so desugaring it once and reusing the result
        # avoids re-desugaring it on every compile.  The program's temporary
        # counter starts above the prelude's so generated names cannot collide.
        self.ast_desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
        desugared_program = self.ast_desugarer.desugar(resolved_ast)

        # The host binding sits above the prelude and below the program, so the
        # program sees both and the host binding shadows nothing in the prelude.
        if inject is not None:
            desugared_program = MenaiASTBindingInjector.wrap(desugared_program, *inject)

        desugared_ast = MenaiASTPreludeInjector.wrap(desugared_program)

        for ast_pass in self.ast_passes:
            desugared_ast = ast_pass.optimize(desugared_ast)

        ir = self.ir_builder.build(desugared_ast)

        for ir_pass in self.ir_passes:
            ir, _ = ir_pass.optimize(ir)

        cfg = self.cfg_builder.build(ir)
        for cfg_pass in self.cfg_passes:
            cfg, _ = cfg_pass.optimize(cfg)

        vcode = self.vcode_builder.build(cfg)
        bytecode = self.bytecode_builder.build(vcode, name)
        return bytecode
