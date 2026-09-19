"""Tests for the Menai module system.

Tests cover:
- Basic module import and usage
- Module caching
- Module search paths
- Circular dependency detection
- Module not found errors
- Nested/transitive imports
- Module with multiple exports
- Semantic validation of import expressions
- Second-class namespace enforcement
"""

import pytest

from menai import Menai
from menai.menai_error import (
    MenaiModuleNotFoundError,
    MenaiCircularImportError,
    MenaiEvalError
)


class TestModuleSystemBasics:
    """Test basic module loading and import functionality."""

    def test_simple_module_import(self, tmp_path):
        """Test importing and using a simple module."""
        module_file = tmp_path / "math_utils.menai"
        module_file.write_text("""
(let ((square (lambda (x) (integer* x x))))
  (export square))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((math (import "math_utils")))
  ((:: math square) 5))
''')

        assert result == 25

    def test_module_with_multiple_exports(self, tmp_path):
        """Test module with multiple exported functions."""
        module_file = tmp_path / "utils.menai"
        module_file.write_text("""
(let ((add-one (lambda (x) (integer+ x 1)))
      (double (lambda (x) (integer* x 2)))
      (negate (lambda (x) (integer-neg x))))
  (export add-one double negate))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((utils (import "utils")))
  (integer+ ((:: utils add-one) 10)
     ((:: utils double) 5)
     ((:: utils negate) 3)))
''')

        # add-one(10) = 11, double(5) = 10, negate(3) = -3
        # 11 + 10 + (-3) = 18
        assert result == 18

    def test_module_with_private_functions(self, tmp_path):
        """Test that functions not in the export form are private."""
        module_file = tmp_path / "private_test.menai"
        module_file.write_text("""
(letrec ((helper (lambda (x) (integer* x 2)))
         (public-fn (lambda (x) (helper x)))
         (also-public (lambda (x) (integer+ x 1))))
  (export public-fn also-public))
""")

        menai = Menai(module_path=[str(tmp_path)])

        # Can call public function
        result = menai.evaluate('''
(let ((mod (import "private_test")))
  ((:: mod public-fn) 5))
''')
        assert result == 10

        # A name not in the export form is not a namespace member.
        with pytest.raises(MenaiEvalError):
            menai.evaluate('''
(let ((mod (import "private_test")))
  (:: mod helper))
''')


class TestModuleCaching:
    """Test module caching behavior."""

    def test_module_cached_on_first_load(self, tmp_path):
        """Test that modules are cached after first load."""
        module_file = tmp_path / "cached.menai"
        module_file.write_text("""
(let ((value 42))
  (export value))
""")

        menai = Menai(module_path=[str(tmp_path)])

        menai.evaluate('(let ((m (import "cached"))) (:: m value))')

        # Check cache
        assert "cached" in menai.module_cache

    def test_same_module_imported_multiple_times(self, tmp_path):
        """Test that importing same module multiple times uses cache."""
        module_file = tmp_path / "multi.menai"
        module_file.write_text("""
(let ((fn (lambda (x) (integer* x x))))
  (export fn))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((m1 (import "multi"))
      (m2 (import "multi")))
  (integer+ ((:: m1 fn) 3)
     ((:: m2 fn) 4)))
''')

        # Should work correctly: 9 + 16 = 25
        assert result == 25

        # Module should only be in cache once
        assert "multi" in menai.module_cache

    def test_clear_module_cache(self, tmp_path):
        """Test clearing the module cache."""
        module_file = tmp_path / "clearable.menai"
        module_file.write_text("""
(let ((x 1))
  (export x))
""")

        menai = Menai(module_path=[str(tmp_path)])

        menai.evaluate('(let ((m (import "clearable"))) (:: m x))')
        assert "clearable" in menai.module_cache

        menai.clear_module_cache()
        assert "clearable" not in menai.module_cache


