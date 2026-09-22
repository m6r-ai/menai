"""Tests for the IR inlining pass."""

from typing import cast

from menai.ir.menai_ir import (
    MenaiIRCall,
    MenaiIRConstant,
    MenaiIRIf,
    MenaiIRLambda,
    MenaiIRLet,
    MenaiIRLetrec,
    MenaiIRReturn,
    MenaiIRVariable,
)
from menai.ir.menai_ir_builder import MenaiIRBuilder
from menai.ast.menai_ast_desugarer import MenaiASTDesugarer
from menai.ast.menai_ast_constant_folder import MenaiASTConstantFolder
from menai.ast.menai_ast_builder import MenaiASTBuilder
from menai.ast.menai_ast_semantic_analyzer import MenaiASTSemanticAnalyzer
from menai.ast.menai_lexer import MenaiLexer
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.menai_value import MenaiInteger


def _build_ir(source: str, inject_prelude: bool = False) -> MenaiIRReturn:
    """Compile source string to IR (stopping before IR optimization passes).

    When inject_prelude is True the program is wrapped in the prelude's lexical
    bindings, matching how the compiler compiles a top-level program: the
    program is desugared first, then wrapped in the prelude's cached desugared
    bindings.
    """
    lexer = MenaiLexer()
    ast_builder = MenaiASTBuilder()
    semantic = MenaiASTSemanticAnalyzer()
    desugarer = MenaiASTDesugarer()
    constant_folder = MenaiASTConstantFolder()
    ir_builder = MenaiIRBuilder()

    tokens = lexer.lex(source)
    ast = ast_builder.build(tokens, source, "<test>")
    checked = semantic.analyze(ast, source)

    desugarer.temp_counter = MenaiASTPreludeInjector.prelude_temp_count()
    desugared = desugarer.desugar(checked)

    if inject_prelude:
        desugared = MenaiASTPreludeInjector.wrap(desugared)

    desugared = constant_folder.optimize(desugared)
    return ir_builder.build(desugared)


def _inline(ir):
    """Run the inliner on an IR tree."""
    inliner = MenaiIRInliner()
    return inliner.optimize(ir)


def _count_calls_to(ir, name: str) -> int:
    """Count direct calls to a named variable in the IR tree."""
    if isinstance(ir, MenaiIRCall):
        func = ir.func_plan
        own = 1 if (isinstance(func, MenaiIRVariable) and func.name == name) else 0
        return own + sum(_count_calls_to(a, name) for a in ir.arg_plans) + _count_calls_to(ir.func_plan, name)

    if isinstance(ir, MenaiIRReturn):
        return _count_calls_to(ir.value_plan, name)

    if isinstance(ir, MenaiIRIf):
        return (_count_calls_to(ir.condition_plan, name)
                + _count_calls_to(ir.then_plan, name)
                + _count_calls_to(ir.else_plan, name))

    if isinstance(ir, MenaiIRLet):
        return sum(_count_calls_to(v, name) for _, v in ir.bindings) + _count_calls_to(ir.body_plan, name)

    if isinstance(ir, MenaiIRLetrec):
        return sum(_count_calls_to(v, name) for _, v in ir.bindings) + _count_calls_to(ir.body_plan, name)

    if isinstance(ir, MenaiIRLambda):
        return _count_calls_to(ir.body_plan, name)

    if isinstance(ir, (MenaiIRConstant, MenaiIRVariable)):
        return 0

    return 0


def _count_direct_lambda_calls(ir) -> int:
    """Count calls whose function position is itself a lambda."""
    if isinstance(ir, MenaiIRCall):
        own = 1 if isinstance(ir.func_plan, MenaiIRLambda) else 0
        return own + sum(_count_direct_lambda_calls(a) for a in ir.arg_plans) + _count_direct_lambda_calls(ir.func_plan)

    if isinstance(ir, MenaiIRReturn):
        return _count_direct_lambda_calls(ir.value_plan)

    if isinstance(ir, MenaiIRIf):
        return (_count_direct_lambda_calls(ir.condition_plan)
                + _count_direct_lambda_calls(ir.then_plan)
                + _count_direct_lambda_calls(ir.else_plan))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (sum(_count_direct_lambda_calls(v) for _, v in ir.bindings)
                + _count_direct_lambda_calls(ir.body_plan))

    if isinstance(ir, MenaiIRLambda):
        return _count_direct_lambda_calls(ir.body_plan)

    return 0


