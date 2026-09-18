"""
Tests for MenaiCFGInterprocTypeAnalysis.

Covers:
  1. Struct field access through a function parameter is rewritten to the
     index-based form when every call site passes the same struct type.
  2. Struct field access stays symbol-based when the function is called with
     two different struct types (the receiver's type identity is not proven).
  3. Struct field access stays symbol-based when the receiver's type is not a
     struct at all.
  4. struct-set through a parameter is rewritten to struct-set-ref.
  5. The rewritten and non-rewritten forms produce identical results.
  6. The type fact lattice join rules.
  7. Guards are eliminated for parameters whose types are proven, and kept
     for parameters whose call-site types are ambiguous.
"""

from menai.cfg.menai_cfg import MenaiCFGBuiltinInstr, MenaiCFGGuardInstr
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg.menai_cfg_type_fact import ANY, BOTTOM, TypeFact, join
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiStructType


def _build_cfg(source: str):
    """Compile source through the full CFG pass pipeline and return the CFG."""
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
        cfg, _ = p.optimize(cfg)

    return cfg


def _field_ops(cfg) -> list[tuple[str, list]]:
    """Collect (op, args) for every struct field-access builtin in the module."""
    result: list[tuple[str, list]] = []
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGBuiltinInstr) and instr.op in {
                    'struct-get', 'struct-ref', 'struct-set', 'struct-set-ref',
                }:
                    result.append((instr.op, instr.args))

    return result


def _ops(cfg) -> list[str]:
    """Collect the op names of every struct field-access builtin in the module."""
    return [op for op, _ in _field_ops(cfg)]


def _defining_instr(cfg, value_id: int):
    """Return the instruction defining a value id anywhere in the module."""
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if hasattr(instr, 'result') and instr.result.id == value_id:
                    return instr

    raise AssertionError(f"no defining instruction for value {value_id}")


MONOMORPHIC_SRC = """
(letrec ((point (struct (x y)))
         (get-x (lambda (p) (struct-get p 'x))))
  (get-x (point 1 2)))
"""


class TestMonormorphicStructFieldAccess:
    """A parameter proven to be one struct type resolves to index access."""

    def test_struct_get_through_parameter_becomes_struct_ref(self):
        cfg = _build_cfg(MONOMORPHIC_SRC)
        assert 'struct-ref' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_resolved_index_is_constant_zero_for_first_field(self):
        cfg = _build_cfg(MONOMORPHIC_SRC)
        refs = [args for op, args in _field_ops(cfg) if op == 'struct-ref']
        assert len(refs) == 1
        index_value = refs[0][1]
        index_instr = _defining_instr(cfg, index_value.id)
        assert index_instr.value.to_python() == 0


POLYMORPHIC_SRC = """
(letrec ((point (struct (x y)))
         (vec (struct (x y)))
         (get-x (lambda (p n)
                  (if (integer<=? n 0)
                      (struct-get p 'x)
                      (get-x p (integer- n 1))))))
  (integer+ (get-x (point 1 2) 1) (get-x (vec 3 4) 1)))
"""


class TestPolymorphicStructFieldAccess:
    """A parameter used at two struct types cannot be resolved."""

    def test_struct_get_stays_symbol_based(self):
        cfg = _build_cfg(POLYMORPHIC_SRC)
        assert 'struct-get' in _ops(cfg)
        assert 'struct-ref' not in _ops(cfg)


NON_STRUCT_SRC = """
(letrec ((get-x (lambda (d) (struct-get d 'x))))
  (get-x (dict "x" 1)))
"""


class TestNonStructReceiver:
    """A receiver that is not a struct is left unchanged."""

    def test_struct_get_stays_symbol_based(self):
        cfg = _build_cfg(NON_STRUCT_SRC)
        assert 'struct-get' in _ops(cfg)
        assert 'struct-ref' not in _ops(cfg)


STRUCT_SET_SRC = """
(letrec ((point (struct (x y)))
         (with-x (lambda (p) (struct-set p 'x 10))))
  (with-x (point 1 2)))
"""