class TestModuleSearchPath:
    """Test module search path resolution."""

    def test_single_directory_search_path(self, tmp_path):
        """Test module resolution with single directory."""
        module_file = tmp_path / "single.menai"
        module_file.write_text('(let ((val 1)) (export val))')

        menai = Menai(module_path=[str(tmp_path)])
        result = menai.evaluate('(let ((m (import "single"))) (:: m val))')

        # Should successfully load
        assert result == 1

    def test_multiple_directory_search_path(self, tmp_path):
        """Test module resolution with multiple directories."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        # Module in first directory
        (dir1 / "first.menai").write_text('(let ((val 1)) (export val))')

        # Module in second directory
        (dir2 / "second.menai").write_text('(let ((val 2)) (export val))')

        menai = Menai(module_path=[str(dir1), str(dir2)])

        # Can import from both
        menai.evaluate('(let ((m (import "first"))) (:: m val))')
        menai.evaluate('(let ((m (import "second"))) (:: m val))')

    def test_first_match_wins_in_search_path(self, tmp_path):
        """Test that first matching module in search path is used."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        # Same module name in both directories with different values
        (dir1 / "duplicate.menai").write_text('(let ((val 1)) (export val))')
        (dir2 / "duplicate.menai").write_text('(let ((val 2)) (export val))')

        # dir1 is first in search path
        menai = Menai(module_path=[str(dir1), str(dir2)])

        result = menai.evaluate('''
(let ((mod (import "duplicate")))
  (:: mod val))
''')

        # Should get value from dir1 (list-first in search path)
        assert result == 1

    def test_subdirectory_modules(self, tmp_path):
        """Test importing modules from subdirectories."""
        subdir = tmp_path / "lib"
        subdir.mkdir()

        (subdir / "helper.menai").write_text('(let ((val 42)) (export val))')

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "lib/helper")))
  (:: mod val))
''')

        assert result == 42


class TestModuleErrors:
    """Test error handling in module system."""

    def test_module_not_found_error(self, tmp_path):
        """Test error when module file doesn't exist."""
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiModuleNotFoundError) as exc_info:
            menai.evaluate('(let ((m (import "nonexistent"))) (:: m x))')

        error_msg = str(exc_info.value)
        assert "not found" in error_msg.lower()
        assert "nonexistent" in error_msg

    def test_module_not_found_shows_search_paths(self, tmp_path):
        """Test that module not found error shows searched paths."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        menai = Menai(module_path=[str(dir1), str(dir2)])

        with pytest.raises(MenaiModuleNotFoundError) as exc_info:
            menai.evaluate('(let ((m (import "missing"))) (:: m x))')

        error_msg = str(exc_info.value)
        assert str(dir1) in error_msg or "dir1" in error_msg
        assert str(dir2) in error_msg or "dir2" in error_msg

    def test_import_with_wrong_number_of_arguments(self):
        """Test that import requires exactly one argument."""
        menai = Menai()

        # No arguments
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import))) (:: m x))')
        assert "wrong number of arguments" in str(exc_info.value).lower()

        # Too many arguments
        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import "mod1" "mod2"))) (:: m x))')
        assert "wrong number of arguments" in str(exc_info.value).lower()

    def test_import_requires_string_literal(self):
        """Test that import requires a string literal, not a variable."""
        menai = Menai()

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import 42))) (:: m x))')
        assert "string literal" in str(exc_info.value).lower()

    def test_import_empty_string_error(self):
        """Test that import rejects empty module names."""
        menai = Menai()

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import ""))) (:: m x))')
        assert "empty" in str(exc_info.value).lower()

    def test_module_with_syntax_error(self, tmp_path):
        """Test error when module has syntax errors."""
        module_file = tmp_path / "broken.menai"
        module_file.write_text('(this is not valid Menai')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(Exception):  # Will be a parse error
            menai.evaluate('(let ((m (import "broken"))) (:: m x))')

    def test_module_without_export_rejected(self, tmp_path):
        """A module whose body is not an export form is rejected."""
        module_file = tmp_path / "noexport.menai"
        module_file.write_text('(let ((x 1)) x)')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(Exception):
            menai.evaluate('(let ((m (import "noexport"))) (:: m x))')

    def test_module_exporting_unbound_name_rejected(self, tmp_path):
        """A module exporting a name it does not bind is rejected."""
        module_file = tmp_path / "bad_export.menai"
        module_file.write_text('(let ((x 1)) (export y))')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(Exception):
            menai.evaluate('(let ((m (import "bad_export"))) (:: m y))')


class TestImportBindingPosition:
    """Test that import is only valid as a binding value."""

    def test_import_in_call_argument_rejected(self, tmp_path):
        """An import used as a call argument is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(integer+ (import "mod") 1)')
        assert "binding" in str(exc_info.value).lower()

    def test_import_in_body_position_rejected(self, tmp_path):
        """An import used as a bare body expression is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(import "mod")')
        assert "binding" in str(exc_info.value).lower()


class TestSecondClassNamespaces:
    """Test that namespaces are second-class and cannot be used as values."""

    def test_namespace_passed_as_argument_rejected(self, tmp_path):
        """Passing a namespace as a function argument is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('''
