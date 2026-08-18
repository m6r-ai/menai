"""Tests for the IR inlining pass."""

from typing import cast

import pytest

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
from menai.ir.menai_ir_inliner import MenaiIRInliner
from menai.menai_value import MenaiInteger


def _build_ir(source: str) -> MenaiIRReturn:
    """Compile source string to IR (stopping before IR optimization passes)."""
    lexer = MenaiLexer()
    ast_builder = MenaiASTBuilder()
    semantic = MenaiASTSemanticAnalyzer()
    desugarer = MenaiASTDesugarer()
    constant_folder = MenaiASTConstantFolder()
    ir_builder = MenaiIRBuilder()

    tokens = lexer.lex(source)
    ast = ast_builder.build(tokens, source, "<test>")
    checked = semantic.analyze(ast, source)
    desugared = desugarer.desugar(checked)
    desugared = constant_folder.optimize(desugared)
    return ir_builder.build(desugared)


def _inline(ir, prelude_lambdas=None):
    """Run the inliner on an IR tree."""
    inliner = MenaiIRInliner(prelude_lambdas=prelude_lambdas or {})
    return inliner.optimize(ir)


def _count_calls_to(ir, name: str) -> int:
    """Count direct calls to a named local variable in the IR tree."""
    if isinstance(ir, MenaiIRCall):
        func = ir.func_plan
        own = 1 if (isinstance(func, MenaiIRVariable) and func.name == name and func.var_type == 'local') else 0
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


class TestPreludeInlining:
    """Tests for inlining prelude functions."""

    @pytest.fixture
    def prelude_lambdas(self):
        """Build prelude lambdas from the prelude source."""
        from menai.menai import Menai
        from menai.menai_compiler import MenaiCompiler

        compiler = MenaiCompiler()
        prelude_ir = compiler.compile_to_ir(Menai._load_prelude_source(), name="<prelude>")
        return MenaiCompiler._extract_prelude_lambdas(prelude_ir)

    def test_map_list_inlined(self, prelude_lambdas):
        """map-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(map-list (lambda (x) (integer+ x 1)) (list 1 2 3))")
        new_ir, changed = _inline(ir, prelude_lambdas)

        assert not changed

    def test_filter_list_inlined(self, prelude_lambdas):
        """filter-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(filter-list (lambda (x) (integer>? x 2)) (list 1 2 3 4 5))")
        new_ir, changed = _inline(ir, prelude_lambdas)

        assert not changed

    def test_fold_list_inlined(self, prelude_lambdas):
        """fold-list should not be inlined (contains recursive helper)."""
        ir = _build_ir("(fold-list integer+ 0 (list 1 2 3))")
        new_ir, changed = _inline(ir, prelude_lambdas)

        assert not changed

    def test_recursive_prelude_not_inlined(self, prelude_lambdas):
        """A recursive prelude function should not be inlined."""
        ir = _build_ir("(sort-list integer<? (list 3 1 2))")
        new_ir, changed = _inline(ir, prelude_lambdas)

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