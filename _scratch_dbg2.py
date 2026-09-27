"""Scratch debug script 2: trace type facts and guards."""

from menai.menai_compiler import MenaiCompiler
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg.menai_cfg import MenaiCFGGuardInstr

SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1))))))
  (add 1 2 3))
"""


def build_cfg_with_passes(source, stop_before=None):
    compiler = MenaiCompiler()
    resolved = compiler.compile_to_resolved_ast(source, "<test>")
    desugared = compiler.ast_desugarer.desugar(resolved)
    for p in compiler.ast_passes:
        desugared = p.optimize(desugared)
    ir = compiler.ir_builder.build(desugared)
    for p in compiler.ir_passes:
        ir, _ = p.optimize(ir)
    cfg = compiler.cfg_builder.build(ir)
    for p in compiler.cfg_passes:
        if stop_before is not None and type(p).__name__ == stop_before:
            break
        cfg, _ = p.optimize(cfg)
    return cfg, compiler


cfg, compiler = build_cfg_with_passes(SRC, stop_before="MenaiCFGGuardInsertion")
print("=== TYPE FACTS (before guard insertion) ===")
for func in collect_functions(cfg):
    print(f"function {func.binding_name!r}")
    for val_id, fact in sorted(func.type_facts.items()):
        print(f"  %{val_id} -> {fact.kind}")

cfg, compiler = build_cfg_with_passes(SRC, stop_before="MenaiCFGLICM")
print("=== GUARDS (after guard insertion) ===")
for func in collect_functions(cfg):
    print(f"function {func.binding_name!r}")
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGGuardInstr):
                print(f"  block {block.id}: {instr}")
