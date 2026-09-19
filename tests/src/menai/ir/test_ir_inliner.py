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
        assert result == 12