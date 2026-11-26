"""
Tests for summing VM trace arrays across repeated executions.

The VM reports counts for one ``execute`` call, so a caller that runs the same
compiled program many times sums the arrays itself.  Two properties matter: the
sums are the element-wise total of every run, and a trace is only combinable
with one taken against the same code tree, because otherwise the ordinals number
different instructions and every count would be misattributed.
"""

import pytest

from menai import Menai
from menai_trace.menai_trace_data import TraceAccumulator, resolve_trace

_SOURCE = """
(let ((f (lambda (x) (if (integer>? x 0) 1 2))))
  (apply f (list 5)))
"""

_OTHER_SOURCE = """
(let ((g (lambda (x) (integer+ x 1)))
      (h (lambda (y) (integer* y 2))))
  (list g h))
"""


def _run(menai: Menai, code: object) -> tuple[list[int], list[int]]:
    """Execute a compiled program with tracing on and return its raw counts."""
    menai.execute_raw(code)
    return menai.vm.get_trace_data()


def _accumulate(source: str, runs: int) -> TraceAccumulator:
    """Compile source, execute it `runs` times, and return the accumulated trace."""
    menai = Menai()
    code = menai.compile(source)
    menai.vm.enable_profiling()

    accumulator = TraceAccumulator()
    accumulator.begin(code)
    for _ in range(runs):
        accumulator.add(*_run(menai, code))

    return accumulator


class TestAccumulation:
    """The totals are the element-wise sum of every execution's arrays."""

    def test_total_is_the_sum_over_every_run(self):
        menai = Menai()
        code = menai.compile(_SOURCE)
        menai.vm.enable_profiling()

        accumulator = TraceAccumulator()
        accumulator.begin(code)
        first = _run(menai, code)
        accumulator.add(*first)
        single = accumulator.result().total_instructions()

        accumulator.add(*_run(menai, code))
        doubled = accumulator.result().total_instructions()

        assert doubled == single * 2

    def test_call_counts_are_summed_too(self):
        accumulator = _accumulate(_SOURCE, 3)
        result = accumulator.result()

        root_calls = result.functions[0].calls
        assert root_calls == 3

    def test_result_resolves_to_the_bound_tree(self):
        accumulator = _accumulate(_SOURCE, 1)
        result = accumulator.result()

        assert len(result.functions) > 0
        assert result.total_instructions() > 0

    def test_accumulated_total_matches_a_direct_resolution(self):
        menai = Menai()
        code = menai.compile(_SOURCE)
        menai.vm.enable_profiling()

        accumulator = TraceAccumulator()
        accumulator.begin(code)
        counts = [_run(menai, code) for _ in range(2)]
        for instr_counts, call_counts in counts:
            accumulator.add(instr_counts, call_counts)

        expected = sum(sum(instr) for instr, _ in counts)
        assert accumulator.result().total_instructions() == expected


class TestRefusals:
    """A trace cannot be combined with one from a different code tree."""

    def test_adding_before_begin_is_an_error(self):
        accumulator = TraceAccumulator()

        with pytest.raises(ValueError, match="call begin"):
            accumulator.add([1, 2], [3])

    def test_result_before_any_execution_is_an_error(self):
        menai = Menai()
        code = menai.compile(_SOURCE)

        accumulator = TraceAccumulator()
        accumulator.begin(code)

        with pytest.raises(ValueError, match="no trace data"):
            accumulator.result()

    def test_adding_a_different_trees_trace_is_an_error(self):
        menai = Menai()
        first = menai.compile(_SOURCE)
        second = menai.compile(_OTHER_SOURCE)
        menai.vm.enable_profiling()

        accumulator = TraceAccumulator()
        accumulator.begin(first)
        accumulator.add(*_run(menai, first))

        other_counts = _run(menai, second)
        if len(other_counts[0]) == len(accumulator.result().functions):
            pytest.skip("the two programs compiled to the same trace shape")

        with pytest.raises(ValueError, match="shape changed"):
            accumulator.add(*other_counts)

    def test_begin_clears_previous_counts(self):
        accumulator = _accumulate(_SOURCE, 2)
        menai = Menai()
        code = menai.compile(_SOURCE)
        accumulator.begin(code)

        with pytest.raises(ValueError, match="no trace data"):
            accumulator.result()


class TestResolution:
    """The accumulated totals are resolved against the same walk as a single run."""

    def test_matches_resolve_trace_on_the_same_counts(self):
        menai = Menai()
        code = menai.compile(_SOURCE)
        menai.vm.enable_profiling()

        accumulator = TraceAccumulator()
        accumulator.begin(code)
        instr_counts, call_counts = _run(menai, code)
        accumulator.add(instr_counts, call_counts)

        direct = resolve_trace(code, instr_counts, call_counts)
        accumulated = accumulator.result()

        assert accumulated.total_instructions() == direct.total_instructions()
        assert len(accumulated.functions) == len(direct.functions)
