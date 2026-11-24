"""
Python reference implementation of the rubiks-cube-vector Menai module.

This mirrors src/menai_benchmark/suites/rubiks_vector/rubiks-cube-vector.menai, an
IDA* Rubik's cube solver, so that the two can be timed as the same work. It is
written the way a Python programmer would write it -- tuples of tuples for the
cube, integer face and turn indices instead of enums and structs, and a list
comprehension for the candidate list -- but every algorithmic choice matches the
Menai module:

- the same face order (U D F B L R) and the same six move permutations, transcribed
  index for index;
- turns applied by repeating the clockwise rotation (once, three times, twice),
  not by separate counter-clockwise tables;
- the same heuristic: the count of stickers not matching their face's centre,
  divided by 8, with the solved test sharing that one count;
- the same IDA* loop, including the bound increasing to ``bound + 1`` when no child
  reports a threshold, and the same ``max-depth`` cut-off;
- the same move order (face-major, then cw, ccw, half) and the same pruning rule
  (a move may not share a face with the preceding move).

``solve`` therefore explores exactly the same tree as the Menai solver. That is
asserted in ``prepare`` against the node counts the Menai opcode profile reports
for each benchmark scramble, so a drift between the two searches fails the run
rather than quietly producing a meaningless ratio.

The one place the representation differs is that a cube is a tuple of six
nine-element tuples rather than a struct of six vectors. That is the natural
Python spelling of the same fixed-size aggregate, and it keeps the per-move work
comparable: each move still constructs six new faces from individual sticker
reads.
"""

from __future__ import annotations

# Face indices, in the module's field order: U D F B L R.
U, D, F, B, L, R = 0, 1, 2, 3, 4, 5

# Turn indices, in the module's enum order.
CW, CCW, HALF = 0, 1, 2

FACE_NAMES = ("u", "d", "f", "b", "l", "r")

TURN_SUFFIXES = ("", "'", "2")

MAX_DEPTH = 20

Cube = tuple[tuple[int, ...], ...]

Move = tuple[int, int]

Path = tuple[Move, ...]

# A search node's outcome: the solution path when found, otherwise the threshold
# the next iteration should start from.
Outcome = int | Path

MAX_DEPTH_EXCEEDED = "Max depth exceeded"

# Every move, in face-major order (all three turns of U, then D, and so on).
ALL_MOVES: tuple[Move, ...] = tuple((face, turn) for face in range(6) for turn in range(3))

# Search nodes (ida-search calls) the Menai solver executes, per benchmark case.
# These are the numbers the pair is pinned to, so a timing comparison is between
# equal work.  The keys are the scramble sequences the suite defines.
MENAI_NODE_COUNTS: dict[tuple[str, ...], int] = {
    ("R",): 19,
    ("R", "U"): 18,
    ("R", "U", "F"): 41,
    ("R", "U", "R'", "D"): 445,
    ("R", "U", "R'", "D", "F"): 1492,
    ("R", "U", "R'", "D", "F", "R"): 9594,
    ("R", "U", "R'", "D", "F", "R", "U"): 21357,
    ("R", "U", "B", "D", "F", "R", "U"): 2733,
}


def solved_cube() -> Cube:
    """Return a solved cube: every face filled with its own sticker value."""
    return (
        (0,) * 9,
        (1,) * 9,
        (2,) * 9,
        (3,) * 9,
        (4,) * 9,
        (5,) * 9,
    )


def rotate_face_cw(face: tuple[int, ...]) -> tuple[int, ...]:
    """Return a face rotated a quarter turn clockwise."""
    return (
        face[6], face[3], face[0],
        face[7], face[4], face[1],
        face[8], face[5], face[2],
    )


