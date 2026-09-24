"""
Tests for MenaiCFGInterprocTypeAnalysis.

Covers:
  1. Struct field access through a function parameter is rewritten to the
     index-based form when every call site passes the same struct type.
  2. Struct field access stays symbol-based when the function is called with
     two different struct types (the receiver's type identity is not proven).
  3. Struct field access stays symbol-based when the receiver's type is not a
     struct at all.
  4. struct-set through a parameter is rewritten to the indexed form.
  5. The rewritten and non-rewritten forms produce identical results.
  6. The type fact lattice join rules.
  7. Guards are eliminated for parameters whose types are proven, and kept
     for parameters whose call-site types are ambiguous.
  8. A parameter reached only by calls inside a recursion cycle is not proven
     from those calls, so its guard is retained.
  9. A receiver whose type is proven only by a struct-is-instance? refinement
     (including the phi an (and ...) guard lowers to) is rewritten too, and
     nested struct destructuring resolves every field read.  This holds when
     the struct type is a free variable captured from an enclosing scope, not
     only when it is a local constant.
 10. A parameter fed two different struct types by two functions inside the
     same recursion component is ambiguous, so its field access stays
     symbol-based and the result is correct.
"""

import pytest

from menai import MenaiError
from menai.cfg.menai_cfg import (
    MenaiCFGBuiltinInstr,
    MenaiCFGConstInstr,
    MenaiCFGGuardInstr,
    MenaiCFGStructGetIndexedInstr,
    MenaiCFGStructSetIndexedInstr,
)
from menai.cfg.menai_cfg_optimization_pass import collect_functions
from menai.cfg.menai_cfg_type_fact import ANY, BOTTOM, TypeFact, join
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiStructType, MenaiSymbol


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
    """
    Collect (op, args) for every struct field access in the module.

    Name-based accesses are reported under their builtin name ('struct-get',
    'struct-set'); the index-based structural instructions the type analysis
    emits are reported as 'struct-indexed-get' and 'struct-indexed-set', so a caller can
    treat the two forms uniformly.
    """
    result: list[tuple[str, list]] = []
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGStructGetIndexedInstr):
                    result.append(('struct-indexed-get', [instr.struct]))
                elif isinstance(instr, MenaiCFGStructSetIndexedInstr):
                    result.append(('struct-indexed-set', [instr.struct, instr.value]))
                elif isinstance(instr, MenaiCFGBuiltinInstr) and instr.op in {
                    'struct-get', 'struct-set',
                }:
                    result.append((instr.op, instr.args))

    return result


def _ops(cfg) -> list[str]:
    """Collect the op names of every struct field-access builtin in the module."""
    return [op for op, _ in _field_ops(cfg)]


def _indexed_gets(cfg) -> list:
    """Return every index-based struct-get instruction in the module."""
    result = []
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGStructGetIndexedInstr):
                    result.append(instr)

    return result


def _indexed_get_functions(cfg) -> set[str]:
    """Return the binding names of functions containing an index-based struct-get."""
    result: set[str] = set()
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGStructGetIndexedInstr):
                    result.add(func.binding_name)

    return result


def _symbol_const_count(cfg) -> int:
    """Count constant instructions defining a quoted symbol across the module."""
    n = 0
    for func in collect_functions(cfg):
        for block in func.blocks:
            for instr in block.instrs:
                if isinstance(instr, MenaiCFGConstInstr) and isinstance(instr.value, MenaiSymbol):
                    n += 1

    return n


MONOMORPHIC_SRC = """
(letrec ((point (struct (x y)))
         (get-x (lambda (p) (struct-get p 'x))))
  (get-x (point 1 2)))
"""


