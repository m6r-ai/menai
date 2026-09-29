import sys
from pathlib import Path

sys.path.insert(0, "src")

from menai_benchmark.suites.png_encode.suite import _FIXTURE_CONTAINERS, _to_menai_expr
from menai import Menai
from menai.cfg.menai_cfg_optimization_pass import MenaiCFGContext

SET_LOG = {}
GET_LOG = []

orig_set_facts = MenaiCFGContext.set_facts
orig_facts_for = MenaiCFGContext.facts_for


def set_facts(self, func, facts):
    SET_LOG[id(func)] = (func.binding_name, len(facts), facts.get(7))
    return orig_set_facts(self, func, facts)


def facts_for(self, func):
    result = orig_facts_for(self, func)
    GET_LOG.append((id(func), func.binding_name, len(result), result.get(7)))
    return result


MenaiCFGContext.set_facts = set_facts
MenaiCFGContext.facts_for = facts_for

suite_dir = Path("src/menai_benchmark/suites/png_encode")
module_path = [str(suite_dir), "menai_modules"]
menai = Menai(module_path=module_path)

name, container_expr = _FIXTURE_CONTAINERS[2]  # alpha-96x96
expr = _to_menai_expr(container_expr)

try:
    code = menai.compile(expr)
    print("compiled OK")
except Exception as exc:
    print("COMPILE ERROR:", exc)

print("=== SET_LOG: id -> (binding, n, fact[7]) [encode only] ===")
for k, v in SET_LOG.items():
    if v[0] and "encode" in v[0]:
        print(f"  {k} -> {v}")

print("=== GET_LOG: (id, binding, n, fact[7]) [encode only] ===")
for entry in GET_LOG:
    if entry[1] and "encode" in entry[1]:
        print(f"  {entry}")