(let ((m (import "mod")))
  (list m))
''')
        assert "namespace" in str(exc_info.value).lower()

    def test_namespace_returned_rejected(self, tmp_path):
        """Returning a namespace from a function is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('''
(let ((m (import "mod")))
  (let ((f (lambda () m)))
    (f)))
''')
        assert "namespace" in str(exc_info.value).lower()

    def test_namespace_member_access_allowed(self, tmp_path):
        """Direct member access on a bound namespace is allowed."""
        (tmp_path / "mod.menai").write_text('(let ((x 41)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((m (import "mod")))
  (integer+ (:: m x) 1))
''')
        assert result == 42

    def test_namespace_member_is_not_a_namespace(self, tmp_path):
        """A member fetched from a namespace is an ordinary value."""
        (tmp_path / "mod.menai").write_text('(let ((x 41)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((m (import "mod")))
  (let ((v (:: m x)))
    (integer+ v 1)))
''')
        assert result == 42

    def test_namespace_used_as_call_head_rejected(self, tmp_path):
        """A namespace used as a call head is rejected, not treated as member access."""
        (tmp_path / "mod.menai").write_text('(let ((fn (lambda (x) x))) (export fn))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('''
(let ((m (import "mod")))
  ((m fn) 1))
''')
        message = str(exc_info.value)
        assert "cannot be used as a function" in message.lower()
        assert "(:: m " in message

    def test_bare_member_access_form_rejected(self, tmp_path):
        """The old bare (namespace member) form is not member access and is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 41)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('''
(let ((m (import "mod")))
  (m x))