class TestMonormorphicStructFieldAccess:
    """A parameter proven to be one struct type resolves to index access."""

    def test_struct_get_through_parameter_becomes_indexed_get(self):
        cfg = _build_cfg(MONOMORPHIC_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_resolved_index_is_constant_zero_for_first_field(self):
        cfg = _build_cfg(MONOMORPHIC_SRC)
        gets = _indexed_gets(cfg)
        assert len(gets) == 1
        assert gets[0].index == 0


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
        assert 'struct-indexed-get' not in _ops(cfg)


NON_STRUCT_SRC = """
(letrec ((get-x (lambda (d) (struct-get d 'x))))
  (get-x (dict "x" 1)))
"""


class TestNonStructReceiver:
    """A receiver that is not a struct is left unchanged."""

    def test_struct_get_stays_symbol_based(self):
        cfg = _build_cfg(NON_STRUCT_SRC)
        assert 'struct-get' in _ops(cfg)
        assert 'struct-indexed-get' not in _ops(cfg)


STRUCT_SET_SRC = """
(letrec ((point (struct (x y)))
         (with-x (lambda (p) (struct-set p 'x 10))))
  (with-x (point 1 2)))
"""


class TestStructSetThroughParameter:
    """struct-set through a proven parameter resolves to the indexed form."""

    def test_struct_set_becomes_indexed_set(self):
        cfg = _build_cfg(STRUCT_SET_SRC)
        assert 'struct-indexed-set' in _ops(cfg)
        assert 'struct-set' not in _ops(cfg)


class TestOrphanedSymbolConstantsRemoved:
    """
    The symbol constant that fed a rewritten field access is removed.

    Once struct-get/struct-set is rewritten to its index-based form the field
    symbol argument is no longer read by any instruction, so the constant that
    defined it is dead and must not survive into the backend.
    """

    def test_struct_get_symbol_constant_removed(self):
        cfg = _build_cfg(MONOMORPHIC_SRC)
        assert _symbol_const_count(cfg) == 0

    def test_struct_set_symbol_constant_removed(self):
        cfg = _build_cfg(STRUCT_SET_SRC)
        assert _symbol_const_count(cfg) == 0

    def test_unresolved_field_access_keeps_symbol_constant(self):
        cfg = _build_cfg(POLYMORPHIC_SRC)
        assert 'struct-get' in _ops(cfg)
        assert _symbol_const_count(cfg) > 0


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

    def test_struct_get_through_return_chain_becomes_indexed_get(self):
        cfg = _build_cfg(RETURN_CHAIN_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
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

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(RETURN_ONLY_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
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

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(SIBLING_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(SIBLING_SRC) == "1"


CAPTURED_RECEIVER_SRC = """
(letrec ((cube (struct (x y)))
         (read-x (lambda (c) (struct-get c 'x)))
         (outer (lambda (c)
                  (letrec ((loop (lambda () (read-x c))))
                    (loop)))))
  (outer (cube 1 2)))
"""


class TestStructTypeThroughCapturedVariable:
    """
    A struct type that reaches a field read through a captured free variable.

    `outer`'s parameter is proven to be a cube by its call site.  `loop`
    captures that parameter and passes it to `read-x`, so `read-x`'s receiver
    is only ever fed by a free variable.  Resolving the field access requires
    deriving the free variable's fact from the value captured in the parent.
    """

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(CAPTURED_RECEIVER_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(CAPTURED_RECEIVER_SRC) == "1"


CAPTURED_SIBLING_RECEIVER_SRC = """
(letrec ((cube (struct (x y)))
         (read-x (lambda (c) (struct-get c 'x)))
         (outer (lambda (c)
                  (letrec ((helper (lambda () (read-x c)))
                           (loop (lambda () (helper))))
                    (loop)))))
  (outer (cube 1 2)))
"""


class TestStructTypeThroughCapturedSiblingVariable:
    """
    A captured free variable that is itself captured by a nested closure.

    `outer`'s proven cube parameter is captured by `helper`, and `helper` is
    captured by `loop` as a letrec sibling.  The struct type must flow through
    two capture levels for `read-x`'s receiver to resolve.
    """

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(CAPTURED_SIBLING_RECEIVER_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(CAPTURED_SIBLING_RECEIVER_SRC) == "1"


class TestResultsUnchanged:
    """Rewritten and non-rewritten forms produce identical results."""

    def test_monomorphic_result(self, menai):
        assert menai.evaluate_and_format(MONOMORPHIC_SRC) == "1"

    def test_polymorphic_result(self, menai):
        assert menai.evaluate_and_format(POLYMORPHIC_SRC) == "4"

    def test_struct_set_result(self, menai):
        assert menai.evaluate_and_format(STRUCT_SET_SRC) == "(point 10 2)"


REFINED_RECEIVER_SRC = """
(let ((point (struct (x y)))
       (box (struct (item tag))))
  (let ((inner (struct-get (box (point 1 2) 9) 'item)))
    (if (struct-is-instance? inner point)
        (struct-get inner 'x)
        0)))
"""


class TestStructTypeThroughRefinement:
    """
    A receiver whose type is proven only by a struct-is-instance? refinement.

    `inner` is produced by a struct-get, so its definition-site fact is unknown.
    Its type is established only by the struct-is-instance? test on the branch
    that reads its field.  The rewrite must consult the block-local refined
    facts, not just the global per-value facts, for this access to resolve.
    """

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(REFINED_RECEIVER_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(REFINED_RECEIVER_SRC) == "1"


REFINED_SET_RECEIVER_SRC = """
(let ((point (struct (x y)))
       (box (struct (item tag))))
  (let ((inner (struct-get (box (point 1 2) 9) 'item)))
    (if (struct-is-instance? inner point)
        (struct-get (struct-set inner 'x 7) 'x)
        0)))
"""


class TestStructSetThroughRefinement:
    """
    A struct-set on a receiver whose type is proven only by a refinement.

    Mirrors TestStructTypeThroughRefinement for the update form: the receiver's
    type comes from the struct-is-instance? test, so the rewrite must read the
    block-local refined facts.
    """

    def test_struct_set_becomes_indexed_set(self):
        cfg = _build_cfg(REFINED_SET_RECEIVER_SRC)
        assert 'struct-indexed-set' in _ops(cfg)
        assert 'struct-set' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(REFINED_SET_RECEIVER_SRC) == "7"


FREE_VAR_STRUCT_TYPE_SRC = """
(let ((point (struct (x y)))
      (box (struct (item tag))))
  (letrec ((pick (lambda (inner) (if (struct-is-instance? inner point)
                                     (struct-get inner 'x)
                                     0))))
    (pick (struct-get (box (point 1 2) 9) 'item))))
"""


class TestStructTypeThroughFreeVariable:
    """
    A struct-is-instance? test whose struct type is a free variable.

    The struct type `point` is declared in an enclosing let, so inside `pick`
    it is a free variable rather than a constant.  The receiver `inner` comes
    from a struct-get, so its type is proven only by the struct-is-instance?
    test.  The rewrite must resolve the structtype argument through the value
    it captures, not only when it is a local constant.
    """

    def test_struct_get_becomes_indexed_get(self):
        cfg = _build_cfg(FREE_VAR_STRUCT_TYPE_SRC)
        assert 'struct-indexed-get' in _ops(cfg)
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(FREE_VAR_STRUCT_TYPE_SRC) == "1"


NESTED_DESTRUCTURE_SRC = """
(let ((point (struct (x y)))
       (box (struct (item tag))))
  (match (box (point 1 2) 9)
    ((box inner tag) (match inner ((point a b) (integer+ a b))))
    (_ 0)))
"""


class TestNestedDestructuring:
    """
    Nested struct destructuring resolves every field read.

    The desugarer lowers each destructured field to a name-based struct-get.
    The outer pattern's field comes from a constructor; the inner pattern's
    receiver is that extracted field, whose type is proven only by the inner
    struct-is-instance? test.  Every field read must resolve to the index form.
    """

    def test_all_field_reads_become_indexed_get(self):
        cfg = _build_cfg(NESTED_DESTRUCTURE_SRC)
        assert _ops(cfg).count('struct-indexed-get') == 3
        assert 'struct-get' not in _ops(cfg)

    def test_result(self, menai):
        assert menai.evaluate_and_format(NESTED_DESTRUCTURE_SRC) == "3"


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
    not the earlier computed key.  The field read produces the value from the
    second function's struct.
    """

    def test_result_is_from_last_pair(self, menai):
        assert menai.evaluate_and_format(SHADOWED_DICT_SRC) == "40"


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


SELF_RECURSIVE_NO_CALLER_SRC = """
(letrec ((walk (lambda (lst)
                 (if (list-null? lst)
                     (list)
                     (walk (list-rest lst))))))
  (list walk))
"""


MUTUAL_RECURSIVE_NO_CALLER_SRC = """
(letrec ((ping (lambda (lst)
                 (if (list-null? lst) (list) (pong (list-rest lst)))))
         (pong (lambda (lst)
                 (if (list-null? lst) (list) (ping (list-rest lst))))))
  (list ping pong))
"""


SELF_RECURSIVE_CONSTANT_BACK_EDGE_SRC = """
(letrec ((f (lambda (x)
              (if (list-null? x) x (f (list 1))))))
  (list f))
"""


class TestRecursiveParameterNotProvenFromCycle:
    """
    A parameter reached only by calls inside a recursion cycle is not proven
    from those calls.

    The arguments of a call inside a cycle are computed from the parameters
    the call is used to infer, so they describe a later iteration rather than
    the first invocation.  With no call site outside the cycle the first
    invocation's argument is unconstrained and the guard must be retained.
    """

    def test_self_recursive_no_caller_keeps_guard(self):
        cfg = _build_cfg(SELF_RECURSIVE_NO_CALLER_SRC)
        assert _guard_count(cfg) > 0

    def test_mutual_recursive_no_caller_keeps_guard(self):
        cfg = _build_cfg(MUTUAL_RECURSIVE_NO_CALLER_SRC)
        assert _guard_count(cfg) > 0

    def test_self_recursive_constant_back_edge_keeps_guard(self):
        cfg = _build_cfg(SELF_RECURSIVE_CONSTANT_BACK_EDGE_SRC)
        assert _guard_count(cfg) > 0

    def test_self_recursive_no_caller_rejects_non_list(self, menai):
        with pytest.raises(MenaiError):
            menai.evaluate(SELF_RECURSIVE_NO_CALLER_SRC.replace("(list walk)", '(walk "abc")'))

    def test_self_recursive_no_caller_accepts_list(self, menai):
        assert menai.evaluate_and_format(SELF_RECURSIVE_NO_CALLER_SRC.replace("(list walk)", "(walk (list 1 2 3))")) == "()"


GROUNDED_RECURSIVE_SRC = """
(letrec ((walk (lambda (lst)
                 (if (list-null? lst)
                     (list)
                     (walk (list-rest lst))))))
  (walk (list 1 2 3)))
"""


DEGRADED_RECURSIVE_SRC = """
(letrec ((result-type (struct (value)))
         (make-result (lambda (v) (result-type v)))
         (search-loop
          (lambda (bound)
            (if (integer>? bound 100)
                (list 1)
                (search-loop (struct-get (make-result (integer+ bound 1)) 'value))))))
  (search-loop 0))
"""


class TestRecursiveParameterGrounding:
    """
    An external call site grounds a parameter; a cycle call site can then
    degrade it but cannot ground it.
    """

    def test_grounded_parameter_guard_eliminated(self):
        cfg = _build_cfg(GROUNDED_RECURSIVE_SRC)
        assert _guard_count(cfg) == 0

    def test_grounded_parameter_result_correct(self, menai):
        assert menai.evaluate_and_format(GROUNDED_RECURSIVE_SRC) == "()"

    def test_cycle_degrading_grounded_parameter_keeps_guard(self):
        cfg = _build_cfg(DEGRADED_RECURSIVE_SRC)
        assert _guard_count(cfg) > 0

    def test_cycle_degrading_grounded_parameter_result_correct(self, menai):
        assert menai.evaluate_and_format(DEGRADED_RECURSIVE_SRC) == "(1)"


AMBIGUOUS_INNER_PARAM_SRC = """
(letrec
  ((Point (struct (x y)))
   (Other (struct (y x)))
   (a
    (lambda (p n)
      (if (integer=? n 0)
          (struct-get p 'x)
          (b p (integer- n 1)))))
   (b
    (lambda (p n)
      (if (integer=? n 0)
          (struct-get p 'x)
          (if (integer=? n 1)
              (c (Other 777 888) 5)
              (c p 0)))))
   (c
    (lambda (p n)
      (if (integer=? n 0)
          (struct-get p 'x)
          (b p 0)))))
  (a (Point 1 2) 2))
"""


class TestAmbiguousInnerParameter:
    """
    A parameter fed two different struct types by two functions inside the
    same recursion component is ambiguous, so its field access stays
    symbol-based.

    `b` is called with a Point from `a` and with an Other from `c`.  Both
    functions are in the same recursion component, so neither call site is
    external.  The receiver's struct type is not proven and the field read
    must not be resolved to a constant index.

    `a` is called only from outside the component, with a Point, so its own
    field read is resolved; only `b` and `c` are ambiguous.
    """

    def test_ambiguous_inner_parameters_stay_symbol_based(self):
        cfg = _build_cfg(AMBIGUOUS_INNER_PARAM_SRC)
        assert _indexed_get_functions(cfg) == {"a"}

    def test_ambiguous_inner_parameter_result_correct(self, menai):
        assert menai.evaluate_and_format(AMBIGUOUS_INNER_PARAM_SRC) == "888"
