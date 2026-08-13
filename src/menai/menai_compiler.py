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
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.bytecode.menai_bytecode import CodeObject
from menai.bytecode.menai_bytecode_builder import MenaiBytecodeBuilder
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGOptimizationPass
from menai.cfg.menai_cfg_branch_const_prop import MenaiCFGBranchConstProp
from menai.cfg.menai_cfg_simplify_blocks import MenaiCFGSimplifyBlocks
from menai.cfg.menai_cfg_collapse_phi_chains import MenaiCFGCollapsePhiChains
from menai.cfg.menai_cfg_type_propagation import MenaiCFGTypePropagation
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ir.menai_ir_optimization_pass import MenaiIROptimizationPass
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ast.menai_lexer import MenaiLexer

from menai.ir.menai_ir import MenaiIRExpr, MenaiIRLambda, MenaiIRLet, MenaiIRLetrec, MenaiIRReturn


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

        self._prelude_lambdas: dict[str, MenaiIRLambda] = {}
        self.ast_builder = MenaiASTBuilder()
        self.ast_semantic_analyzer = MenaiASTSemanticAnalyzer()
        self.ast_module_resolver = MenaiASTModuleResolver(module_loader)
        self.ast_desugarer = MenaiASTDesugarer()
        self.ast_passes: list[MenaiASTOptimizationPass] = [
            MenaiASTConstantFolder(),
        ]
        self.ir_builder = MenaiIRBuilder()
        self._ir_inliner = MenaiIRInliner(prelude_lambdas=self._prelude_lambdas)
        self.ir_passes: list[MenaiIROptimizationPass] = [
            self._ir_inliner,
            MenaiIROptimizer(),
        ]
        self.cfg_builder = MenaiCFGBuilder()
        self.cfg_passes: list[MenaiCFGOptimizationPass] = [
            MenaiCFGCollapsePhiChains(),
            MenaiCFGBranchConstProp(),
            MenaiCFGSimplifyBlocks(),
            MenaiCFGTypePropagation(),
        ]
        self.vcode_builder = MenaiVCodeBuilder()
        self.bytecode_builder = MenaiBytecodeBuilder()

    def set_prelude_lambdas(self, lambdas: dict[str, MenaiIRLambda]) -> None:
        """
        Set the prelude lambda map used by the IR inliner.

        Called after prelude compilation to make prelude function bodies
        available for inlining into user code.
        """
        self._prelude_lambdas = lambdas
        self._ir_inliner.set_prelude_lambdas(lambdas)

    def compile_to_ir(self, source: str, name: str = "<module>") -> MenaiIRExpr:
        """
        Compile source through desugaring, AST optimization, and IR building.

        Returns the IR tree before any IR optimization passes run.
        Used to extract prelude lambda bodies for the inliner.
        """
        resolved_ast = self.compile_to_resolved_ast(source, name)
        desugared_ast = self.ast_desugarer.desugar(resolved_ast)

        for ast_pass in self.ast_passes:
            desugared_ast = ast_pass.optimize(desugared_ast)

        return self.ir_builder.build(desugared_ast)

    @staticmethod
    def _extract_prelude_lambdas(ir: MenaiIRExpr) -> dict[str, MenaiIRLambda]:
        """
        Walk the top-level IR of a prelude module and extract all
        let/letrec-bound lambdas indexed by binding name.
        """
        result: dict[str, MenaiIRLambda] = {}
        MenaiCompiler._collect_lambdas(ir, result)
        return result

    @staticmethod
    def _collect_lambdas(ir: MenaiIRExpr, result: dict[str, MenaiIRLambda]) -> None:
        """Recursively collect lambda bindings from let/letrec nodes."""
        if isinstance(ir, MenaiIRReturn):
            MenaiCompiler._collect_lambdas(ir.value_plan, result)

        if isinstance(ir, MenaiIRLet):
            for name, value_plan in ir.bindings:
                if isinstance(value_plan, MenaiIRLambda):
                    result[name] = value_plan

                else:
                    MenaiCompiler._collect_lambdas(value_plan, result)

            MenaiCompiler._collect_lambdas(ir.body_plan, result)

        elif isinstance(ir, MenaiIRLetrec):
            for name, value_plan in ir.bindings:
                if isinstance(value_plan, MenaiIRLambda):
                    result[name] = value_plan

                else:
                    MenaiCompiler._collect_lambdas(value_plan, result)

            MenaiCompiler._collect_lambdas(ir.body_plan, result)


    def compile_to_resolved_ast(self, source: str, source_file: str = "") -> MenaiASTNode:
        """
        Compile source to fully resolved AST.

        This runs the front-end compilation stages:
        - Lexing
        - Parsing
        - Semantic analysis
        - Module resolution (including recursive module compilation)

        The result is a fully resolved AST ready for desugaring and backend compilation.
        This method is used by the module system to compile imported modules.

        Args:
            source: Menai source code as a string
            source_file: Source file name for tracking origin of AST nodes

        Returns:
            Fully resolved AST (all imports replaced with module ASTs)
        """
        tokens = self.lexer.lex(source)
        ast = self.ast_builder.build(tokens, source, source_file)
        checked_ast = self.ast_semantic_analyzer.analyze(ast, source)
        resolved_ast = self.ast_module_resolver.resolve(checked_ast)
        return resolved_ast

    def compile(self, source: str, name: str = "<module>") -> CodeObject:
        """
        Compile Menai source code to bytecode.

        This is the main entry point that runs the complete pipeline.

        Args:
            source: Menai source code as a string
            name: Optional name for the code object (e.g. filename)

        Returns:
            Compiled bytecode ready for execution
        """
        resolved_ast = self.compile_to_resolved_ast(source, name)
        desugared_ast = self.ast_desugarer.desugar(resolved_ast)

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
