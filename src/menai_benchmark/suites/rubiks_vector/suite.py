from pathlib import Path

from menai_benchmark import BenchmarkCase, BenchmarkSuite, MenaiProgram, ReferenceImplementation
from menai_benchmark.suites.rubiks_list.suite import _SCRAMBLES, move_to_menai
from menai_benchmark.suites.rubiks_vector import reference

_SUITE_DIR = Path(__file__).resolve().parent

# The scramble sequences are shared with the list-based rubiks_list suite so the
# two suites measure exactly the same work: only the face representation
# differs.


def _expr(scramble_moves: list[str]) -> str:
    """Build the solver-driving expression for a scramble sequence."""
    moves_literal = "(list " + " ".join(move_to_menai(m) for m in scramble_moves) + ")"
    return (
        '(let ((rubiks (import "rubiks-cube-vector")))'
        '  (let ((move (:: rubiks move))'
        '        (face (:: rubiks face))'
        '        (turn (:: rubiks turn))'
        '        (solved-cube-fn (:: rubiks solved-cube))'
        '        (apply-moves-fn (:: rubiks apply-moves))'
        '        (ida-star-fn (:: rubiks ida-star)))'
        f'    (let ((scrambled (apply-moves-fn (solved-cube-fn) {moves_literal})))'
        '      (ida-star-fn scrambled 20))))'
    )


class Suite(BenchmarkSuite):
    """Benchmark suite for a Rubik's cube IDA* solver whose faces are vectors."""

    name = "rubiks_vector"
    description = "Solve scrambled Rubik's cubes of increasing depth using IDA* on a vector cube."

    def cases(self) -> list[BenchmarkCase]:
        """Return one case per scramble sequence."""
        return [
            BenchmarkCase(name=name, input=moves, iterations=3)
            for name, moves in _SCRAMBLES
        ]

    def menai_program(self) -> MenaiProgram:
        """Return the solver expression, built from the scramble sequence."""
        return MenaiProgram(expression=_expr)

    def reference_implementations(self) -> list[ReferenceImplementation]:
        """
        Return a pure-Python reimplementation of the same solver.

        It mirrors rubiks-cube-vector.menai step for step -- the same face order and
        move permutations, the same repeat-the-clockwise-turn handling, the same
        heuristic and solved test sharing one mismatch count, the same IDA* loop and
        the same move pruning and ordering -- so the ratio is the language's cost for
        identical work.  Its preparation step asserts that it visits the same number
        of search nodes as the Menai solver for each scramble, and the structural check
        it also runs rejects a move table that is not a genuine quarter turn.

        An in-place variant (flat bytearray, mutated and undone, with precomputed cycle
        rotations) was written and measured, and it was 1.7x slower than this one, not
        faster: the mirror's per-move cost is one tuple construction of nine loads,
        which beats walking a cycle with bytecode-level index writes. So immutability
        costs Python nothing here, and there is no second column to report.

        That result says nothing about Menai, which pays for its own representation
        rather than Python's. Nothing here should be read as "Menai cannot mutate":
        the VM updates a collection in place when the update cannot be observed, and
        whether this module's struct-of-six-vectors shape qualifies is a separate
        question from which Python spelling is faster.
        """
        return [
            ReferenceImplementation(
                name="python",
                prepare=reference.prepare,
                run=reference.solve,
            ),
        ]
