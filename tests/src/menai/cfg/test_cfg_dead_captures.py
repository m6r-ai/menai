"""
Tests for the dead capture elimination CFG pass.

The pass removes FreeVarInstrs whose result SSA value is never referenced
anywhere in the child function.  The most common case is a self-recursive
letrec lambda whose only self-reference is a tail call that the CFG builder
has already converted to a SelfLoopTerm (back-edge jump).

Tests cover:
  - Self-capture elimination for simple tail-recursive letrec.
  - Self-capture elimination when outer captures survive.
  - No elimination when the self-capture is used in non-tail position.
  - No elimination when there are no captures at all.
  - End-to-end execution correctness.
"""

from menai.bytecode.menai_bytecode import Opcode, unpack_instruction
from menai.cfg.menai_cfg import (
    MenaiCFGBlock,
    MenaiCFGConstInstr,
    MenaiCFGFreeVarInstr,
    MenaiCFGFunction,
    MenaiCFGMakeClosureInstr,
    MenaiCFGParamInstr,
    MenaiCFGPatchClosureInstr,
    MenaiCFGReturnTerm,
    MenaiCFGSelfLoopTerm,
    MenaiCFGValue,
)
from menai.cfg.menai_cfg_dead_captures import MenaiCFGDeadCaptures
from menai.menai_compiler import MenaiCompiler
from menai.menai_value import MenaiInteger


_pass = MenaiCFGDeadCaptures()
_vid = 0


def v(hint: str = "") -> MenaiCFGValue:
    """Return a fresh SSA value with a unique id."""
    global _vid
    _vid += 1
    return MenaiCFGValue(id=_vid, hint=hint)


def _count_free_vars(func: MenaiCFGFunction) -> int:
    """Count FreeVarInstrs in a function's entry block."""
    return sum(
        1 for instr in func.blocks[0].instrs
        if isinstance(instr, MenaiCFGFreeVarInstr)
    )


def _find_make_closure(func: MenaiCFGFunction) -> MenaiCFGMakeClosureInstr | None:
    """Find the first MakeClosureInstr in a function."""
    for block in func.blocks:
        for instr in block.instrs:
            if isinstance(instr, MenaiCFGMakeClosureInstr):
                return instr

    return None


