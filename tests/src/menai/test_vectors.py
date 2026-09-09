"""Tests for vector operations."""

import pytest

from menai import MenaiEvalError


class TestVectors:
    """Test vector operations."""

    @pytest.mark.parametrize("expression,expected", [
        ('(vector)', '#vector()'),
        ('(vector 1)', '#vector(1)'),
        ('(vector 1 2)', '#vector(1 2)'),
        ('(vector 1 2 3)', '#vector(1 2 3)'),
        ('(vector 1 "hello" #t)', '#vector(1 "hello" #t)'),
        ('(vector "a" 2 #f 3.14)', '#vector("a" 2 #f 3.14)'),
        ('(vector (vector 1 2) (vector 3 4))', '#vector(#vector(1 2) #vector(3 4))'),
    ])
    def test_vector_construction(self, menai, expression, expected):
        """Test vector construction with various element types."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_construction_python_objects(self, menai):
        """Test that vector construction returns proper Python lists."""
        result = menai.evaluate('(vector 1 2 3)')
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    @pytest.mark.parametrize("expression,expected", [
        ('(vector? (vector 1 2 3))', '#t'),
        ('(vector? (vector))', '#t'),
        ('(vector? (list 1 2 3))', '#f'),
        ('(vector? "hello")', '#f'),
        ('(vector? 42)', '#f'),
        ('(vector? #t)', '#f'),
    ])
    def test_vector_type_predicate(self, menai, expression, expected):
        """Test the vector? type predicate."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector=? (vector 1 2 3) (vector 1 2 3))', '#t'),
        ('(vector=? (vector 1 2 3) (vector 1 2 4))', '#f'),
        ('(vector=? (vector 1 2) (vector 1 2 3))', '#f'),
        ('(vector=? (vector) (vector))', '#t'),
        ('(vector!=? (vector 1 2) (vector 1 3))', '#t'),
        ('(vector!=? (vector 1 2) (vector 1 2))', '#f'),
        ('(vector=? (vector 1 2 3) (vector 3 2 1))', '#f'),
    ])
    def test_vector_equality(self, menai, expression, expected):
        """Test element-wise vector equality; order matters."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_equality_requires_vectors(self, menai):
        """Test that vector=? rejects non-vector arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector=? (vector 1) (list 1))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector=? 42 (vector 1))')

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-ref (vector 1 2 3) 0)', '1'),
        ('(vector-ref (vector 1 2 3) 2)', '3'),
        ('(vector-ref (vector "a" "b") 1)', '"b"'),
        ('(vector-ref (vector (vector 1) (vector 2)) 1)', '#vector(2)'),
    ])
    def test_vector_ref(self, menai, expression, expected):
        """Test O(1) indexed access."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_ref_out_of_bounds(self, menai):
        """Test that vector-ref rejects out-of-bounds indices."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-ref (vector 1 2 3) 3)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-ref (vector 1 2 3) -1)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-ref (vector) 0)')

    def test_vector_ref_requires_vector(self, menai):
        """Test that vector-ref rejects non-vector arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-ref (list 1 2 3) 0)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-ref 42 0)')

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-length (vector))', '0'),
        ('(vector-length (vector 1))', '1'),
        ('(vector-length (vector 1 2 3))', '3'),
    ])
    def test_vector_length(self, menai, expression, expected):
        """Test vector-length."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-set (vector 1 2 3) 0 10)', '#vector(10 2 3)'),
        ('(vector-set (vector 1 2 3) 2 30)', '#vector(1 2 30)'),
        ('(vector-set (vector 1) 0 "x")', '#vector("x")'),
    ])
    def test_vector_set(self, menai, expression, expected):
        """Test functional update returns a new vector."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_set_original_unchanged(self, menai):
        """Test that vector-set does not modify the original vector."""
        assert menai.evaluate_and_format('(let ((v (vector 1 2 3))) (vector-set v 1 20))') == '#vector(1 20 3)'
        assert menai.evaluate_and_format(
            '(let ((v (vector 1 2 3)))'
            '  (let ((ignored (vector-set v 1 20)))'
            '    v))'
        ) == '#vector(1 2 3)'

    def test_vector_set_out_of_bounds(self, menai):
        """Test that vector-set rejects out-of-bounds indices."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-set (vector 1 2 3) 3 9)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-set (vector 1 2 3) -1 9)')

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-slice (vector 1 2 3 4 5) 0 0)', '#vector()'),
        ('(vector-slice (vector 1 2 3 4 5) 0 1)', '#vector(1)'),
        ('(vector-slice (vector 1 2 3 4 5) 0 3)', '#vector(1 2 3)'),
        ('(vector-slice (vector 1 2 3 4 5) 0 5)', '#vector(1 2 3 4 5)'),
        ('(vector-slice (vector 1 2 3 4 5) 1 3)', '#vector(2 3)'),
        ('(vector-slice (vector 1 2 3 4 5) 4 5)', '#vector(5)'),
        ('(vector-slice (vector 1 2 3 4 5) 5 5)', '#vector()'),
        ('(vector-slice (vector 1 "hello" #t 3.14) 0 2)', '#vector(1 "hello")'),
        ('(vector-slice (vector) 0 0)', '#vector()'),
    ])
    def test_vector_slice_two_args(self, menai, expression, expected):
        """Test vector slicing with explicit start and end."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-slice (vector 1 2 3 4 5) 0)', '#vector(1 2 3 4 5)'),
        ('(vector-slice (vector 1 2 3 4 5) 1)', '#vector(2 3 4 5)'),
        ('(vector-slice (vector 1 2 3 4 5) 3)', '#vector(4 5)'),
        ('(vector-slice (vector 1 2 3 4 5) 5)', '#vector()'),
        ('(vector-slice (vector 1 "hello" #t 3.14) 2)', '#vector(#t 3.14)'),
        ('(vector-slice (vector) 0)', '#vector()'),
    ])
    def test_vector_slice_one_arg(self, menai, expression, expected):
        """Test vector slicing from start to the end of the vector."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression", [
        '(vector-slice (vector 1 2 3) -1 2)',
        '(vector-slice (vector 1 2 3) 0 -1)',
        '(vector-slice (vector 1 2 3) -1)',
    ])
    def test_vector_slice_negative_index(self, menai, expression):
        """Test that vector-slice rejects negative indices."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)

    @pytest.mark.parametrize("expression", [
        '(vector-slice (vector 1 2 3) 4 5)',
        '(vector-slice (vector 1 2 3) 4)',
    ])
    def test_vector_slice_start_out_of_range(self, menai, expression):
        """Test that vector-slice rejects a start index beyond the vector length."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)

    @pytest.mark.parametrize("expression", [
        '(vector-slice (vector 1 2 3) 0 10)',
        '(vector-slice (vector) 0 1)',
    ])
    def test_vector_slice_end_out_of_range(self, menai, expression):
        """Test that vector-slice rejects an end index beyond the vector length."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)

    def test_vector_slice_start_after_end(self, menai):
        """Test that vector-slice rejects start greater than end."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice (vector 1 2 3) 2 1)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice (vector 1 2 3) 3 1)')

    def test_vector_slice_requires_vector(self, menai):
        """Test that vector-slice rejects non-vector first arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice (list 1 2 3) 0 2)')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice 42 0 2)')

    def test_vector_slice_requires_integer_indices(self, menai):
        """Test that vector-slice rejects non-integer indices."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice (vector 1 2 3) 0 "hello")')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-slice (vector 1 2 3) 0 2.5)')

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-concat (vector) (vector))', '#vector()'),
        ('(vector-concat (vector 1 2) (vector))', '#vector(1 2)'),
        ('(vector-concat (vector) (vector 1 2))', '#vector(1 2)'),
        ('(vector-concat (vector 1 2) (vector 3 4))', '#vector(1 2 3 4)'),
        ('(vector-concat (vector 1 "a") (vector #t 3.14))', '#vector(1 "a" #t 3.14)'),
    ])
    def test_vector_concat(self, menai, expression, expected):
        """Test vector concatenation."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_concat_requires_vectors(self, menai):
        """Test that vector-concat rejects non-vector arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-concat (vector 1) (list 2))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector-concat "ab" (vector 1))')

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-empty? (vector))', '#t'),
        ('(vector-empty? (vector 1))', '#f'),
        ('(vector-empty? (vector 1 2 3))', '#f'),
    ])
    def test_vector_empty(self, menai, expression, expected):
        """Test vector-empty?."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-member? (vector 1 2 3) 2)', '#t'),
        ('(vector-member? (vector 1 2 3) 4)', '#f'),
        ('(vector-member? (vector) 1)', '#f'),
        ('(vector-member? (vector "a" "b") "b")', '#t'),
    ])
    def test_vector_member(self, menai, expression, expected):
        """Test vector membership testing."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector-index (vector 1 2 3) 2)', '1'),
        ('(vector-index (vector 1 2 3 2) 2)', '1'),
        ('(vector-index (vector 1 2 3) 42)', '#none'),
        ('(vector-index (vector) 1)', '#none'),
    ])
    def test_vector_index(self, menai, expression, expected):
        """Test vector-index returns the first matching index or #none."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(vector->list (vector))', '()'),
        ('(vector->list (vector 1 2 3))', '(1 2 3)'),
        ('(vector->list (vector 1 "a" #t))', '(1 "a" #t)'),
        ('(list->vector (list))', '#vector()'),
        ('(list->vector (list 1 2 3))', '#vector(1 2 3)'),
        ('(list->vector (list 1 "a" #t))', '#vector(1 "a" #t)'),
    ])
    def test_vector_list_conversions(self, menai, expression, expected):
        """Test conversions between vectors and lists."""
        assert menai.evaluate_and_format(expression) == expected

    def test_vector_list_roundtrip(self, menai):
        """Test that vector→list→vector preserves the vector."""
        assert menai.evaluate_and_format('(list->vector (vector->list (vector 1 2 3)))') == '#vector(1 2 3)'

    def test_conversions_require_correct_types(self, menai):
        """Test that conversions reject wrong argument types."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate('(vector->list (list 1 2))')

        with pytest.raises(MenaiEvalError):
            menai.evaluate('(list->vector (vector 1 2))')

    @pytest.mark.parametrize("expression,expected", [
        ('(map-vector (lambda (x) (integer* x 2)) (vector 1 2 3))', '#vector(2 4 6)'),
        ('(map-vector (lambda (x) x) (vector))', '#vector()'),
    ])
    def test_map_vector(self, menai, expression, expected):
        """Test map-vector applies f to each element."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(filter-vector (lambda (x) (integer>? x 0)) (vector -1 2 -3 4))', '#vector(2 4)'),
        ('(filter-vector (lambda (x) #t) (vector))', '#vector()'),
    ])
    def test_filter_vector(self, menai, expression, expected):
        """Test filter-vector keeps elements satisfying pred."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(fold-vector integer+ 0 (vector 1 2 3 4))', '10'),
        ('(fold-vector integer+ 0 (vector))', '0'),
        ('(fold-vector (lambda (acc item) (vector-concat acc (vector item))) (vector) (vector 1 2))', '#vector(1 2)'),
    ])
    def test_fold_vector(self, menai, expression, expected):
        """Test fold-vector is a left fold with (acc item) accumulator signature."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(find-vector (lambda (x) (integer>? x 3)) (vector 1 2 3 4 5))', '4'),
        ('(find-vector (lambda (x) (integer>? x 9)) (vector 1 2 3))', '#none'),
        ('(find-vector (lambda (x) #t) (vector))', '#none'),
    ])
    def test_find_vector(self, menai, expression, expected):
        """Test find-vector returns the first satisfying element or #none."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(any-vector? (lambda (x) (integer>? x 3)) (vector 1 2 3 4 5))', '#t'),
        ('(any-vector? (lambda (x) (integer>? x 9)) (vector 1 2 3))', '#f'),
        ('(any-vector? (lambda (x) #t) (vector))', '#f'),
    ])
    def test_any_vector(self, menai, expression, expected):
        """Test any-vector?."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(all-vector? (lambda (x) (integer>? x 0)) (vector 1 2 3))', '#t'),
        ('(all-vector? (lambda (x) (integer>? x 1)) (vector 1 2 3))', '#f'),
        ('(all-vector? (lambda (x) #f) (vector))', '#t'),
    ])
    def test_all_vector(self, menai, expression, expected):
        """Test all-vector? is vacuously true on the empty vector."""
        assert menai.evaluate_and_format(expression) == expected

    @pytest.mark.parametrize("expression,expected", [
        ('(sort-vector integer<? (vector 3 1 4 1 5))', '#vector(1 1 3 4 5)'),
        ('(sort-vector integer<? (vector))', '#vector()'),
        ('(sort-vector integer>? (vector 3 1 4))', '#vector(4 3 1)'),
        ('(sort-vector string<? (vector "b" "a" "c"))', '#vector("a" "b" "c")'),
    ])
    def test_sort_vector(self, menai, expression, expected):
        """Test sort-vector returns a new sorted vector."""
        assert menai.evaluate_and_format(expression) == expected

    def test_sort_vector_original_unchanged(self, menai):
        """Test that sort-vector does not modify the original vector."""
        assert menai.evaluate_and_format(
            '(let ((v (vector 3 1 2)))'
            '  (let ((ignored (sort-vector integer<? v)))'
            '    v))'
        ) == '#vector(3 1 2)'

    def test_slice_reconstruct(self, menai):
        """Test that slicing apart and concatenating reconstructs the original."""
        expression = (
            '(vector-concat'
            '  (vector-slice (vector 1 2 3 4 5) 0 3)'
            '  (vector-slice (vector 1 2 3 4 5) 3))'
        )
        assert menai.evaluate_and_format(expression) == '#vector(1 2 3 4 5)'

    @pytest.mark.parametrize("expression", [
        '(vector-slice (vector 1 2 3))',
        '(vector-slice (vector 1 2 3) 1 2 3)',
    ])
    def test_vector_slice_arity(self, menai, expression):
        """Test that vector-slice requires 2 or 3 arguments."""
        with pytest.raises(MenaiEvalError):
            menai.evaluate(expression)
