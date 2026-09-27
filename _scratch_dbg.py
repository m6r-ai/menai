"""Scratch debug script."""

from menai.menai_compiler import MenaiCompiler
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg.menai_cfg import (
    MenaiCFGGuardInstr,
    MenaiCFGSelfLoopTerm,
    MenaiCFGPhiInstr,
)

SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1))))))
  (add 1 2 3))
"""


def build_cfg(source):
    compiler = MenaiCompiler()
    resolved = compiler.compile_to_resolved_ast(source, "<test>")
    desugared = compiler.ast_desugarer.desugar(resolved)
    for p in compiler.ast_passes:
        desugared = p.optimize(desugared)
    ir = compiler.ir_builder.build(desugared)
    for p in compiler.ir_passes:
        ir, _ = p.optimize(ir)
    cfg = compiler.cfg_builder.build(ir)
    return cfg, compiler


cfg, compiler = build_cfg(SRC)
print("=== FUNCTIONS ===")
for func in collect_functions(cfg):
    print(f"function {func.binding_name!r} params={func.param_count()}")
    for block in func.blocks:
        print(f"  block {block.id}: preds={[p.id for p in block.predecessors]}")
        for instr in block.instrs:
            print(f"    {instr}")
        print(f"    TERM: {block.terminator}")