class TestSelfCaptureElimination:
    """Unit tests with hand-built CFGs."""

    def test_dead_self_capture_removed(self):
        """
        A child function with a self-capture FreeVarInstr that is never
        referenced (the self-call was converted to a SelfLoopTerm) should
        have the capture removed.
        """
        # Child function: one param, one free var (self-capture, never used).
        # The body is a self-loop (no reference to the capture).
        param = v("n")
        fv_self = v("self")
        child = MenaiCFGFunction(
            params=["n"],
            free_vars=["self"],
            binding_name="loop",
        )
        child_entry = MenaiCFGBlock(id=0, label="entry")
        child_entry.instrs = [
            MenaiCFGParamInstr(result=param, index=0, param_name="n"),
            MenaiCFGFreeVarInstr(result=fv_self, index=0, var_name="self"),
        ]
        child_entry.terminator = MenaiCFGSelfLoopTerm(args=[param])
        child.blocks = [child_entry]

        # Parent function: make closure + patch closure for self-capture.
        closure_val = v("loop")
        parent = MenaiCFGFunction(params=[], free_vars=[], binding_name=None)
        parent_entry = MenaiCFGBlock(id=0, label="entry")
        parent_entry.instrs = [
            MenaiCFGMakeClosureInstr(
                result=closure_val,
                function=child,
                captures=[],
                needs_patching=True,
            ),
            MenaiCFGPatchClosureInstr(
                closure=closure_val,
                capture_index=0,
                value=closure_val,
            ),
        ]
        parent_entry.terminator = MenaiCFGReturnTerm(value=closure_val)
        parent.blocks = [parent_entry]

        new_parent, changed = _pass._optimize_function(parent)

        assert changed
        mc = _find_make_closure(new_parent)
        assert mc is not None
        assert mc.needs_patching is False
        assert len(mc.captures) == 0
        assert mc.function.free_vars == []

        # No PatchClosureInstrs should remain.
        patches = [
            instr for instr in new_parent.blocks[0].instrs
            if isinstance(instr, MenaiCFGPatchClosureInstr)
        ]
        assert len(patches) == 0

        # No FreeVarInstrs should remain in the child.
        assert _count_free_vars(mc.function) == 0

    def test_live_outer_capture_survives(self):
        """
        A child function with a dead self-capture and a live outer capture
        should have only the self-capture removed.  The outer capture should
        be renumbered to index 0.
        """
        param = v("n")
        fv_self = v("self")
        fv_outer = v("limit")
        child = MenaiCFGFunction(
            params=["n"],
            free_vars=["self", "limit"],
            binding_name="loop",
        )
        child_entry = MenaiCFGBlock(id=0, label="entry")
        child_entry.instrs = [
            MenaiCFGParamInstr(result=param, index=0, param_name="n"),
            MenaiCFGFreeVarInstr(result=fv_self, index=0, var_name="self"),
            MenaiCFGFreeVarInstr(result=fv_outer, index=1, var_name="limit"),
        ]
        # Body uses fv_outer (limit) in a builtin but not fv_self.
        from menai.cfg.menai_cfg import MenaiCFGBuiltinInstr
        cmp_result = v("cmp")
        child_entry.instrs.append(MenaiCFGBuiltinInstr(
            result=cmp_result, op="integer>=?", args=[param, fv_outer],
        ))
        child_entry.terminator = MenaiCFGSelfLoopTerm(args=[param])
        child.blocks = [child_entry]

        outer_cap = v("limit_val")
        closure_val = v("loop")
        parent = MenaiCFGFunction(params=[], free_vars=[], binding_name=None)
        parent_entry = MenaiCFGBlock(id=0, label="entry")
        parent_entry.instrs = [
            MenaiCFGMakeClosureInstr(
                result=closure_val,
                function=child,
                captures=[outer_cap],
                needs_patching=True,
            ),
            MenaiCFGPatchClosureInstr(
                closure=closure_val,
                capture_index=0,
                value=closure_val,
            ),
        ]
        parent_entry.terminator = MenaiCFGReturnTerm(value=closure_val)
        parent.blocks = [parent_entry]

        new_parent, changed = _pass._optimize_function(parent)

        assert changed
        mc = _find_make_closure(new_parent)
        assert mc is not None
        assert mc.needs_patching is False
        assert len(mc.captures) == 1
        assert mc.captures[0] is outer_cap
        assert mc.function.free_vars == ["limit"]

        # The surviving FreeVar should be renumbered to index 0.
        fvs = [
            instr for instr in mc.function.blocks[0].instrs
            if isinstance(instr, MenaiCFGFreeVarInstr)
        ]
        assert len(fvs) == 1
        assert fvs[0].index == 0
        assert fvs[0].var_name == "limit"

    def test_live_self_capture_not_removed(self):
        """
        A self-capture that IS referenced (e.g. a non-tail self-call) should
        not be removed.
        """
        param = v("n")
        fv_self = v("self")
        child = MenaiCFGFunction(
            params=["n"],
            free_vars=["self"],
            binding_name="loop",
        )
        child_entry = MenaiCFGBlock(id=0, label="entry")
        # The FreeVarInstr's result (fv_self) is referenced by the return.
        child_entry.instrs = [
            MenaiCFGParamInstr(result=param, index=0, param_name="n"),
            MenaiCFGFreeVarInstr(result=fv_self, index=0, var_name="self"),
        ]
        child_entry.terminator = MenaiCFGReturnTerm(value=fv_self)
        child.blocks = [child_entry]

        closure_val = v("loop")
        parent = MenaiCFGFunction(params=[], free_vars=[], binding_name=None)
        parent_entry = MenaiCFGBlock(id=0, label="entry")
        parent_entry.instrs = [
            MenaiCFGMakeClosureInstr(
                result=closure_val,
                function=child,
                captures=[],
                needs_patching=True,
            ),
            MenaiCFGPatchClosureInstr(
                closure=closure_val,
                capture_index=0,
                value=closure_val,
            ),
        ]
        parent_entry.terminator = MenaiCFGReturnTerm(value=closure_val)
        parent.blocks = [parent_entry]

        new_parent, changed = _pass._optimize_function(parent)

        assert not changed
        mc = _find_make_closure(new_parent)
        assert mc is not None
        assert mc.needs_patching is True
        assert mc.function.free_vars == ["self"]

    def test_no_captures_no_change(self):
        """A function with no captures should be unchanged."""
        param = v("n")
        child = MenaiCFGFunction(
            params=["n"],
            free_vars=[],
            binding_name="loop",
        )
        child_entry = MenaiCFGBlock(id=0, label="entry")
        child_entry.instrs = [
            MenaiCFGParamInstr(result=param, index=0, param_name="n"),
        ]
        child_entry.terminator = MenaiCFGReturnTerm(value=param)
        child.blocks = [child_entry]

        closure_val = v("loop")
        parent = MenaiCFGFunction(params=[], free_vars=[], binding_name=None)
        parent_entry = MenaiCFGBlock(id=0, label="entry")
        parent_entry.instrs = [
            MenaiCFGMakeClosureInstr(
                result=closure_val,
                function=child,
                captures=[],
                needs_patching=False,
            ),
        ]
        parent_entry.terminator = MenaiCFGReturnTerm(value=closure_val)
        parent.blocks = [parent_entry]

        new_parent, changed = _pass._optimize_function(parent)

        assert not changed


