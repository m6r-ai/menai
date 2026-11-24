"""
Python reference solver for the sudoku_mrv benchmark suite.

This reimplements sudoku-mrv.menai's search as an idiomatic Python solver: the
same most-constrained-cell ordering and nine-bit candidate masks, but mutating
the grid and the row/column/box masks in place and undoing on backtrack, which is
how a Python programmer would write it.  It exists so the benchmark can report
what the same amount of work costs in Python.

The two searches must do identical work for the comparison to mean anything, so
``prepare`` solves the puzzle once and checks the search node count against
MENAI_NODE_COUNTS, the figures the Menai opcode profile reports for the same
puzzles.  That check runs outside the timed loop.  If it fails, either the Menai
solver or this one has changed shape, and both this table and the recorded counts
need revisiting before any timing from the pair is worth reading.
"""

from __future__ import annotations

ALL_DIGITS = 0x1FF

# Search nodes (solve-from calls) the Menai solver executes, per puzzle.  These are
# the numbers the pair is pinned to, so a timing comparison is between equal work.
MENAI_NODE_COUNTS: dict[tuple[int, ...], int] = {
    # Easy (36 givens)
    (
        5, 3, 0, 0, 7, 0, 0, 0, 0,
        6, 0, 0, 1, 9, 5, 0, 0, 0,
        0, 9, 8, 0, 0, 0, 0, 6, 0,
        8, 0, 0, 0, 6, 0, 0, 0, 3,
        4, 0, 0, 8, 0, 3, 0, 0, 1,
        7, 0, 0, 0, 2, 0, 0, 0, 6,
        0, 6, 0, 0, 0, 0, 2, 8, 0,
        0, 0, 0, 4, 1, 9, 0, 0, 5,
        0, 0, 0, 0, 8, 0, 0, 7, 9,
    ): 52,
    # Medium (30 givens)
    (
        0, 0, 0, 2, 6, 0, 7, 0, 1,
        6, 8, 0, 0, 7, 0, 0, 9, 0,
        1, 9, 0, 0, 0, 4, 5, 0, 0,
        8, 2, 0, 1, 0, 0, 0, 4, 0,
        0, 0, 4, 6, 0, 2, 9, 0, 0,
        0, 5, 0, 0, 0, 3, 0, 2, 8,
        0, 0, 9, 3, 0, 0, 0, 7, 4,
        0, 4, 0, 0, 5, 0, 0, 3, 6,
        7, 0, 3, 0, 1, 8, 0, 0, 0,
    ): 46,
    # Hard (25 givens)
    (
        0, 0, 0, 6, 0, 0, 4, 0, 0,
        7, 0, 0, 0, 0, 3, 6, 0, 0,
        0, 0, 0, 0, 9, 1, 0, 8, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 5, 0, 1, 8, 0, 0, 0, 3,
        0, 0, 0, 3, 0, 6, 0, 4, 5,
        0, 4, 0, 2, 0, 0, 0, 6, 0,
        9, 0, 3, 0, 0, 0, 0, 0, 0,
        0, 2, 0, 0, 0, 0, 1, 0, 0,
    ): 6590,
    # Expert (23 givens)
    (
        8, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 3, 6, 0, 0, 0, 0, 0,
        0, 7, 0, 0, 9, 0, 2, 0, 0,
        0, 5, 0, 0, 0, 7, 0, 0, 0,
        0, 0, 0, 0, 4, 5, 7, 0, 0,
        0, 0, 0, 1, 0, 0, 0, 3, 0,
        0, 0, 1, 0, 0, 0, 0, 6, 8,
        0, 0, 8, 5, 0, 0, 0, 1, 0,
        0, 9, 0, 0, 0, 0, 4, 0, 0,
    ): 13811,
}


def _solve_from(grid: list[int], rows: list[int], cols: list[int], boxes: list[int], empties: list[int], nodes: list[int]) -> bool:
    """Solve in place from the given empty cells, returning True on success."""
    nodes[0] += 1
    if not empties:
        return True

    best_position = 0
    best_mask = 0
    best_count = 10
    for position, at in enumerate(empties):
        row = at // 9
        col = at % 9
        mask = ALL_DIGITS & ~(rows[row] | cols[col] | boxes[(row // 3) * 3 + col // 3])
        count = mask.bit_count()
        if count < best_count:
            best_position = position
            best_mask = mask
            best_count = count
            if count == 1:
                break

    if best_mask == 0:
        return False

    at = empties[best_position]
    row = at // 9
    col = at % 9
    box = (row // 3) * 3 + col // 3
    del empties[best_position]

    mask = best_mask
    while mask:
        bit = mask & -mask
        mask ^= bit
        grid[at] = bit.bit_length()
        rows[row] |= bit
        cols[col] |= bit
        boxes[box] |= bit
        if _solve_from(grid, rows, cols, boxes, empties, nodes):
            return True

        rows[row] ^= bit
        cols[col] ^= bit
        boxes[box] ^= bit
        grid[at] = 0

    empties.insert(best_position, at)
    return False


def _initial_state(flat: tuple[int, ...]) -> tuple[list[int], list[int], list[int], list[int], list[int]]:
    """Return the grid, row/column/box masks, and empty-cell list for a puzzle."""
    grid = list(flat)
    rows = [0] * 9
    cols = [0] * 9
    boxes = [0] * 9
    empties: list[int] = []

    for at in range(81):
        value = grid[at]
        if value == 0:
            empties.append(at)

        else:
            bit = 1 << (value - 1)
            rows[at // 9] |= bit
            cols[at % 9] |= bit
            boxes[(at // 9 // 3) * 3 + at % 9 // 3] |= bit

    return grid, rows, cols, boxes, empties


def solve(flat: tuple[int, ...]) -> list[int]:
    """Solve a flat 81-element puzzle, returning the completed grid."""
    grid, rows, cols, boxes, empties = _initial_state(flat)
    return grid if _solve_from(grid, rows, cols, boxes, empties, [0]) else []


def count_nodes(flat: tuple[int, ...]) -> int:
    """Return the number of search nodes the solver visits for a puzzle."""
    grid, rows, cols, boxes, empties = _initial_state(flat)
    nodes = [0]
    _solve_from(grid, rows, cols, boxes, empties, nodes)
    return nodes[0]



def prepare(flat: list[int]) -> tuple[int, ...]:
    """
    Return the puzzle as a tuple, having checked this solver matches the Menai node count.

    This runs outside the timed loop.  A count mismatch means the two solvers are
    no longer doing the same work, which makes the timings incomparable, so it is
    an error rather than a warning.
    """
    puzzle = tuple(flat)
    expected = MENAI_NODE_COUNTS.get(puzzle)
    if expected is None:
        raise KeyError(
            "no recorded Menai node count for this puzzle; add it to "
            "MENAI_NODE_COUNTS before benchmarking the pair",
        )

    actual = count_nodes(puzzle)
    if actual != expected:
        raise ValueError(
            f"reference solver visits {actual} nodes but the Menai solver visits "
            f"{expected}; the two searches no longer do the same work",
        )

    return puzzle
