"""
Derivation of bytecode coverage from a VM execution trace.

Coverage is the boolean view of a trace: an instruction is covered when it
executed at least once.  This is deliberately a separate view from the execution
profile, which ranks instructions by how often they ran.  A profile answers
"where did the work happen"; coverage answers "which bytecode was never
reached".

The trace already contains one entry for every instruction in the compiled code
tree, with a zero for instructions that did not execute.  That makes the
unexecuted instructions explicit rather than inferred: nothing is missing from
the array, so coverage does not depend on sampling or on a second instrumentation
mode.

Coverage is measured over the bytecode that was compiled.  Code that an
optimisation pass removed before bytecode emission (an unreachable binding, an
inlined function) has no instructions in the tree and therefore contributes
neither to the covered nor to the total count.  This is the correct definition
for bytecode coverage: the question is what the VM did with the program it was
given, not what the source contained before optimisation.
"""

from dataclasses import dataclass, field

from menai.bytecode.menai_bytecode import CodeObject, Instruction
from menai_trace.menai_trace_data import resolve_trace, TraceResult


@dataclass(frozen=True, slots=True)
class InstructionCoverage:
    """Coverage of one instruction: its trace count and whether it ran."""

    index: int
    instruction: Instruction
    count: int

    def covered(self) -> bool:
        """Return True if this instruction executed at least once."""
        return self.count > 0


@dataclass(frozen=True, slots=True)
class FunctionCoverage:
    """
    Coverage of one code object and the instructions it contains.

    `path` is the code object's position in the tree, `ordinal` is its canonical
    code ordinal, and `calls` is the number of times the code object was entered.
    A function is considered entered when any of its instructions executed; the
    call count is retained separately because a function can execute through a
    path that does not increment it (for example a tail call folded into a loop
    is still one call).
    """

    path: tuple[int, ...]
    ordinal: int
    code: CodeObject
    calls: int
    instructions: tuple[InstructionCoverage, ...]

    def total_instructions(self) -> int:
        """Return the number of instructions in this code object."""
        return len(self.instructions)

    def covered_instructions(self) -> int:
        """Return the number of instructions in this code object that executed."""
        return sum(1 for instruction in self.instructions if instruction.covered())

    def uncovered_instructions(self) -> tuple[InstructionCoverage, ...]:
        """Return this code object's instructions that never executed, in order."""
        return tuple(instruction for instruction in self.instructions if not instruction.covered())

    def coverage_percent(self) -> float:
        """Return this code object's instruction coverage as a percentage."""
        if not self.instructions:
            return 100.0

        return self.covered_instructions() / self.total_instructions() * 100.0

    def entered(self) -> bool:
        """Return True if any instruction in this code object executed."""
        return any(instruction.covered() for instruction in self.instructions)

    def fully_covered(self) -> bool:
        """Return True if every instruction in this code object executed."""
        return self.covered_instructions() == self.total_instructions()


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """Bytecode coverage for one compiled program."""

    functions: tuple[FunctionCoverage, ...]

    def total_instructions(self) -> int:
        """Return the total number of instructions in the compiled program."""
        return sum(function.total_instructions() for function in self.functions)

    def covered_instructions(self) -> int:
        """Return the number of instructions that executed at least once."""
        return sum(function.covered_instructions() for function in self.functions)

    def uncovered_instructions(self) -> int:
        """Return the number of instructions that never executed."""
        return self.total_instructions() - self.covered_instructions()

    def coverage_percent(self) -> float:
        """Return overall instruction coverage as a percentage."""
        if self.total_instructions() == 0:
            return 100.0

        return self.covered_instructions() / self.total_instructions() * 100.0

    def function_count(self) -> int:
        """Return the number of code objects in the compiled program."""
        return len(self.functions)

    def entered_function_count(self) -> int:
        """Return the number of code objects in which at least one instruction executed."""
        return sum(1 for function in self.functions if function.entered())

    def never_entered(self) -> tuple[FunctionCoverage, ...]:
        """Return the code objects in which no instruction executed, in tree order."""
        return tuple(function for function in self.functions if not function.entered())


