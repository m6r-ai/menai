"""
Validation: exercise the converted CFG model end to end.

Builds a CFG from source, runs the two converted passes, and lowers to VCode.
This bypasses menai/__init__.py (which still imports the unconverted passes)
by importing the modules directly.
"""

import sys

# Import the concrete modules directly rather than the menai package, whose
# __init__ pulls in the still-unconverted passes.
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_module_resolver import MenaiASTModuleResolver
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.cfg.menai_cfg import predecessors, blocks_by_id
from menai.cfg.menai_cfg_builder import MenaiCFGBuilder
from menai.cfg.menai_cfg_dead_captures import MenaiCFGDeadCaptures
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext, collect_functions
from menai.cfg.menai_cfg_order_exception_blocks import MenaiCFGOrderExceptionBlocks
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.ir.menai_ir_optimizer import MenaiIROptimizer
from menai.vcode.menai_vcode_builder import MenaiVCodeBuilder


def build_cfg(source: str):
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

    for p in (MenaiIRLetrecToLoop(), MenaiIRInliner(), MenaiIROptimizer()):
        while True:
            ir, changed = p.optimize(ir)
            if not changed:
                break

    return MenaiCFGBuilder().build(ir)


def dump(func, indent="  "):
    print(f"{indent}function {func.binding_name or '<root>'} "
          f"({len(func.blocks)} blocks, {len(func.params)} params)")
    for b in func.blocks:
        term = repr(b.terminator) if b.terminator is not None else "None"
        preds = [p.id for p in predecessors(func, b)]
        print(f"{indent}  b{b.id} [{b.label}] preds={preds} "
              f"instrs={len(b.instrs)} term={term}")


SOURCE = """
(letrec ((sum-to (lambda (n acc)
                   (if (integer=? n 0)
                       acc
                       (sum-to (integer- n 1) (integer+ acc n))))))
  (lambda (x) (integer+ (sum-to x 0) 1)))
"""

print("=== build CFG ===", flush=True)
cfg = build_cfg(SOURCE)
for f in collect_functions(cfg):
    dump(f)

print("=== run converted passes ===", flush=True)
ctx = MenaiCFGContext()
passes = [MenaiCFGOrderExceptionBlocks(), MenaiCFGDeadCaptures()]
for p in passes:
    while True:
        cfg, changed = p.optimize(cfg, ctx)
        print(f"  {type(p).__name__}: changed={changed}", flush=True)
        if not changed:
            break

print("=== after passes ===", flush=True)
for f in collect_functions(cfg):
    dump(f)

print("=== lower to VCode ===", flush=True)
vcode = MenaiVCodeBuilder().build(cfg)
print(f"  vcode ok: {type(vcode).__name__}", flush=True)
print("DONE", flush=True)
