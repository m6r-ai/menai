from menai import Menai
import sys
from menai.ir.menai_ir_letrec_to_loop import MenaiIRLetrecToLoop
from menai.cfg import menai_cfg_interproc_type_analysis as ita

SRC = chr(10).join([
    '(letrec ((f (lambda (x)',
    '              (letrec ((loop (lambda (i acc)',
    '                               (if (integer>=? i 10)',
    '                                   acc',
    '                                   (loop (integer+ i 1) (integer+ acc i))))))',
    '                (loop 0 x)))))',
    '  (f 5))',
])

orig = ita.MenaiCFGInterprocTypeAnalysis._call_sites


def patched(self, info):
    result = orig(self, info)
    from menai.cfg.menai_cfg import MenaiCFGSelfLoopTerm
    for block in info.func.blocks:
        term = block.terminator
        if isinstance(term, MenaiCFGSelfLoopTerm) and term.param_vals is not None:
            print(f"function {info.func.binding_name!r}: param_vals back-edge with {len(term.args)} args "
                  f"is reported as a call site: {any(c[0] is info.func for c in result)}")
    return result


ita.MenaiCFGInterprocTypeAnalysis._call_sites = patched

m = Menai()
if len(sys.argv) > 1 and sys.argv[1] == "noloop":
    m.compiler.ir_passes = [p for p in m.compiler.ir_passes if not isinstance(p, MenaiIRLetrecToLoop)]
print(m.evaluate_and_format(SRC))
