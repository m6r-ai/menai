from menai.menai_compiler import MenaiCompiler
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.vcode.menai_vcode import MenaiVCodeMove, MenaiVCodeFunction
from menai.vcode.menai_vcode_allocator import allocate_slots
from menai.vcode.menai_vcode_peephole import schedule_self_loop_moves

src = "(lambda (lst) (sort-list integer<? lst))"

c = MenaiCompiler()
resolved = c.compile_to_resolved_ast(src, "scratch")
c.ast_desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
desugared = c.ast_desugarer.desugar(resolved)
desugared = MenaiASTPreludeInjector.wrap(desugared)
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
desugared = MenaiASTConstantFolder().optimize(desugared)
ir = c.ir_builder.build(desugared)
for p in c.ir_passes:
    ir, _ = p.optimize(ir)
cfg = c.cfg_builder.build(ir)
for p in c.cfg_passes:
    cfg, _ = p.optimize(cfg)

# Find the integer<? function in the CFG by walking
from menai.cfg.menai_cfg_optimization_pass import collect_functions
funcs = collect_functions(cfg)
target = None
for f in funcs:
    if f.binding_name == "integer<?":
        target = f
        break
print("found:", target.binding_name if target else None)

# Build vcode for just this function
vcode = c.vcode_builder._lower_function(target)
print("=== BEFORE schedule_self_loop_moves ===")
for i, ins in enumerate(vcode.instrs):
    print(i, ins)
vcode = schedule_self_loop_moves(vcode)
print("=== AFTER schedule_self_loop_moves ===")
for i, ins in enumerate(vcode.instrs):
    print(i, ins)

print("=== SLOTS ===")
sm = allocate_slots(vcode)
for i, ins in enumerate(vcode.instrs):
    print(i, ins)
print("slots:", sm.slots)
print("loop_param_reg_ids:", vcode.loop_param_reg_ids)
print("param_reg_ids:", vcode.param_reg_ids)
print("free_var_reg_ids:", vcode.free_var_reg_ids)
print("hoisted_reg_ids:", vcode.hoisted_reg_ids)
