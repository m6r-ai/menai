from menai.menai_compiler import MenaiCompiler
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.vcode.menai_vcode_allocator import allocate_slots
from menai.vcode.menai_vcode_peephole import schedule_self_loop_moves, peephole

src = "(lambda (lst) (sort-list integer<? lst))"
c = MenaiCompiler()
resolved = c.compile_to_resolved_ast(src, "scratch")
c.ast_desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
desugared = c.ast_desugarer.desugar(resolved)
desugared = MenaiASTPreludeInjector.wrap(desugared)
desugared = MenaiASTConstantFolder().optimize(desugared)
ir = c.ir_builder.build(desugared)
for p in c.ir_passes:
    ir, _ = p.optimize(ir)
cfg = c.cfg_builder.build(ir)
for p in c.cfg_passes:
    cfg, _ = p.optimize(cfg)

from menai.cfg.menai_cfg_optimization_pass import collect_functions
target = [f for f in collect_functions(cfg) if f.binding_name == "integer<?"][0]

vcode = c.vcode_builder._lower_function(target)
vcode = schedule_self_loop_moves(vcode)

# Simulate removing r12/r13 from hoisted
import dataclasses
vcode2 = dataclasses.replace(vcode, hoisted_reg_ids=[h for h in vcode.hoisted_reg_ids if h not in (12, 13)])
sm = allocate_slots(vcode2)
print("slots:", {k: sm.slots[k] for k in sorted(sm.slots)})
vcode3 = peephole(vcode2, sm)
print("=== after peephole ===")
for i, ins in enumerate(vcode3.instrs):
    print(i, ins)