def move_u(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the U face."""
    u, d, f, b, l, r = cube
    return (
        rotate_face_cw(u),
        d,
        (r[0], r[1], r[2], f[3], f[4], f[5], f[6], f[7], f[8]),
        (l[0], l[1], l[2], b[3], b[4], b[5], b[6], b[7], b[8]),
        (f[0], f[1], f[2], l[3], l[4], l[5], l[6], l[7], l[8]),
        (b[0], b[1], b[2], r[3], r[4], r[5], r[6], r[7], r[8]),
    )


def move_d(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the D face."""
    u, d, f, b, l, r = cube
    return (
        u,
        rotate_face_cw(d),
        (f[0], f[1], f[2], f[3], f[4], f[5], l[6], l[7], l[8]),
        (b[0], b[1], b[2], b[3], b[4], b[5], r[6], r[7], r[8]),
        (l[0], l[1], l[2], l[3], l[4], l[5], b[6], b[7], b[8]),
        (r[0], r[1], r[2], r[3], r[4], r[5], f[6], f[7], f[8]),
    )


def move_f(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the F face."""
    u, d, f, b, l, r = cube
    return (
        (u[0], u[1], u[2], u[3], u[4], u[5], l[8], l[5], l[2]),
        (r[0], r[3], r[6], d[3], d[4], d[5], d[6], d[7], d[8]),
        rotate_face_cw(f),
        b,
        (l[0], l[1], d[2], l[3], l[4], d[1], l[6], l[7], d[0]),
        (u[6], r[1], r[2], u[7], r[4], r[5], u[8], r[7], r[8]),
    )


def move_b(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the B face."""
    u, d, f, b, l, r = cube
    return (
        (r[2], r[5], r[8], u[3], u[4], u[5], u[6], u[7], u[8]),
        (d[0], d[1], d[2], d[3], d[4], d[5], l[6], l[3], l[0]),
        f,
        rotate_face_cw(b),
        (u[2], l[1], l[2], u[1], l[4], l[5], u[0], l[7], l[8]),
        (r[0], r[1], d[6], r[3], r[4], d[7], r[6], r[7], d[8]),
    )


def move_l(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the L face."""
    u, d, f, b, l, r = cube
    return (
        (b[8], u[1], u[2], b[5], u[4], u[5], b[2], u[7], u[8]),
        (f[0], d[1], d[2], f[3], d[4], d[5], f[6], d[7], d[8]),
        (u[0], f[1], f[2], u[3], f[4], f[5], u[6], f[7], f[8]),
        (b[0], b[1], d[6], b[3], b[4], d[3], b[6], b[7], d[0]),
        rotate_face_cw(l),
        r,
    )


def move_r(cube: Cube) -> Cube:
    """Return the cube turned one quarter turn clockwise on the R face."""
    u, d, f, b, l, r = cube
    return (
        (u[0], u[1], f[2], u[3], u[4], f[5], u[6], u[7], f[8]),
        (d[0], d[1], b[6], d[3], d[4], b[3], d[6], d[7], b[0]),
        (f[0], f[1], d[2], f[3], f[4], d[5], f[6], f[7], d[8]),
        (u[8], b[1], b[2], u[5], b[4], b[5], u[2], b[7], b[8]),
        l,
        rotate_face_cw(r),
    )


FACE_ROTATIONS = (move_u, move_d, move_f, move_b, move_l, move_r)

TURN_REPEATS = (1, 3, 2)


def apply_move(cube: Cube, move: Move) -> Cube:
    """Return the cube with the move applied, repeating the face rotation as the turn requires."""
    rotate = FACE_ROTATIONS[move[0]]
    for _ in range(TURN_REPEATS[move[1]]):
        cube = rotate(cube)

    return cube


def apply_moves(cube: Cube, moves: tuple[Move, ...]) -> Cube:
    """Return the cube with every move in moves applied in order."""
    for move in moves:
        cube = apply_move(cube, move)

    return cube


def count_mismatched(face: tuple[int, ...]) -> int:
    """Return how many stickers on a face differ from that face's centre sticker."""
    centre = face[4]
    return sum(1 for sticker in face if sticker != centre)


def mismatches(cube: Cube) -> int:
    """Return the number of stickers on the cube that do not match their face's centre."""
    return sum(count_mismatched(face) for face in cube)


def heuristic(cube: Cube) -> int:
    """Return the IDA* heuristic: mismatched stickers divided by eight."""
    return mismatches(cube) // 8


def cube_solved(cube: Cube) -> bool:
    """Return True if every sticker matches its face's centre."""
    return mismatches(cube) == 0


def get_candidates(last_move: Move | None) -> list[Move]:
    """Return the moves that may follow last_move, in move order."""
    if last_move is None:
        return list(ALL_MOVES)

    return [move for move in ALL_MOVES if move[0] != last_move[0]]


def check_move_permutations() -> None:
    """
    Check that every face turn is a permutation of the stickers with order 4.

    A quarter turn of a cube rearranges the 54 stickers and returns them after four
    applications, so any move that moves a sticker twice onto one slot, or needs
    more than four turns to come back, is wrong. This is checked against a cube
    whose stickers are all distinct, because a cube of six uniform faces cannot tell
    a correct permutation from one that merely keeps each face single-coloured.

    The move tables are transcribed by hand, so this is the cheap structural check
    that catches a transcription slip in either implementation.
    """
    labelled: Cube = tuple(tuple(range(face * 9, face * 9 + 9)) for face in range(6))
    order = list(range(54))
    for face, rotate in enumerate(FACE_ROTATIONS):
        moved = [sticker for one in rotate(labelled) for sticker in one]
        if sorted(moved) != order:
            raise ValueError(f"face turn {FACE_NAMES[face]} does not permute the stickers")

        cube = labelled
        for _ in range(4):
            cube = rotate(cube)

        if cube != labelled:
            raise ValueError(f"face turn {FACE_NAMES[face]} does not have order 4")


def search_moves(
    cube: Cube,
    bound: int,
    g: int,
    path: Path,
    moves: list[Move],
    best_threshold: int | None,
    nodes: list[int],
) -> tuple[bool, Outcome]:
    """Try each remaining candidate move, returning (found, path or next threshold)."""
    if not moves:
        return False, bound + 1 if best_threshold is None else best_threshold

    move = moves[0]
    found, value = ida_search(apply_move(cube, move), bound, g + 1, move, path + (move,), nodes)
    if found:
        return True, value

    if not isinstance(value, int):
        raise TypeError("a failed search must report an integer threshold")

    if best_threshold is None:
        next_threshold = value

    else:
        next_threshold = min(best_threshold, value)

    return search_moves(cube, bound, g, path, moves[1:], next_threshold, nodes)


def ida_search(cube: Cube, bound: int, g: int, last_move: Move | None, path: Path, nodes: list[int]) -> tuple[bool, Outcome]:
    """Explore one node of the IDA* search."""
    nodes[0] += 1
    mismatched = mismatches(cube)
    if mismatched == 0:
        return True, path

    h = mismatched // 8
    if g + h > bound:
        return False, g + h

    return search_moves(cube, bound, g, path, get_candidates(last_move), None, nodes)


def ida_star(cube: Cube, max_depth: int, nodes: list[int]) -> tuple[bool, Outcome | str]:
    """Search for the solution, returning (found, path) or (False, "Max depth exceeded")."""
    bound = 0
    while True:
        if bound > max_depth:
            return False, MAX_DEPTH_EXCEEDED

        found, value = ida_search(cube, bound, 0, None, (), nodes)
        if found:
            return True, value

        if not isinstance(value, int):
            raise TypeError("a failed iteration must report an integer threshold")

        bound = value


def parse_move(text: str) -> Move:
    """Return the (face, turn) pair for a standard-notation move such as "R'"."""
    face = FACE_NAMES.index(text[0].lower())
    turn = {"": CW, "'": CCW, "2": HALF}[text[1:]]
    return face, turn


def move_to_string(move: Move) -> str:
    """Render a move in standard notation: "U", "U'" or "U2"."""
    return FACE_NAMES[move[0]].upper() + TURN_SUFFIXES[move[1]]


def scramble_moves(scramble: tuple[str, ...]) -> tuple[Move, ...]:
    """Return the move values for a scramble sequence."""
    return tuple(parse_move(text) for text in scramble)


def solve(scramble: tuple[str, ...]) -> list[str]:
    """Scramble a solved cube and solve it, returning the solution in standard notation."""
    scrambled = apply_moves(solved_cube(), scramble_moves(scramble))
    found, value = ida_star(scrambled, MAX_DEPTH, [0])
    if not found or not isinstance(value, tuple):
        return []

    return [move_to_string(move) for move in value]


def prepare(scramble: list[str]) -> tuple[str, ...]:
    """
    Return the scramble as a tuple, having checked this solver visits the Menai node count.

    This runs outside the timed loop.  A count mismatch means the two solvers no
    longer explore the same tree, which makes the timings incomparable, so it is an
    error rather than a warning.
    """
    check_move_permutations()

    key = tuple(scramble)
    expected = MENAI_NODE_COUNTS.get(key)
    if expected is None:
        raise KeyError(
            "no recorded Menai node count for this scramble; add it to "
            "MENAI_NODE_COUNTS before benchmarking the pair",
        )

    nodes = [0]
    found, _ = ida_star(apply_moves(solved_cube(), scramble_moves(key)), MAX_DEPTH, nodes)
    if not found:
        raise ValueError(f"reference solver found no solution for scramble {key}")

    if nodes[0] != expected:
        raise ValueError(
            f"reference solver visits {nodes[0]} nodes but the Menai solver visits "
            f"{expected}; the two searches no longer explore the same tree",
        )

    return key
