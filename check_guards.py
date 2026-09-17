"""Temporary: count type guards for a parameter whose type is interprocedurally known."""
from menai.menai_compiler import MenaiCompiler
from menai.cfg.menai_cfg import MenaiCFGGuardInstr
from menai.cfg.menai_cfg_optimization_pass import collect_functions

SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0) (integer+ a b) (add a b (integer- n 1))))))
  (add 1 2 3))
"""

compiler = MenaiCompiler()
resolved = compiler.compile_to_resolved_ast(SRC, "<t>")
desugared = compiler.ast_desugarer.desugar(resolved)
for p in compiler.ast_passes:
    desugared = p.optimize(desugared)
ir = compiler.ir_builder.build(desugared)
for p in compiler.ir_passes:
    ir, _ = p.optimize(ir)
cfg = compiler.cfg_builder.build(ir)
for p in compiler.cfg_passes:
    cfg, _ = p.optimize(cfg)

guards = 0
for f in collect_functions(cfg):
    for block in f.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGGuardInstr):
                guards += 1
                print(f"guard: {instr.value} is {instr.expected_type}")
print(f"total guards: {guards}")