class TestIntegration:
    """Integration tests compiling real Menai source."""

    def _compile(self, src: str):
        """Compile Menai source and return the top-level CodeObject."""
        return MenaiCompiler().compile(src)

    def _find_lambda(self, code, name: str):
        """Find a nested code object by name (BFS through all code objects)."""
        queue = [code]
        while queue:
            co = queue.pop(0)
            if name in co.name:
                return co
            queue.extend(co.code_objects)

        raise AssertionError(f"lambda {name!r} not found")

    def _count_op(self, code, opcode) -> int:
        """Count occurrences of opcode in code and all nested code objects."""
        n = sum(1 for i in code.instructions if unpack_instruction(i).opcode == opcode)
        for nested in code.code_objects:
            n += self._count_op(nested, opcode)

        return n

    def test_self_recursive_letrec_no_make_closure(self):
        """
        A simple self-recursive letrec whose only self-reference is a tail
        call should compile to LOAD_CONST (no MAKE_CLOSURE, no PATCH_CLOSURE).
        """
        src = """
        (letrec ((sum (lambda (n acc)
                        (if (integer=? n 0)
                            acc
                            (sum (integer- n 1) (integer+ acc n))))))
          (sum 100 0))
        """
        code = self._compile(src)
        assert self._count_op(code, Opcode.MAKE_CLOSURE) == 0
        assert self._count_op(code, Opcode.PATCH_CLOSURE) == 0

    def test_self_recursive_with_outer_capture_no_patch(self):
        """
        A self-recursive letrec that also captures an outer variable should
        not need PATCH_CLOSURE for the self-capture (only for the outer
        capture, which is handled by the bytecode builder directly).
        """
        src = """
        (letrec ((limit 100000)
                 (loop (lambda (n)
                          (if (integer>=? n limit) n (loop (integer+ n 1))))))
          (loop 0))
        """
        code = self._compile(src)
        # The loop function should have no self-capture free var.
        loop = self._find_lambda(code, "loop")
        assert len(loop.free_vars) == 1
        assert loop.free_vars[0] == "limit"

    def test_execution_correct_simple_recursion(self):
        """End-to-end: simple tail recursion produces correct results."""
        from menai import Menai

        menai = Menai()
        result = menai.evaluate("""
        (letrec ((sum (lambda (n acc)
                        (if (integer=? n 0)
                            acc
                            (sum (integer- n 1) (integer+ acc n))))))
          (sum 100 0))
        """)
        assert result == 5050

    def test_execution_correct_with_outer_capture(self):
        """End-to-end: recursion with outer capture produces correct results."""
        from menai import Menai

        menai = Menai()
        result = menai.evaluate("""
        (letrec ((limit 100000)
                 (loop (lambda (n)
                          (if (integer>=? n limit) n (loop (integer+ n 1))))))
          (loop 0))
        """)
        assert result == 100000

    def test_execution_correct_nested_letrec(self):
        """End-to-end: nested letrec with outer capture works correctly."""
        from menai import Menai

        menai = Menai()
        result = menai.evaluate(r"""
        (letrec ((skip-ws
                  (lambda (s pos)
                    (letrec ((loop (lambda (i)
                                     (if (integer>=? i (string-length s))
                                         i
                                         (let ((ch (string-ref s i)))
                                           (if (or (string=? ch " ")
                                               (or (string=? ch "\t")
                                               (or (string=? ch "\n")
                                                   (string=? ch "\r"))))
                                               (loop (integer+ i 1))
                                               i))))))
                      (loop pos)))))
          (list
            (skip-ws "   hello" 0)
            (skip-ws "\t\nworld" 0)
            (skip-ws "no-space" 0)
            (skip-ws "   " 0)))
        """)
        assert result == [3, 2, 0, 3]

    def test_execution_correct_mutual_recursion(self):
        """End-to-end: mutual recursion still works (both captures are live)."""
        from menai import Menai

        menai = Menai()
        result = menai.evaluate("""
        (letrec ((even? (lambda (n) (if (integer=? n 0) #t (odd?  (integer- n 1)))))
                 (odd?  (lambda (n) (if (integer=? n 0) #f (even? (integer- n 1))))))
          (list (even? 100) (odd? 101)))
        """)
        assert result == [True, True]

    def test_non_tail_self_recursion_keeps_capture(self):
        """
        A self-recursive letrec where the self-call is NOT in tail position
        should keep the self-capture (it's needed for the non-tail call).
        """
        src = """
        (letrec ((fact (lambda (n)
                         (if (integer<=? n 1)
                             1
                             (integer* n (fact (integer- n 1)))))))
          (fact 5))
        """
        code = self._compile(src)
        fact = self._find_lambda(code, "fact")
        # fact should still have the self-capture.
        assert "fact" in fact.free_vars

    def test_prelude_boolean_eq_works(self):
        """The prelude's boolean=? function (which uses this pattern) works."""
        from menai import Menai

        menai = Menai()
        result = menai.evaluate("""
        (boolean=? #t #t #t)
        """)
        assert result is True

        result = menai.evaluate("""
        (boolean=? #t #f #t)
        """)
        assert result is False