class TestStructSetThroughParameter:
    """struct-set through a proven parameter resolves to struct-set-ref."""

    def test_struct_set_becomes_struct_set_ref(self):
        cfg = _build_cfg(STRUCT_SET_SRC)
        assert 'struct-set-ref' in _ops(cfg)
        assert 'struct-set' not in _ops(cfg)


RETURN_CHAIN_SRC = """
(letrec ((cube (struct (x y z)))
         (make-cube (lambda (a b c) (cube a b c)))
         (move (lambda (c n)
                 (if (integer<=? n 0)
                     (cube (struct-get c 'y) (struct-get c 'z) (struct-get c 'x))
                     (move (cube (struct-get c 'y) (struct-get c 'z) (struct-get c 'x))
                           (integer- n 1))))))
  (move (move (make-cube 1 2 3) 1) 1))
"""


class TestStructTypeThroughReturnValues:
    """
    A struct type that enters a call chain through return values resolves.

    The receiver of each struct-get is either a function parameter fed by
    another function's return value or the result of a nested call, never a
    constructor at the call site.  Resolving these requires return-type
    propagation.
    """

    def test_struct_get_through_return_chain_becomes_struct_ref(self):
        cfg = _build_cfg(RETURN_CHAIN_SRC)
        assert 'struct-ref' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_return_chain_result(self, menai):
        assert menai.evaluate_and_format(RETURN_CHAIN_SRC) == "(cube 2 3 1)"


RETURN_ONLY_SRC = """
(letrec ((cube (struct (x y z)))
         (make (lambda (n)
                 (if (integer<=? n 0) (cube 1 2 3) (make (integer- n 1)))))
         (pass (lambda (c n)
                 (if (integer<=? n 0) c (pass c (integer- n 1)))))
         (read-x (lambda (c n)
                   (if (integer<=? n 0) (struct-get c 'x) (read-x c (integer- n 1))))))
  (read-x (pass (make 3) 2) 0))
"""


class TestStructTypeOnlyThroughReturns:
    """
    A struct type that reaches a field read only through return values.

    The cube is constructed inside `make`; `pass` forwards its parameter and
    returns it; `read-x` reads a field of its parameter.  Every function is
    recursive so none is inlined, and no call site passes a constructor.  The
    struct type can only reach `read-x`'s parameter through return-type
    propagation.
    """

    def test_struct_get_becomes_struct_ref(self):
        cfg = _build_cfg(RETURN_ONLY_SRC)
        assert 'struct-ref' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(RETURN_ONLY_SRC) == "1"


SIBLING_SRC = """
(letrec ((cube (struct (x y)))
         (make (lambda (n)
                 (if (integer<=? n 0) (cube 1 2) (make (integer- n 1)))))
         (build (lambda (n)
                  (if (integer<=? n 0) (make 1) (build (integer- n 1)))))
         (read-x (lambda (n)
                   (if (integer<=? n 0)
                       (struct-get (build 1) 'x)
                       (read-x (integer- n 1))))))
  (read-x 1))
"""


class TestStructTypeThroughSiblingCall:
    """
    A struct type that reaches a field read through a call to a letrec sibling.

    `read-x` calls `build` and `build` calls `make`; both are letrec siblings,
    so each is reached through a captured free variable rather than a local
    make_closure.  The struct type flows make -> build -> the receiver of the
    struct-get, so resolving the field access requires resolving the sibling
    callees.
    """

    def test_struct_get_becomes_struct_ref(self):
        cfg = _build_cfg(SIBLING_SRC)
        assert 'struct-ref' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(SIBLING_SRC) == "1"


class TestResultsUnchanged:
    """Rewritten and non-rewritten forms produce identical results."""

    def test_monomorphic_result(self, menai):
        assert menai.evaluate_and_format(MONOMORPHIC_SRC) == "1"

    def test_polymorphic_result(self, menai):
        assert menai.evaluate_and_format(POLYMORPHIC_SRC) == "4"

    def test_struct_set_result(self, menai):
        assert menai.evaluate_and_format(STRUCT_SET_SRC) == "(point 10 2)"


