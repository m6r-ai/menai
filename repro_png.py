import sys
from pathlib import Path

sys.path.insert(0, "src")

from menai_benchmark.suites.png_encode.suite import _FIXTURE_CONTAINERS, _to_menai_expr
from menai import Menai
from menai.cfg.menai_cfg_predicate_fold import MenaiCFGPredicateFold
from menai.cfg.menai_cfg_interproc_type_analysis import MenaiCFGInterprocTypeAnalysis

DISABLE = sys.argv[1] if len(sys.argv) > 1 else ""

suite_dir = Path("src/menai_benchmark/suites/png_encode")
module_path = [str(suite_dir), "menai_modules"]

menai = Menai(module_path=module_path)

if DISABLE == "predfold":
    menai.compiler.cfg_passes = [
        p for p in menai.compiler.cfg_passes if not isinstance(p, MenaiCFGPredicateFold)
    ]
    print("disabled predicate fold")
elif DISABLE == "interproc":
    menai.compiler.cfg_passes = [
        p for p in menai.compiler.cfg_passes if not isinstance(p, MenaiCFGInterprocTypeAnalysis)
    ]
    print("disabled interproc type analysis")

for name, container_expr in _FIXTURE_CONTAINERS:
    expr = _to_menai_expr(container_expr)
    print(f"=== {name} ===")
    try:
        code = menai.compile(expr)
        result = menai.execute_raw(code)
        print("OK", type(result))
    except Exception as exc:
        print("ERROR:", type(exc).__name__, exc)