''')
        assert "cannot be used as a function" in str(exc_info.value).lower()

    def test_member_access_requires_namespace(self):
        """The first argument of :: must be a namespace in scope."""
        menai = Menai()

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(:: not-a-namespace member)')
        assert "not a namespace" in str(exc_info.value).lower()

    def test_member_access_wrong_arity_rejected(self, tmp_path):
        """:: requires exactly a namespace and a member."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import "mod"))) (:: m))')
        assert "wrong number of arguments" in str(exc_info.value).lower()

    def test_member_must_be_a_symbol(self, tmp_path):
        """The member name of :: must be an unquoted symbol."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import "mod"))) (:: m "x"))')
        assert "must be a symbol" in str(exc_info.value).lower()

    def test_unknown_member_rejected(self, tmp_path):
        """A member the module does not export is rejected."""
        (tmp_path / "mod.menai").write_text('(let ((x 1)) (export x))')
        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiEvalError) as exc_info:
            menai.evaluate('(let ((m (import "mod"))) (:: m nope))')
        assert "no member" in str(exc_info.value).lower()


class TestCircularImports:
    """Test circular dependency detection."""

    def test_direct_circular_import(self, tmp_path):
        """Test detection of direct circular dependency (A imports B imports A)."""
        (tmp_path / "module_a.menai").write_text(
            '(let ((b (import "module_b"))) (export v))')
        (tmp_path / "module_b.menai").write_text(
            '(let ((a (import "module_a"))) (export v))')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiCircularImportError) as exc_info:
            menai.evaluate('(let ((m (import "module_a"))) (:: m v))')

        error_msg = str(exc_info.value)
        assert "circular" in error_msg.lower()
        assert "module_a" in error_msg
        assert "module_b" in error_msg

    def test_circular_import_shows_chain(self, tmp_path):
        """Test that circular import error shows the import chain."""
        (tmp_path / "a.menai").write_text('(let ((b (import "b"))) (export v))')
        (tmp_path / "b.menai").write_text('(let ((a (import "a"))) (export v))')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiCircularImportError) as exc_info:
            menai.evaluate('(let ((m (import "a"))) (:: m v))')

        error_msg = str(exc_info.value)
        # Should show the chain: a -> b -> a
        assert "a" in error_msg
        assert "b" in error_msg
        assert "->" in error_msg or "chain" in error_msg.lower()

    def test_three_way_circular_import(self, tmp_path):
        """Test detection of three-way circular dependency (A -> B -> C -> A)."""
        (tmp_path / "x.menai").write_text('(let ((y (import "y"))) (export v))')
        (tmp_path / "y.menai").write_text('(let ((z (import "z"))) (export v))')
        (tmp_path / "z.menai").write_text('(let ((x (import "x"))) (export v))')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiCircularImportError) as exc_info:
            menai.evaluate('(let ((m (import "x"))) (:: m v))')

        error_msg = str(exc_info.value)
        assert "circular" in error_msg.lower()

    def test_self_import(self, tmp_path):
        """Test detection of module importing itself."""
        (tmp_path / "self.menai").write_text('(let ((s (import "self"))) (export v))')

        menai = Menai(module_path=[str(tmp_path)])

        with pytest.raises(MenaiCircularImportError) as exc_info:
            menai.evaluate('(let ((m (import "self"))) (:: m v))')

        error_msg = str(exc_info.value)
        assert "circular" in error_msg.lower()


class TestTransitiveImports:
    """Test modules that import other modules (nested/transitive imports)."""

    def test_two_level_import(self, tmp_path):
        """Test module that imports another module."""
        # Base module
        (tmp_path / "base.menai").write_text("""
(let ((add (lambda (x y) (integer+ x y))))
  (export add))
""")

        # Module that uses base
        (tmp_path / "wrapper.menai").write_text("""
(let ((base (import "base")))
  (let ((add-ten (lambda (x) ((:: base add) x 10))))
    (export add-ten)))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((w (import "wrapper")))
  ((:: w add-ten) 5))
''')

        assert result == 15

    def test_three_level_import_chain(self, tmp_path):
        """Test three-level import chain (A imports B imports C)."""
        # Level 3 (deepest)
        (tmp_path / "level3.menai").write_text("""
(let ((value 1))
  (export value))
""")

        # Level 2
        (tmp_path / "level2.menai").write_text("""
(let ((l3 (import "level3")))
  (let ((get-value (lambda () (:: l3 value))))
    (export get-value)))
""")

        # Level 1
        (tmp_path / "level1.menai").write_text("""
(let ((l2 (import "level2")))
  (let ((get-nested (lambda () ((:: l2 get-value)))))
    (export get-nested)))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((l1 (import "level1")))
  ((:: l1 get-nested)))
''')

        assert result == 1

    def test_diamond_dependency(self, tmp_path):
        """Test diamond dependency pattern (A imports B and C, both import D)."""
        # Base module (D)
        (tmp_path / "base.menai").write_text("""
(let ((value 10))
  (export value))
""")

        # B imports base
        (tmp_path / "left.menai").write_text("""
(let ((base (import "base")))
  (let ((get-left (lambda () (:: base value))))
    (export get-left)))
""")

        # C imports base
        (tmp_path / "right.menai").write_text("""
(let ((base (import "base")))
  (let ((get-right (lambda () (:: base value))))
    (export get-right)))
""")

        # A imports both B and C
        (tmp_path / "top.menai").write_text("""
(let ((left (import "left"))
      (right (import "right")))
  (let ((sum (lambda ()
               (integer+ ((:: left get-left))
                  ((:: right get-right))))))
    (export sum)))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((top (import "top")))
  ((:: top sum)))
''')

        # Should get 10 + 10 = 20 (base module cached and reused)
        assert result == 20


class TestModuleCompilation:
    """Test module system integration with compilation pipeline."""

    def test_module_with_let_bindings(self, tmp_path):
        """Test module using let bindings."""
        (tmp_path / "let_test.menai").write_text("""
(let ((x 10)
      (y 20))
  (let ((sum (lambda () (integer+ x y))))
    (export sum)))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "let_test")))
  ((:: mod sum)))
''')

        assert result == 30

    def test_module_with_letrec(self, tmp_path):
        """Test module using letrec for recursion."""
        (tmp_path / "recursive.menai").write_text("""
(letrec ((factorial (lambda (n)
                      (if (integer<=? n 1)
                          1
                          (integer* n (factorial (integer- n 1)))))))
  (export factorial))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "recursive")))
  ((:: mod factorial) 5))
''')

        assert result == 120

    def test_module_with_conditionals(self, tmp_path):
        """Test module using if expressions."""
        (tmp_path / "cond_test.menai").write_text("""
(let ((abs-val (lambda (x)
                 (if (integer<? x 0)
                     (integer-neg x)
                     x))))
  (export abs-val))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "cond_test")))
  ((:: mod abs-val) -42))
