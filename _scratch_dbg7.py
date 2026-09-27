from menai.menai_compiler import MenaiCompiler
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.cfg.menai_cfg import MenaiCFGPhiInstr
from menai.cfg.menai_cfg_optimization_pass import collect_functions

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

target = [f for f in collect_functions(cfg) if f.binding_name == "integer<?"][0]
for b in target.blocks:
    print(f"block {b.id} label={b.label}")
    for ins in b.instrs:
        print("   ", ins)
    print("   term:", b.terminator)
