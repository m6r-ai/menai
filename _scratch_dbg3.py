"""Scratch debug script 3: instrument guard insertion."""

from menai.menai_compiler import MenaiCompiler
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg import menai_cfg_guard_insertion as gi

SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1))))))
  (add 1 2 3))
"""

orig = gi.MenaiCFGGuardInsertion._insert_guards


def patched(self, func):
    print("PASS: _insert_guards called")
    print("  type_facts:", {k: v.kind for k, v in func.type_facts.items()})
    return orig(self, func)


gi.MenaiCFGGuardInsertion._insert_guards = patched

compiler = MenaiCompiler()
resolved = compiler.compile_to_resolved_ast(SRC, "<test>")
desugared = compiler.ast_desugarer.desugar(resolved)
for p in compiler.ast_passes:
    desugared = p.optimize(desugared)
ir = compiler.ir_builder.build(desugared)
for p in compiler.ir_passes:
    ir, _ = p.optimize(ir)
cfg = compiler.cfg_builder.build(ir)
for p in compiler.cfg_passes:
    if type(p).__name__ == "MenaiCFGLICM":
        break
    cfg, _ = p.optimize(cfg)