def _count_builtin_calls(ir, name: str) -> int:
    """Count calls to a builtin by name in the IR tree."""
    if isinstance(ir, MenaiIRCall):
        own = 1 if ir.is_builtin and ir.builtin_name == name else 0
        return own + _count_builtin_calls(ir.func_plan, name) + sum(_count_builtin_calls(a, name) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _count_builtin_calls(ir.value_plan, name)

    if isinstance(ir, MenaiIRIf):
        return (_count_builtin_calls(ir.condition_plan, name)
                + _count_builtin_calls(ir.then_plan, name)
                + _count_builtin_calls(ir.else_plan, name))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (sum(_count_builtin_calls(v, name) for _, v in ir.bindings)
                + _count_builtin_calls(ir.body_plan, name))

    if isinstance(ir, MenaiIRLambda):
        return _count_builtin_calls(ir.body_plan, name)

    return 0


def _count_calls_to_constant(ir) -> int:
    """Count calls whose function position is a constant."""
    if isinstance(ir, MenaiIRCall):
        own = 1 if isinstance(ir.func_plan, MenaiIRConstant) else 0
        return own + _count_calls_to_constant(ir.func_plan) + sum(_count_calls_to_constant(a) for a in ir.arg_plans)

    if isinstance(ir, MenaiIRReturn):
        return _count_calls_to_constant(ir.value_plan)

    if isinstance(ir, MenaiIRIf):
        return (_count_calls_to_constant(ir.condition_plan)
                + _count_calls_to_constant(ir.then_plan)
                + _count_calls_to_constant(ir.else_plan))

    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        return (sum(_count_calls_to_constant(v) for _, v in ir.bindings)
                + _count_calls_to_constant(ir.body_plan))

    if isinstance(ir, MenaiIRLambda):
        return _count_calls_to_constant(ir.body_plan)

    return 0


def _count_bindings_named(ir, prefix: str) -> int:
    """Count let/letrec bindings whose name starts with *prefix*."""
    count = 0
    if isinstance(ir, (MenaiIRLet, MenaiIRLetrec)):
        count += sum(1 for name, _ in ir.bindings if name.startswith(prefix))
        count += sum(_count_bindings_named(v, prefix) for _, v in ir.bindings)
        count += _count_bindings_named(ir.body_plan, prefix)
        return count

    if isinstance(ir, MenaiIRReturn):
        return _count_bindings_named(ir.value_plan, prefix)

    if isinstance(ir, MenaiIRIf):
        return (_count_bindings_named(ir.condition_plan, prefix)
                + _count_bindings_named(ir.then_plan, prefix)
                + _count_bindings_named(ir.else_plan, prefix))

    if isinstance(ir, MenaiIRLambda):
        return _count_bindings_named(ir.body_plan, prefix)

    if isinstance(ir, MenaiIRCall):
        return _count_bindings_named(ir.func_plan, prefix) + sum(_count_bindings_named(a, prefix) for a in ir.arg_plans)

    return 0


class _RecordingInliner(MenaiIRInliner):
    """An inliner that records the letrec names in scope at each call candidate."""

    def __init__(self, seen_letrec_names: list[set[str]]) -> None:
        super().__init__()
        self._seen_letrec_names = seen_letrec_names

    def _is_inlineable(self, target, func_plan, letrec_names, arg_count):
        """Record the letrec names in scope, then delegate to the base check."""
        self._seen_letrec_names.append(set(letrec_names))
        return super()._is_inlineable(target, func_plan, letrec_names, arg_count)


class TestLocalLambdaInlining:
    """Tests for inlining locally-bound lambdas."""

    def test_simple_let_bound_lambda_inlined(self):
        """A let-bound lambda called once should be inlined."""
        ir = _build_ir("(let ((f (lambda (x) (integer+ x 1)))) (f 5))")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_calls_to(new_ir, 'f') == 0

    def test_let_bound_lambda_called_multiple_times_inlined(self):
        """A let-bound lambda called multiple times should be inlined at all call sites."""
        ir = _build_ir("(let ((double (lambda (x) (integer* x 2)))) (integer+ (double 1) (double 2)))")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_calls_to(new_ir, 'double') == 0

    def test_recursive_lambda_not_inlined(self):
        """A recursive lambda in a letrec should not be inlined."""
        ir = _build_ir("""
            (letrec ((loop (lambda (n) (if (integer=? n 0) 0 (loop (integer- n 1))))))
              (loop 5))
        """)
        new_ir, changed = _inline(ir)

        assert not changed

    def test_recursion_check_fires_for_letrec_body_call(self):
        """
        A call to a letrec-bound lambda made from the letrec's body is rejected
        by the recursion check.

        The lambda captures itself as a sibling, so the captures check would also
        reject it.  This test asserts that the letrec's binding names are in scope
        at the call site, which is what makes the recursion check fire, so a
        regression in the letrec-scope tracking is caught.
        """
        ir = _build_ir("""
            (letrec ((loop (lambda (n) (if (integer=? n 0) 0 (loop (integer- n 1))))))
              (loop 5))
        """)
        seen_letrec_names = []
        recorder = _RecordingInliner(seen_letrec_names)
        recorder.optimize(ir)

        assert seen_letrec_names
        assert all('loop' in names for names in seen_letrec_names)

    def test_arity_mismatch_not_inlined(self):
        """A lambda called with wrong number of arguments should not be inlined."""
        ir = _build_ir("(let ((f (lambda (x) x))) (f 1 2))")
        new_ir, changed = _inline(ir)

        assert not changed

    def test_large_lambda_not_inlined(self):
        """A lambda with a body exceeding the node threshold should not be inlined."""
        deep = "(integer+ x " * 25 + "0" + ")" * 25
        ir = _build_ir(f"(let ((f (lambda (x) {deep}))) (f 5))")
        new_ir, changed = _inline(ir)

        assert not changed

    def test_variadic_lambda_inlined(self):
        """A variadic lambda should be inlined with rest args packed into a list."""
        ir = _build_ir("(let ((f (lambda (. args) args))) (f 1 2 3))")
        new_ir, changed = _inline(ir)

        assert changed

    def test_variadic_lambda_with_fixed_params_inlined(self):
        """A variadic lambda with fixed params should be inlined correctly."""
        ir = _build_ir("(let ((f (lambda (x y . rest) (integer+ x y)))) (f 1 2 3 4))")
        new_ir, changed = _inline(ir)

        assert changed

    def test_variadic_lambda_under_arity_not_inlined(self):
        """A variadic lambda called with too few args should not be inlined."""
        ir = _build_ir("(let ((f (lambda (x y . rest) (integer+ x y)))) (f 1))")
        new_ir, changed = _inline(ir)

        assert not changed


class TestDirectLambdaApplicationInlining:
    """Tests for inlining calls whose function position is itself a lambda."""

    def test_direct_lambda_application_inlined(self):
        """A call to a lambda written at the call site should be inlined."""
        ir = _build_ir("((lambda (x) (integer+ x 1)) 5)")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_direct_lambda_calls(new_ir) == 0

    def test_direct_lambda_application_with_capture_inlined(self):
        """A direct lambda application whose lambda captures an outer variable is inlined."""
        ir = _build_ir("(let ((y 10)) ((lambda (x) (integer+ x y)) 5))")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_direct_lambda_calls(new_ir) == 0

    def test_nested_direct_lambda_applications_inlined(self):
        """A direct lambda application whose body is another direct application is inlined at both."""
        ir = _build_ir("((lambda (x) ((lambda (y) (integer+ x y)) 10)) 5)")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_direct_lambda_calls(new_ir) == 0

    def test_variadic_direct_lambda_application_inlined(self):
        """A variadic direct lambda application is inlined with rest args packed into a list."""
        ir = _build_ir("((lambda (. args) args) 1 2 3)")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_direct_lambda_calls(new_ir) == 0

    def test_nested_lambda_capturing_param_not_inlined(self):
        """A direct lambda application whose body captures a parameter is not inlined."""
        ir = _build_ir("((lambda (x) (lambda () x)) 5)")
        new_ir, changed = _inline(ir)

        assert not changed

    def test_direct_lambda_application_arity_mismatch_not_inlined(self):
        """A direct lambda application with the wrong argument count is not inlined."""
        ir = _build_ir("((lambda (x) x) 1 2)")
        new_ir, changed = _inline(ir)

        assert not changed

    def test_large_direct_lambda_application_not_inlined(self):
        """A direct lambda application whose body exceeds the node threshold is not inlined."""
        deep = "(integer+ x " * 25 + "0" + ")" * 25
        ir = _build_ir(f"((lambda (x) {deep}) 5)")
        new_ir, changed = _inline(ir)

        assert not changed


class TestPreludeInlining:
    """Tests for inlining prelude functions."""

    def test_map_list_not_inlined(self):
        """map-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(map-list (lambda (x) (integer+ x 1)) (list 1 2 3))", inject_prelude=True)
        new_ir, changed = _inline(ir)

        assert not changed

    def test_filter_list_not_inlined(self):
        """filter-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(filter-list (lambda (x) (integer>? x 2)) (list 1 2 3 4 5))", inject_prelude=True)
        new_ir, changed = _inline(ir)

        assert not changed

    def test_fold_list_not_inlined(self):
        """fold-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(fold-list integer+ 0 (list 1 2 3))", inject_prelude=True)
        new_ir, changed = _inline(ir)

        assert not changed

    def test_recursive_prelude_not_inlined(self):
        """A recursive prelude function should not be inlined."""
        ir = _build_ir("(sort-list integer<? (list 3 1 2))", inject_prelude=True)
        new_ir, changed = _inline(ir)

        assert not changed


class TestInliningCorrectness:
    """Tests that inlining preserves semantics."""

    def test_inlined_identity_preserves_value(self):
        """Inlining (lambda (x) x) should preserve the argument value."""
        ir = _build_ir("(let ((id (lambda (x) x))) (id 42))")
        new_ir, changed = _inline(ir)

        assert changed

    def test_inlined_addition_correct(self):
        """Inlining a lambda that adds should produce the correct result."""
        ir = _build_ir("(let ((add1 (lambda (x) (integer+ x 1)))) (add1 5))")
        new_ir, changed = _inline(ir)

        assert changed

    def test_shadowed_variable_not_substituted(self):
        """A parameter name shadowed by an inner let should not be substituted."""
        ir = _build_ir("""
            (let ((f (lambda (x) (let ((x 10)) (integer+ x 1)))))
              (f 5))
        """)
        new_ir, changed = _inline(ir)

        assert changed


class TestParameterCallNotInlined:
    """Tests that a call to a parameter is never inlined as the enclosing lambda."""

    def test_parameter_call_not_inlined_as_enclosing_lambda(self):
        """A call to a parameter is not replaced by the enclosing lambda's body."""
        ir = _build_ir("(let ((g (lambda (f) (f 5)))) (g (lambda (x) (integer+ x 1))))")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_calls_to_constant(new_ir) == 0

    def test_parameter_call_result_correct(self):
        """A higher-order call through a parameter evaluates to the expected value."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("(let ((g (lambda (f) (f 5)))) (g (lambda (x) (integer+ x 1))))")
        assert result == 6


class TestInliningIntegration:
    """Tests that inlining integrates with the full compilation pipeline."""

    def test_map_list_produces_correct_result(self):
        """map-list inlining should produce the correct result end-to-end."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("(map-list (lambda (x) (integer+ x 1)) (list 1 2 3))")
        assert result == [2, 3, 4]

    def test_filter_list_produces_correct_result(self):
        """filter-list inlining should produce the correct result end-to-end."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("(filter-list (lambda (x) (integer>? x 2)) (list 1 2 3 4 5))")
        assert result == [3, 4, 5]

    def test_fold_list_produces_correct_result(self):
        """fold-list inlining should produce the correct result end-to-end."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("(fold-list integer+ 0 (list 1 2 3 4 5))")
        assert result == 15

    def test_function_composition_not_broken(self):
        """Function composition (which returns a lambda) should still work."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("""
            (let* ((add1 (lambda (x) (integer+ x 1)))
                   (mul2 (lambda (x) (integer* x 2)))
                   (compose (lambda (f g) (lambda (x) (f (g x)))))
                   (func1 (compose mul2 add1)))
              (func1 5))
        """)


class TestArgumentLetBinding:
    """Tests for hoisting a duplicated non-trivial argument into a let binding."""

    def test_duplicated_call_argument_bound_once(self):
        """A non-trivial argument used in several branches is evaluated once."""
        ir = _build_ir("""
            (let ((f (lambda (s) (if (integer=? s 0) "a" (if (integer=? s 1) "b" "c"))))
                  (v 3))
              (f (integer+ v 2)))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_builtin_calls(new_ir, 'integer+') == 1
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 1

    def test_duplicated_argument_uses_fresh_variable(self):
        """The hoisted argument is referenced through the fresh binding, not re-evaluated."""
        ir = _build_ir("""
            (let ((f (lambda (s) (integer+ s s)))
                  (v 3))
              (f (integer* v 4)))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_builtin_calls(new_ir, 'integer*') == 1
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 1

    def test_variable_argument_not_bound(self):
        """A variable argument is not hoisted, even when used several times."""
        ir = _build_ir("""
            (let ((f (lambda (s) (integer+ s s)))
                  (v 7))
              (f v))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 0

    def test_constant_argument_not_bound(self):
        """A constant argument is not hoisted, even when used several times."""
        ir = _build_ir("(let ((f (lambda (s) (integer+ s s)))) (f 7))")
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 0

    def test_single_use_argument_not_bound(self):
        """A non-trivial argument used once is not hoisted."""
        ir = _build_ir("""
            (let ((f (lambda (s) (integer+ s 1)))
                  (v 3))
              (f (integer* v 4)))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 0

    def test_shadowed_parameter_uses_not_counted(self):
        """Uses of a parameter shadowed by an inner binding are not counted."""
        ir = _build_ir("""
            (let ((f (lambda (s) (let ((s 100)) (integer+ s s))))
                  (v 3))
              (f (integer* v 4)))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 0

    def test_distinct_arguments_bound_separately(self):
        """Two duplicated non-trivial arguments each get their own binding."""
        ir = _build_ir("""
            (let ((f (lambda (a b) (integer+ a b a b)))
                  (v 3))
              (f (integer* v 2) (integer* v 5)))
        """)
        new_ir, changed = _inline(ir)

        assert changed
        assert _count_bindings_named(new_ir, '#:inline-tmp-') == 2

    def test_duplicated_argument_result_correct(self):
        """Hoisting a duplicated argument preserves the computed result."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("""
            (let ((f (lambda (s) (integer+ s s s)))
                  (v 6))
              (f (integer* v 7)))
        """)
        assert result == 126

    def test_tail_call_duplicated_argument_result_correct(self):
        """Hoisting a duplicated argument in a tail call preserves the result."""
        from menai.menai import Menai

        m = Menai()
        result = m.evaluate("""
            (let ((f (lambda (s) (if (integer=? s 0) 0 (integer+ s s))))
                  (v 5))
              (f (integer* v 5)))
        """)
        assert result == 50
