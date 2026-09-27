from menai.menai_compiler import MenaiCompiler
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.vcode.menai_vcode_allocator import allocate_slots
from menai.vcode.menai_vcode_peephole import schedule_self_loop_moves, peephole
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai import Menai

src = open("menai_modules/deflate-compress.menai").read()
c = MenaiCompiler(module_loader=Menai(module_path=["menai_modules"]))
resolved = c.compile_to_resolved_ast(src, "deflate-compress.menai")
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

target = [f for f in collect_functions(cfg) if f.binding_name == "add-candidates"][0]

vcode = c.vcode_builder._lower_function(target)
print("=== VCODE (pre-schedule) ===")
for i, ins in enumerate(vcode.instrs):
    print(i, ins)
print("loop_param_reg_ids:", vcode.loop_param_reg_ids)
print("param_reg_ids:", vcode.param_reg_ids)
print("free_var_reg_ids:", vcode.free_var_reg_ids)
print("hoisted_reg_ids:", vcode.hoisted_reg_ids)

vcode = schedule_self_loop_moves(vcode)
print("=== VCODE (post-schedule) ===")
for i, ins in enumerate(vcode.instrs):
    print(i, ins)

sm = allocate_slots(vcode)
print("=== SLOTS ===")
print(sm.slots)
print("local_count:", sm.local_count, "slot_count:", sm.slot_count)
