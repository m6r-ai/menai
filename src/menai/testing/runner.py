"""
Library form of the Menai test runner.

A `*.test.menai` suite is an ordinary Menai module exporting a binding named
`tests`.  That binding is a node-list: a list of `(name thing)` nodes where
`thing` is a test thunk, a dict leaf, or a nested node-list.  This module
loads such a suite, locates each leaf by its path, and executes each leaf on a
fresh VM invocation so that a failure in one leaf does not affect another.

The API is deliberately host-facing and free of printing, argument parsing and
reporting: `run_test_suite` returns a list of `TestOutcome` values that a tool
renders however it likes.  It has no dependency on the `menai_test` console
tool or on the coverage support, so it can be embedded anywhere the `menai`
package is available.
"""

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from menai.bytecode.menai_bytecode import CodeObject
from menai.menai import Menai
from menai.menai_error import MenaiError
from menai.menai_value import (
    MenaiBoolean, MenaiDict, MenaiFunction, MenaiList, MenaiString, MenaiValue,
)

from menai.testing.support import support_module_dir

# Name of the binding carrying a leaf path into the compiled leaf program.  It
# must not start with '$', be a reserved word, or collide with a prelude name.
_PATH_BINDING = "__path__"

# A fixed placeholder keeps the injected path distinguishable in the constant
# pool when the module under test contributes its own list constants.
_PLACEHOLDER_PATH = ["__placeholder__"]


@dataclass(frozen=True)
class TestOutcome:
    """
    The result of running one leaf.

    Attributes:
        path: Full path to the leaf, including its branch names
        passed: True when the leaf behaved as expected
        error: Why the leaf failed, or None when it passed
    """
    path: list[str]
    passed: bool
    error: str | None = None


@dataclass
class _Node:
    """A node in a parsed test node-list."""

    path: list[str]
    children: list["_Node"] = field(default_factory=list)
    thunk_path: list[str] | None = None
    expect_error: bool = False
    expect_error_contains: str | None = None

    def is_leaf(self) -> bool:
        """Return True when this node is a leaf rather than a branch."""
        return self.thunk_path is not None


def run_test_suite(test_file: str | Path, module_path: list[str] | None = None) -> list[TestOutcome]:
    """
    Run every leaf in a single `*.test.menai` suite.

    Each leaf runs on a fresh VM invocation, sharing one compiled leaf program
    whose injected path is rebound before each execution.  A runtime error (an
    assertion failure, a type error, an explicit `error`) ends only the leaf
    that raised it; it is reported in that leaf's outcome.

    Args:
        test_file: Path to the `.test.menai` file to run
        module_path: Directories to search for Menai modules, ahead of the
            suite's own directory and the standard library.  The test support
            module is prepended automatically.

    Returns:
        One TestOutcome per leaf, in node-list order.

    Raises:
        ValueError: If the suite's structure is not a valid node-list or if the
            test support module has no filesystem location.
        MenaiError: If the suite or a module it imports fails to compile.
    """
    path = Path(test_file)
    module_name = path.stem

    search_path = list(module_path or []) + Menai.build_module_path(str(path.parent))
    menai = Menai(_module_path_with_support(search_path))

    resolved = Path(menai.resolve_module(module_name)).resolve()
    if resolved != path.resolve():
        raise ValueError(
            f"The suite '{module_name}' resolves to {resolved}, not {path}. "
            "Rename the suite or put its directory ahead of the shadowing one in module_path."
        )

    nodes = _load_suite(menai, module_name)
    code, path_index = _compile_leaf_program(menai, module_name)

    outcomes: list[TestOutcome] = []
    _run_nodes(nodes, menai, code, path_index, outcomes)
    return outcomes