def coverage_from_trace(result: TraceResult) -> CoverageResult:
    """Derive bytecode coverage from a resolved execution trace."""
    functions = tuple(
        FunctionCoverage(
            path=function.path,
            ordinal=function.ordinal,
            code=function.code,
            calls=function.calls,
            instructions=tuple(
                InstructionCoverage(
                    index=trace.index,
                    instruction=trace.instruction,
                    count=trace.count,
                )
                for trace in function.instructions
            ),
        )
        for function in result.functions
    )
    return CoverageResult(functions=functions)


def coverage_from_counts(
    root: CodeObject,
    instr_counts: list[int],
    call_counts: list[int],
) -> CoverageResult:
    """Resolve raw ordinal arrays and derive bytecode coverage from them."""
    return coverage_from_trace(resolve_trace(root, instr_counts, call_counts))


class CoverageAccumulator:
    """
    Accumulate per-instruction coverage across repeated executions of one program.

    A compiled program can be executed many times (for example once per test leaf)
    while the VM trace arrays are zeroed at the start of each `execute`.  Coverage
    over the whole workload is therefore the element-wise sum of the arrays from
    each run, not the arrays from the last run.

    The accumulator is tied to one code tree.  Calling `begin` with a different
    tree starts a fresh accumulation, because instruction ordinals from two
    different trees are unrelated.
    """

    def __init__(self) -> None:
        """Create an empty accumulator that has not yet been bound to a program."""
        self._root: CodeObject | None = None
        self._instr_counts: list[int] | None = None
        self._call_counts: list[int] | None = None

    def begin(self, root: CodeObject) -> None:
        """Bind the accumulator to a compiled program and clear any prior counts."""
        self._root = root
        self._instr_counts = None
        self._call_counts = None

    def add(self, instr_counts: list[int], call_counts: list[int]) -> None:
        """
        Add one execution's trace arrays to the accumulated totals.

        Raises:
            ValueError: If no program has been bound, or if the arrays do not
                match the shape of the previously accumulated arrays.  A shape
                change means the execution used a different code tree, whose
                ordinals cannot be combined with the existing ones.
        """
        if self._root is None:
            raise ValueError("coverage accumulator has no program; call begin() first")

        if self._instr_counts is None or self._call_counts is None:
            self._instr_counts = list(instr_counts)
            self._call_counts = list(call_counts)
            return

        if len(instr_counts) != len(self._instr_counts) or len(call_counts) != len(self._call_counts):
            raise ValueError("trace array shape changed during coverage accumulation")

        for index, count in enumerate(instr_counts):
            self._instr_counts[index] += count

        for index, count in enumerate(call_counts):
            self._call_counts[index] += count

    def result(self) -> CoverageResult:
        """
        Return the accumulated coverage.

        Raises:
            ValueError: If no execution has been added since `begin`.
        """
        if self._root is None or self._instr_counts is None or self._call_counts is None:
            raise ValueError("coverage accumulator has no trace data")

        return coverage_from_counts(self._root, self._instr_counts, self._call_counts)


@dataclass(frozen=True, slots=True)
class FunctionKey:
    """
    Stable identity for a function across separately compiled programs.

    Instruction ordinals are only meaningful within one compiled tree, so
    cumulative coverage cannot merge raw arrays.  It merges by source identity:
    a function from the same file, at the same line, with the same short name is
    treated as the same function in every program that compiled it.

    The short name strips the module-import index that the compiler adds to
    closure names (for example `json#1#decode` becomes `decode`) and the
    parameter-count suffix, because both can change with the import context.
    """

    source_file: str
    source_line: int
    name: str


def function_key(code: CodeObject) -> FunctionKey:
    """Return the stable source identity of a code object."""
    name = code.name or "<anonymous>"
    short_name = name.rsplit("#", 1)[-1]
    if "(" in short_name:
        short_name = short_name[:short_name.index("(")].strip()

    # The compiler numbers anonymous lambdas per compilation, so the number is
    # not stable across programs.  The source file and line identify the lambda.
    if short_name.startswith("<lambda"):
        short_name = "<lambda>"

    return FunctionKey(
        source_file=code.source_file,
        source_line=code.source_line,
        name=short_name,
    )