class TestTypeFactJoin:
    """The type fact lattice join rules."""

    def test_bottom_is_identity(self):
        integer = TypeFact(kind='integer')
        assert join(BOTTOM, integer) == integer
        assert join(integer, BOTTOM) == integer

    def test_any_absorbs(self):
        integer = TypeFact(kind='integer')
        assert join(ANY, integer) == ANY
        assert join(integer, ANY) == ANY

    def test_same_kind_joins_to_that_kind(self):
        assert join(TypeFact(kind='integer'), TypeFact(kind='integer')) == TypeFact(kind='integer')

    def test_different_kinds_join_to_any(self):
        assert join(TypeFact(kind='integer'), TypeFact(kind='list')) == ANY

    def test_same_struct_type_joins_to_that_type(self):
        point = MenaiStructType('point', 1, ('x', 'y'))
        assert join(TypeFact(kind='struct', struct_type=point),
                    TypeFact(kind='struct', struct_type=point)) == TypeFact(kind='struct', struct_type=point)

    def test_different_struct_types_join_to_tagless_struct(self):
        point = MenaiStructType('point', 1, ('x', 'y'))
        vec = MenaiStructType('vec', 2, ('x', 'y'))
        assert join(TypeFact(kind='struct', struct_type=point),
                    TypeFact(kind='struct', struct_type=vec)) == TypeFact(kind='struct')


SHADOWED_DICT_SRC = """
(let ((p (struct (x y)))
      (q (struct (y x)))
      (f (lambda (n) (p 10 20)))
      (g (lambda (n) (q 30 40))))
  (letrec ((build (lambda (suffix n)
                    (if (integer<=? n 0)
                        (let ((d (dict (string-concat "k" suffix) f "k" g)))
                          (struct-get ((dict-get d "k") 0) 'x))
                        (build suffix (integer- n 1))))))
    (build "" 1)))
"""


class TestDictGetShadowedKey:
    """
    A dict-get whose constant key shadows an earlier computed key.

    The dict is last-wins, so the later constant "k" determines the result,
    not the earlier computed key.  The analysis may resolve the call to the
    function paired with the constant key, because no later pair could also
    equal "k".  The field read must produce the value from the second
    function's struct.
    """

    def test_result_is_from_last_pair(self, menai):
        assert menai.evaluate_and_format(SHADOWED_DICT_SRC) == "40"

    def test_field_access_resolved_to_last_pair_index(self):
        cfg = _build_cfg(SHADOWED_DICT_SRC)
        assert 'struct-ref' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)


def _guard_count(cfg) -> int:
    """Count guard instructions across all functions in the module."""
    n = 0
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGGuardInstr):
                    n += 1

    return n


PROVABLE_PARAM_SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1))))))
  (add 1 2 3))
"""


UNPROVABLE_PARAM_SRC = """
(letrec ((add (lambda (a b n)
                (if (integer<=? n 0)
                    (integer+ a b)
                    (add a b (integer- n 1)))))
         (flip (lambda (n) (if (integer<=? n 0) 0 (flop (integer- n 1)))))
         (flop (lambda (n) (if (integer<=? n 0) (list 1) (flip (integer- n 1))))))
  (add (flip 0) (flip 0) (flip 0)))
"""


class TestGuardElimination:
    """
    Guards are eliminated for parameters whose types the interprocedural
    analysis proves.
    """

    def test_provable_param_guards_eliminated(self):
        """Every call site passes an integer, so no integer guards are needed."""
        cfg = _build_cfg(PROVABLE_PARAM_SRC)
        assert _guard_count(cfg) == 0

    def test_unprovable_param_guards_inserted(self):
        """Ambiguous call-site types leave the parameters unprovable, so guards remain."""
        cfg = _build_cfg(UNPROVABLE_PARAM_SRC)
        assert _guard_count(cfg) > 0

    def test_provable_param_result_correct(self, menai):
        assert menai.evaluate_and_format(PROVABLE_PARAM_SRC) == "3"

    def test_unprovable_param_result_correct(self, menai):
        assert menai.evaluate_and_format(UNPROVABLE_PARAM_SRC) == "0"