def _module_path_with_support(module_path: list[str]) -> list[str]:
    """
    Prepend the test support directory to a module search path.

    Args:
        module_path: The caller's module search path

    Returns:
        A new path with the support directory first and duplicates removed

    Raises:
        ValueError: If the support module has no filesystem location
    """
    support_dir = support_module_dir()
    if support_dir is None:
        raise ValueError(
            "The menai-test support module has no filesystem location in this installation"
        )

    directories: list[str] = []
    for directory in [str(support_dir)] + list(module_path):
        if directory not in directories:
            directories.append(directory)

    return directories


def _load_suite(menai: Menai, module_name: str) -> list[_Node]:
    """
    Evaluate a test module and parse its exported node-list.

    Args:
        menai: Instance with the support module on its path
        module_name: Module name of the suite (the test file's stem)

    Returns:
        The parsed top-level nodes

    Raises:
        ValueError: If the module does not export a valid node-list
    """
    result = menai.evaluate_raw(f'(let ((m (import "{module_name}"))) (:: m tests))')

    if not isinstance(result, MenaiList):
        raise ValueError(
            f"The test module must export a 'tests' list of nodes, got: {result.type_name()}"
        )

    return _parse_nodes(result, [])


def _parse_nodes(value: MenaiList, path: list[str]) -> list[_Node]:
    """
    Recursively parse a node-list into nodes.

    Args:
        value: The node-list to parse
        path: Path of the enclosing branch

    Returns:
        The parsed nodes

    Raises:
        ValueError: If the structure is not a node-list
    """
    nodes: list[_Node] = []

    for element in value.elements:
        if not isinstance(element, MenaiList) or len(element.elements) != 2:
            raise ValueError(
                f"Each node must be a 2-element list (name, thing), got: {element.describe()}"
            )

        name_value, thing = element.elements
        if not isinstance(name_value, MenaiString):
            raise ValueError(f"Node name must be a string, got: {name_value.describe()}")

        node_path = path + [name_value.value]

        if isinstance(thing, MenaiFunction):
            nodes.append(_Node(path=node_path, thunk_path=node_path))

        elif isinstance(thing, MenaiDict):
            nodes.append(_parse_dict_leaf(node_path, name_value.value, thing))

        elif isinstance(thing, MenaiList):
            nodes.append(_Node(path=node_path, children=_parse_nodes(thing, node_path)))

        else:
            raise ValueError(
                f"Node '{name_value.value}': second element must be a function, dict or list, "
                f"got: {thing.type_name()}"
            )

    return nodes


def _parse_dict_leaf(node_path: list[str], name: str, value: MenaiDict) -> _Node:
    """
    Parse a leaf from its dict representation.

    Args:
        node_path: Full path to the node
        name: Node name, for error messages
        value: The leaf dict

    Returns:
        The parsed leaf node

    Raises:
        ValueError: If the dict does not describe a valid leaf
    """
    thunk = _lookup(value, "thunk")
    if not isinstance(thunk, MenaiFunction):
        raise ValueError(f"Node '{name}': leaf dict requires a 'thunk' function")

    expect_error = _lookup(value, "expect-error")
    if expect_error is None:
        expect_error = MenaiBoolean(False)

    if not isinstance(expect_error, MenaiBoolean):
        raise ValueError(f"Node '{name}': 'expect-error' must be a boolean")

    contains = _lookup(value, "expect-error-contains")
    if contains is not None and not isinstance(contains, MenaiString):
        raise ValueError(f"Node '{name}': 'expect-error-contains' must be a string")

    return _Node(
        path=node_path,
        thunk_path=node_path,
        expect_error=expect_error.value,
        expect_error_contains=contains.value if isinstance(contains, MenaiString) else None,
    )


def _lookup(value: MenaiDict, key: str) -> MenaiValue | None:
    """
    Look up a key in a leaf dict.

    Args:
        value: The dict to search
        key: The key to find

    Returns:
        The value, or None when the key is absent
    """
    entry = value.lookup.get(MenaiDict.to_hashable_key(MenaiString(key)))
    return entry[1] if entry is not None else None