@dataclass
class CumulativeFunctionCoverage:
    """
    Coverage of one function merged across every program that compiled it.

    `all_indices` is the union of instruction indices seen for this function and
    `covered_indices` is the union of those that executed at least once in any
    program.  Each instruction is therefore counted once regardless of how many
    test files or benchmark suites compiled the function.

    A function that compiles to different instruction layouts in different
    programs is merged by index, so coverage is a conservative union rather than
    an exact per-variant figure.  The layout is stable for a given source
    function in normal use; the union only matters when optimisation
    specialises a function differently per program.
    """

    key: FunctionKey
    all_indices: set[int] = field(default_factory=set)
    covered_indices: set[int] = field(default_factory=set)
    calls: int = 0
    variants: int = 0

    def add(self, function: FunctionCoverage) -> None:
        """Merge one compilation's coverage of this function into the totals."""
        self.all_indices.update(instruction.index for instruction in function.instructions)
        self.covered_indices.update(
            instruction.index for instruction in function.instructions if instruction.covered()
        )
        self.calls += function.calls
        self.variants += 1

    def total_instructions(self) -> int:
        """Return the number of distinct instructions seen for this function."""
        return len(self.all_indices)

    def covered_instructions(self) -> int:
        """Return the number of distinct instructions that executed at least once."""
        return len(self.covered_indices)

    def uncovered_indices(self) -> tuple[int, ...]:
        """Return the instruction indices that never executed, in ascending order."""
        return tuple(sorted(self.all_indices - self.covered_indices))

    def coverage_percent(self) -> float:
        """Return this function's cumulative instruction coverage as a percentage."""
        if not self.all_indices:
            return 100.0

        return self.covered_instructions() / self.total_instructions() * 100.0

    def entered(self) -> bool:
        """Return True if any instruction executed in any program."""
        return self.covered_instructions() > 0

    def label(self) -> str:
        """Return a display label naming the function's source location."""
        if self.key.source_line > 0:
            return f"{self.key.name} [{self.key.source_file}:line {self.key.source_line}]"

        return f"{self.key.name} [{self.key.source_file}]"


@dataclass
class CumulativeCoverage:
    """
    Coverage merged across a set of separately compiled programs.

    Programs are added one at a time with `add`.  Code objects with no source
    file are skipped: they are module roots, whose instruction positions depend
    on the whole program and have no stable identity across compilations.
    Functions with a source file are merged by `FunctionKey`.
    """

    functions: dict[FunctionKey, CumulativeFunctionCoverage] = field(default_factory=dict)

    def add(self, coverage: CoverageResult) -> None:
        """Merge one compiled program's coverage into the cumulative totals."""
        for function in coverage.functions:
            if not function.code.source_file:
                continue

            key = function_key(function.code)
            merged = self.functions.get(key)
            if merged is None:
                merged = CumulativeFunctionCoverage(key=key)
                self.functions[key] = merged

            merged.add(function)

    def total_instructions(self) -> int:
        """Return the total distinct instructions across all merged functions."""
        return sum(function.total_instructions() for function in self.functions.values())

    def covered_instructions(self) -> int:
        """Return the number of distinct instructions that executed at least once."""
        return sum(function.covered_instructions() for function in self.functions.values())

    def uncovered_instructions(self) -> int:
        """Return the number of distinct instructions that never executed."""
        return self.total_instructions() - self.covered_instructions()

    def coverage_percent(self) -> float:
        """Return overall cumulative instruction coverage as a percentage."""
        if self.total_instructions() == 0:
            return 100.0

        return self.covered_instructions() / self.total_instructions() * 100.0

    def function_count(self) -> int:
        """Return the number of distinct functions merged."""
        return len(self.functions)

    def entered_function_count(self) -> int:
        """Return the number of distinct functions in which any instruction executed."""
        return sum(1 for function in self.functions.values() if function.entered())

    def ordered_functions(self) -> tuple[CumulativeFunctionCoverage, ...]:
        """Return the merged functions ordered by coverage, least covered first."""
        return tuple(
            sorted(
                self.functions.values(),
                key=lambda function: (
                    function.coverage_percent(),
                    function.key.source_file,
                    function.key.source_line,
                    function.key.name,
                ),
            )
        )
