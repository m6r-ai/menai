"""
End-to-end execution tests for MenaiCFGSchedule.

The pass reorders instructions within a basic block.  A reordering that is
wrong is not caught by inspecting the CFG: it shows up as a wrong answer, a
crash, or a hang when the emitted bytecode actually runs.  These tests therefore
compile and execute real programs and assert their results.

Each case is chosen because it exercises an ordering constraint the scheduler
has to respect:

  - a $letrec group, whose closures must all be patched before any is called,
  - a closure capturing a value defined in the same block,
  - a loop whose carried values feed the back-edge,
  - a variadic tail call, whose rest argument is packed into a fresh list,
  - a match fused into a jump table,
  - a loop with more than one back-edge.

Every case is also run with the pass disabled, and the two results must agree.
That is the strongest statement available: the pass may not change observable
behaviour, whatever the program does.
"""

import pytest

from menai.cfg.menai_cfg_schedule import MenaiCFGSchedule
from menai.menai import Menai
from menai.menai_compiler import MenaiCompiler


def _evaluate(source: str, with_pass: bool) -> object:
    """Evaluate `source`, optionally with the scheduling pass disabled."""
    if with_pass:
        return Menai().evaluate(source)

    original = MenaiCompiler.__init__

    def without_pass(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.cfg_passes = [
            candidate for candidate in self.cfg_passes
            if not isinstance(candidate, MenaiCFGSchedule)
        ]

    MenaiCompiler.__init__ = without_pass
    try:
        return Menai().evaluate(source)
    finally:
        MenaiCompiler.__init__ = original


def _assert_same_as_baseline(source: str, expected: object) -> None:
    """Assert the program gives `expected` with and without the pass."""
    without_pass = _evaluate(source, with_pass=False)
    assert without_pass == expected, (
        f"baseline (pass disabled) gave {without_pass!r}, expected {expected!r}"
    )

    with_pass = _evaluate(source, with_pass=True)
    assert with_pass == expected, (
        f"with the pass gave {with_pass!r}, expected {expected!r}"
    )


MUTUAL_RECURSION = """
(let ((even? (lambda (n) (if (integer=? n 0) #t (odd?  (integer- n 1)))))
      (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
  (list (even? 10) (odd? 11)))
"""

MUTUAL_RECURSION_CALLED_IMMEDIATELY = """
(let ((even? (lambda (n) (if (integer=? n 0) #t (odd?  (integer- n 1)))))
      (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
  (list (even? 4) (odd? 5)))
"""

THREE_WAY_MUTUAL_RECURSION = """
(let ((a (lambda (n) (if (integer=? n 0) 0 (b (integer- n 1)))))
      (b (lambda (n) (if (integer=? n 0) 1 (c (integer- n 1)))))
      (c (lambda (n) (if (integer=? n 0) 2 (a (integer- n 1))))))
  (list (a 3) (b 3) (c 3)))
"""

CLOSURE_CAPTURES_SIBLING = """
(let ((make (lambda (n)
              (let ((add (lambda (x) (integer+ x n)))
                    (mul (lambda (x) (integer* x n))))
                (list (add 1) (mul 2))))))
  (make 5))
"""

NESTED_CLOSURE_CAPTURES_OUTER = """
(let ((outer (lambda (a)
               (let ((inner (lambda (b) (integer+ a b))))
                 (inner 10)))))
  (outer 5))
"""

TAIL_RECURSIVE_LOOP = """
(let ((sum (lambda (lst acc)
             (if (list-null? lst)
                 acc
                 (sum (list-rest lst) (integer+ acc (list-first lst)))))))
  (sum (list 1 2 3 4 5) 0))
"""

VARIADIC_TAIL_CALL = """
(let ((sum-all (lambda (. rest) (fold-list integer+ 0 rest)))
      (first-of (lambda (a . rest) a)))
  (list (sum-all 1 2 3) (first-of 7 8 9)))
"""

MATCH_OVER_ENUM = """
(let ((state (enum (idle running stopped)))
      (describe (lambda (s)
                  (match s
                    ((: state 'idle) "idle")
                    ((: state 'running) "running")
                    ((: state 'stopped) "stopped")))))
  (list (describe (state 'idle)) (describe (state 'running)) (describe (state 'stopped))))
"""

MATCH_OVER_INTEGER = """
(let ((f (lambda (n) (match n (0 100) (1 200) (2 300) (_ 0)))))
  (list (f 0) (f 1) (f 2) (f 9)))
"""

LOOP_WITH_TWO_BACK_EDGES = """
(let ((f (lambda (n)
           (let ((loop (lambda (i acc)
                         (if (integer>=? i n)
                             acc
                             (if (integer=? (integer% i 2) 0)
                                 (loop (integer+ i 1) (integer+ acc 1))
                                 (loop (integer+ i 1) (integer+ acc 10)))))))
             (loop 0 0)))))
  (f 4))
"""

LOOP_CARRIED_VALUES = """
(let ((f (lambda (n)
           (let ((loop (lambda (i a b)
                         (if (integer>=? i n)
                             (list a b)
                             (loop (integer+ i 1)
                                   (integer+ a i)
                                   (integer* b 2))))))
             (loop 0 0 1)))))
  (f 4))
"""

LOOP_WITH_CALL_IN_BODY = """
(let ((f (lambda (v)
           (let ((loop (lambda (i acc)
                         (if (integer>=? i (vector-length v))
                             acc
                             (loop (integer+ i 1)
                                   (if (integer>? (vector-nth v i) 1)
                                       (integer+ acc 1)
                                       acc))))))
             (loop 0 0)))))
  (f (vector 1 5 2 7)))
"""

DEEPLY_NESTED_LETS = """
(let ((a 1))
  (let ((b (integer+ a 1)))
    (let ((c (integer+ b 1)))
      (let ((d (integer+ c 1)))
        (list a b c d)))))
"""

STRUCT_CONSTRUCTION_AND_MATCH = """
(let ((point (struct (x y)))
      (f (lambda (p) (match p ((: point x y) (integer+ x y))))))
  (f (point 3 4)))
"""

CHAIN_OF_SINGLE_USE_TEMPORARIES = """
(let ((f (lambda (a b c d)
           (integer+ (integer* a 1) (integer+ (integer* b 2) (integer+ (integer* c 3) (integer* d 4)))))))
  (f 1 2 3 4))
"""


class TestLetrecAndClosures:
    """A $letrec group's closures are all patched before any is called."""

    def test_mutual_recursion(self):
        """Mutual recursion produces the right answers."""
        _assert_same_as_baseline(MUTUAL_RECURSION, [True, True])

    def test_mutual_recursion_called_immediately(self):
        """A group called immediately after construction still works."""
        _assert_same_as_baseline(MUTUAL_RECURSION_CALLED_IMMEDIATELY, [True, True])

    def test_three_way_mutual_recursion(self):
        """A three-way mutually-recursive group works."""
        _assert_same_as_baseline(THREE_WAY_MUTUAL_RECURSION, [0, 1, 2])

    def test_closure_captures_sibling(self):
        """A closure capturing a sibling binding works."""
        _assert_same_as_baseline(CLOSURE_CAPTURES_SIBLING, [6, 10])

    def test_nested_closure_captures_outer(self):
        """A closure capturing an enclosing lambda's parameter works."""
        _assert_same_as_baseline(NESTED_CLOSURE_CAPTURES_OUTER, 15)


class TestLoops:
    """Loop-carried values and back-edges survive the reordering."""

    def test_tail_recursive_loop(self):
        """A tail-recursive accumulator loop sums correctly."""
        _assert_same_as_baseline(TAIL_RECURSIVE_LOOP, 15)

    def test_loop_with_two_back_edges(self):
        """A loop with a branch and two back-edges counts correctly."""
        _assert_same_as_baseline(LOOP_WITH_TWO_BACK_EDGES, 22)

    def test_loop_carried_values(self):
        """Two loop-carried values are updated correctly."""
        _assert_same_as_baseline(LOOP_CARRIED_VALUES, [6, 16])

    def test_loop_with_call_in_body(self):
        """A loop whose body calls a function counts correctly."""
        _assert_same_as_baseline(LOOP_WITH_CALL_IN_BODY, 3)

    def test_variadic_tail_call(self):
        """A variadic function packs its rest argument correctly."""
        _assert_same_as_baseline(VARIADIC_TAIL_CALL, [6, 7])


class TestDispatch:
    """Fused dispatch tables survive the reordering."""

    def test_match_over_enum(self):
        """A match over enum variants selects the right arm."""
        _assert_same_as_baseline(
            MATCH_OVER_ENUM, ["idle", "running", "stopped"],
        )

    def test_match_over_integer(self):
        """A match over integer literals selects the right arm."""
        _assert_same_as_baseline(MATCH_OVER_INTEGER, [100, 200, 300, 0])

    def test_struct_construction_and_match(self):
        """A struct is constructed and destructured correctly."""
        _assert_same_as_baseline(STRUCT_CONSTRUCTION_AND_MATCH, 7)


class TestStraightLine:
    """Straight-line blocks are reordered without changing behaviour."""

    def test_deeply_nested_lets(self):
        """A chain of dependent let bindings produces the right values."""
        _assert_same_as_baseline(DEEPLY_NESTED_LETS, [1, 2, 3, 4])

    def test_chain_of_single_use_temporaries(self):
        """A chain of single-use temporaries is evaluated correctly."""
        _assert_same_as_baseline(CHAIN_OF_SINGLE_USE_TEMPORARIES, 30)


@pytest.mark.parametrize(
    "source",
    [
        MUTUAL_RECURSION,
        MUTUAL_RECURSION_CALLED_IMMEDIATELY,
        THREE_WAY_MUTUAL_RECURSION,
        CLOSURE_CAPTURES_SIBLING,
        NESTED_CLOSURE_CAPTURES_OUTER,
        TAIL_RECURSIVE_LOOP,
        LOOP_WITH_TWO_BACK_EDGES,
        LOOP_CARRIED_VALUES,
        LOOP_WITH_CALL_IN_BODY,
        VARIADIC_TAIL_CALL,
        MATCH_OVER_ENUM,
        MATCH_OVER_INTEGER,
        STRUCT_CONSTRUCTION_AND_MATCH,
        DEEPLY_NESTED_LETS,
        CHAIN_OF_SINGLE_USE_TEMPORARIES,
    ],
    ids=[
        "mutual-recursion",
        "mutual-recursion-immediate",
        "three-way-mutual-recursion",
        "closure-captures-sibling",
        "nested-closure-captures-outer",
        "tail-recursive-loop",
        "loop-two-back-edges",
        "loop-carried-values",
        "loop-call-in-body",
        "variadic-tail-call",
        "match-enum",
        "match-integer",
        "struct-construct-match",
        "deeply-nested-lets",
        "chain-of-temporaries",
    ],
)
def test_pass_does_not_change_behaviour(source: str) -> None:
    """Every program gives the same result with and without the pass."""
    with_pass = _evaluate(source, with_pass=True)
    without_pass = _evaluate(source, with_pass=False)
    assert with_pass == without_pass
