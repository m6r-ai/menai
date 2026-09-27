"""Scratch debug script 4: instrument guard insertion internals."""

from menai.menai_compiler import MenaiCompiler
from menai.cfg.menai_cfg import MenaiCFGBranchTerm, MenaiCFGBuiltinInstr
from menai.bytecode.menai_type_signatures import BUILTIN_TYPE_SIGNATURES

SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1))))))
  (add 1 2 3))
"""

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
    if type(p).__name__ == "MenaiCFGGuardInsertion":
        break
    cfg, _ = p.optimize(cfg)

# Manually run the guard insertion logic with tracing.
from menai.cfg.menai_cfg_guard_insertion import MenaiCFGGuardInsertion

pass_obj = MenaiCFGGuardInsertion()
for func in [cfg]:
    types = {val_id: fact.kind for val_id, fact in func.type_facts.items()}
    print("types:", types)
    outgoing_types = {}
    branch_true_types = {}
    for block in func.blocks:
        preds = block.predecessors
        if len(preds) == 1:
            pred = preds[0]
            block_types = dict(outgoing_types.get(pred.id, types))
        elif len(preds) > 1:
            block_types = pass_obj._meet_outgoing_types(preds, outgoing_types, types)
        else:
            block_types = dict(types)
        print(f"block {block.id}: preds={[p.id for p in preds]} block_types={block_types}")
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGBuiltinInstr):
                sig = BUILTIN_TYPE_SIGNATURES.get(instr.op)
                print(f"   builtin {instr.op} args={[a.id for a in instr.args]} sig={sig} argtypes={[(a.id, block_types.get(a.id)) for a in instr.args]}")
        outgoing_types[block.id] = block_types