''')

        assert result == 42

    def test_module_with_higher_order_functions(self, tmp_path):
        """Test module using map, filter, fold."""
        (tmp_path / "hof.menai").write_text("""
(let ((sum-squares (lambda (lst)
                     (fold-list integer+ 0 (map-list (lambda (x) (integer* x x)) lst)))))
  (export sum-squares))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "hof")))
  ((:: mod sum-squares) (list 1 2 3 4)))
''')

        # 1^2 + 2^2 + 3^2 + 4^2 = 1 + 4 + 9 + 16 = 30
        assert result == 30


class TestModuleEdgeCases:
    """Test edge cases and unusual module scenarios."""

    def test_empty_export_module(self, tmp_path):
        """A module exporting nothing has no members."""
        (tmp_path / "empty.menai").write_text('(export)')

        menai = Menai(module_path=[str(tmp_path)])

        # An empty export form has no members; any member access fails.
        with pytest.raises(MenaiEvalError):
            menai.evaluate('''
(let ((mod (import "empty")))
  (:: mod anything))
''')

    def test_module_with_complex_data_structures(self, tmp_path):
        """Test module with nested dicts and lists."""
        (tmp_path / "complex.menai").write_text("""
(let ((data (list 1 2 3))
      (nested (dict "inner" 42)))
  (export data nested))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((mod (import "complex")))
  (let ((data (:: mod data))
        (nested (:: mod nested)))
    (integer+ (list-first data)
       (dict-get nested "inner"))))
''')

        # 1 + 42 = 43
        assert result == 43

    def test_multiple_menai_instances_separate_caches(self, tmp_path):
        """Test that different Menai instances have separate module caches."""
        (tmp_path / "test.menai").write_text('(let ((val 1)) (export val))')

        menai1 = Menai(module_path=[str(tmp_path)])
        menai2 = Menai(module_path=[str(tmp_path)])

        menai1.evaluate('(let ((m (import "test"))) (:: m val))')

        # menai1 has it cached
        assert "test" in menai1.module_cache

        # menai2 doesn't
        assert "test" not in menai2.module_cache

    def test_module_name_with_special_characters(self, tmp_path):
        """Test module names with underscores and hyphens."""
        (tmp_path / "my_module-v2.menai").write_text('(let ((x 1)) (export x))')

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('(let ((m (import "my_module-v2"))) (:: m x))')
        assert result == 1


class TestModuleNameCollisions:
    """Test that importing modules with clashing private names is safe."""

    def test_two_modules_with_same_private_name(self, tmp_path):
        """Two modules binding the same private name do not collide."""
        (tmp_path / "mod_a.menai").write_text("""
(let ((helper (lambda (x) (integer+ x 1))))
  (export helper))
""")
        (tmp_path / "mod_b.menai").write_text("""
(let ((helper (lambda (x) (integer* x 10))))
  (export helper))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((a (import "mod_a"))
      (b (import "mod_b")))
  (integer+ ((:: a helper) 5) ((:: b helper) 5)))
''')

        # (5 + 1) + (5 * 10) = 6 + 50 = 56
        assert result == 56

    def test_same_module_imported_twice_under_different_names(self, tmp_path):
        """Importing the same module twice keeps the two copies distinct."""
        (tmp_path / "shared.menai").write_text("""
(let ((f (lambda (x) (integer* x x))))
  (export f))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((s1 (import "shared"))
      (s2 (import "shared")))
  (integer+ ((:: s1 f) 3) ((:: s2 f) 4)))
''')

        # 9 + 16 = 25
        assert result == 25


class TestImportedStructAsPatternHead:
    """Test that an imported struct type works as a pattern head (Q2b)."""

    def test_imported_struct_pattern_head(self, tmp_path):
        """An imported struct bound to a local name is a valid pattern head."""
        (tmp_path / "shapes.menai").write_text("""
(letrec ((point (struct (x y)))
         (make-point (lambda (x y) (point x y))))
  (export point make-point))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((shapes (import "shapes")))
  (let ((Point (:: shapes point))
        (make-point (:: shapes make-point)))
    (let ((p (make-point 3 4)))
      (match p
        ((Point x y) (integer+ x y))))))
''')

        assert result == 7

    def test_imported_struct_constructor(self, tmp_path):
        """An imported struct bound to a local name is a valid constructor."""
        (tmp_path / "shapes.menai").write_text("""
(letrec ((point (struct (x y))))
  (export point))
""")

        menai = Menai(module_path=[str(tmp_path)])

        result = menai.evaluate('''
(let ((shapes (import "shapes")))
  (let ((Point (:: shapes point)))
    (let ((p (Point 5 6)))
      (struct-get p 'x))))
''')

        assert result == 5