def _path_value(path: list[str]) -> MenaiList:
    """
    Build the Menai list value for a leaf path.

    Args:
        path: The path segments

    Returns:
        A Menai list of strings
    """
    return MenaiList(elements=tuple(MenaiString(value=segment) for segment in path))


def _compile_leaf_program(menai: Menai, module_name: str) -> tuple[CodeObject, int]:
    """
    Compile the leaf program once, with the path read from an injected binding.

    The program is:

        (let ((t (import "menai-test")))
          (let ((m (import "module")))
            (let ((thunk ((:: t test-find) (:: m tests) __path__)))
              (thunk))))

    Args:
        menai: Instance with the support module on its path
        module_name: Module name of the suite

    Returns:
        The compiled program and the index of the constant holding the path

    Raises:
        ValueError: If the injected placeholder is not in the constant pool
    """
    expression = (
        f'(let ((t (import "menai-test")))'
        f'    (let ((m (import "{module_name}")))'
        f'      (let ((thunk ((:: t test-find) (:: m tests) {_PATH_BINDING})))'
        f'        (thunk))))'
    )
    placeholder = _path_value(_PLACEHOLDER_PATH)
    code = menai.compile(expression, inject=(_PATH_BINDING, placeholder))

    for index, constant in enumerate(code.constants):
        if constant is placeholder:
            return code, index

    raise ValueError(
        "The injected path binding is not present in the compiled program's "
        "constant pool; the leaf program cannot be rebound"
    )


def _with_path(code: CodeObject, path_index: int, path: list[str]) -> CodeObject:
    """
    Return a copy of a compiled leaf program with the path rebound.

    Only the constant at `path_index` is replaced; every other constant belongs
    to the module under test and must be left untouched.

    Args:
        code: The compiled leaf program
        path_index: Index of the constant holding the path
        path: The leaf path to inject

    Returns:
        A copy of the code object with the path replaced
    """
    constants = list(code.constants)
    constants[path_index] = _path_value(path)
    return dataclasses.replace(code, constants=tuple(constants))


def _run_nodes(
    nodes: list[_Node],
    menai: Menai,
    code: CodeObject,
    path_index: int,
    outcomes: list[TestOutcome],
) -> None:
    """
    Recursively execute the leaves of a node-list.

    Args:
        nodes: Nodes to execute
        menai: Shared instance used for every leaf
        code: The compiled leaf program
        path_index: Index of the constant holding the path
        outcomes: Accumulator for leaf outcomes
    """
    for node in nodes:
        if node.is_leaf():
            assert node.thunk_path is not None
            outcomes.append(
                _run_leaf(
                    menai,
                    code,
                    path_index,
                    node.thunk_path,
                    node.expect_error,
                    node.expect_error_contains,
                )
            )

        else:
            _run_nodes(node.children, menai, code, path_index, outcomes)


def _run_leaf(
    menai: Menai,
    code: CodeObject,
    path_index: int,
    path: list[str],
    expect_error: bool,
    expect_error_contains: str | None,
) -> TestOutcome:
    """
    Execute a single leaf and interpret its outcome.

    Args:
        menai: The instance to execute on
        code: The compiled leaf program
        path_index: Index of the constant holding the path
        path: The leaf path
        expect_error: Whether the leaf is a negative test
        expect_error_contains: Required substring of a negative test's error

    Returns:
        The leaf's outcome
    """
    try:
        menai.execute_raw(_with_path(code, path_index, path))

    except MenaiError as exc:
        if not expect_error:
            return TestOutcome(path=path, passed=False, error=str(exc))

        if expect_error_contains is not None and expect_error_contains not in str(exc):
            return TestOutcome(
                path=path,
                passed=False,
                error=f"expected error containing {expect_error_contains!r}, but got: {exc}",
            )

        return TestOutcome(path=path, passed=True)

    if expect_error:
        return TestOutcome(
            path=path,
            passed=False,
            error="expected an error but the test returned normally",
        )

    return TestOutcome(path=path, passed=True)
