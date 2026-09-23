"""
Deterministic traversal of a CodeObject tree.

This module defines the canonical ordering of code objects and instructions
within a compiled program.  The ordering is the contract shared between the
Python-side renderers and the C VM's instruction tracer: the C VM assigns the
same ordinals during conversion so that execution counts collected in C can be
attributed back to individual instructions by the Python renderers.

The ordering is:

  * Code objects are visited depth-first, root first, and within each code
    object its children are visited in list order (``code_objects[0]`` first).
  * Each code object is assigned a code ordinal (0-based) in visit order.
  * Each instruction is assigned a global instruction ordinal (0-based)
    in visit order: all of the root's instructions come first, then all of
    its first child's instructions, and so on.

Because the traversal depends only on the tree structure, it is stable across
repeated conversions of the same CodeObject, which is what makes the trace
buffer meaningful across multiple ``execute()`` calls.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from menai.bytecode.menai_bytecode import CodeObject


@dataclass(frozen=True, slots=True)
class CodeObjectVisit:
    """
    One code object encountered during a tree walk.

    path            — child indices from the root to this code object, so the
                      root has path ``()`` and its second child ``(1,)``.
    code_ordinal    — 0-based position of this code object in visit order.
    instr_base      — global instruction ordinal of this code object's
                      instruction 0.
    instruction_count — number of instructions in this code object.
    code            — the code object itself.
    """

    path: tuple[int, ...]
    code_ordinal: int
    instr_base: int
    instruction_count: int
    code: CodeObject


def walk_code_objects(root: CodeObject) -> Iterator[CodeObjectVisit]:
    """
    Yield every code object in the tree in canonical visit order.

    See the module docstring for the definition of the ordering.  The walk is
    depth-first: a code object is yielded before its children, and its children
    are yielded before any sibling's children.
    """
    stack: list[tuple[tuple[int, ...], CodeObject]] = [((), root)]
    code_ordinal = 0
    instr_base = 0

    # Depth-first, pre-order.  Children are pushed in reverse so that they pop
    # in list order.
    while stack:
        path, code = stack.pop()
        count = len(code.instructions)

        yield CodeObjectVisit(
            path=path,
            code_ordinal=code_ordinal,
            instr_base=instr_base,
            instruction_count=count,
            code=code,
        )

        code_ordinal += 1
        instr_base += count

        for child_index in range(len(code.code_objects) - 1, -1, -1):
            stack.append((path + (child_index,), code.code_objects[child_index]))


def count_instructions(root: CodeObject) -> int:
    """Return the total number of instructions in the tree."""
    return sum(len(code.instructions) for _, code in _iter_tree(root))


def count_code_objects(root: CodeObject) -> int:
    """Return the total number of code objects in the tree."""
    return sum(1 for _ in _iter_tree(root))


def _iter_tree(root: CodeObject) -> Iterator[tuple[tuple[int, ...], CodeObject]]:
    """Yield (path, code) pairs in canonical visit order."""
    for visit in walk_code_objects(root):
        yield visit.path, visit.code
