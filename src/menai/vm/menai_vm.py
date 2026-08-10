"""Thin wrapper around the C VM execution engine."""

from collections.abc import Callable
from typing import cast

from menai.bytecode.menai_bytecode import Opcode
from menai.bytecode.menai_bytecode import CodeObject
from menai.menai_value import MenaiValue
from menai.vm.menai_vm_bytecode_validator import validate_bytecode
from menai.vm.menai_vm_errors import _MenaiVMRuntimeError, translate_vm_error

# pylint: disable=no-name-in-module
from menai.vm.menai_vm_c import execute as _c_vm_execute  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import state_alloc as _c_vm_state_alloc  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import state_free as _c_vm_state_free  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import set_prelude as _c_vm_set_prelude  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import cancel as _c_vm_cancel  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import enable_profiling as _c_vm_enable_profiling  # type: ignore[import-not-found]
from menai.vm.menai_vm_c import get_profile_data as _c_vm_get_profile_data  # type: ignore[import-not-found]


class MenaiVM:
    """Wrapper around the C VM, exposing execute() and cancel()."""

    def __init__(self, validate: bool = True) -> None:
        self.validate_bytecode = validate
        self._state = _c_vm_state_alloc()

    def __del__(self) -> None:
        if hasattr(self, '_state'):
            _c_vm_state_free(self._state)

    def set_prelude(self, prelude: CodeObject) -> None:
        """Execute a prelude CodeObject and store its globals in the VM state."""
        _c_vm_set_prelude(self._state, prelude)

    def execute(
        self,
        code: CodeObject,
        extra_bindings: dict[str, MenaiValue] | None = None,
    ) -> MenaiValue:
        """Execute a code object and return the result."""
        if self.validate_bytecode:
            validate_bytecode(code)

        try:
            return cast(Callable[..., MenaiValue], _c_vm_execute)(
                code, extra_bindings or {}, self._state
            )

        except _MenaiVMRuntimeError as exc:
            raise translate_vm_error(
                exc.code,
                exc.opcode,
                exc.ip,
                exc.call_depth,
                exc.user_message,
            ) from None

    def cancel(self) -> None:
        """
        Request cancellation of the currently executing code.

        Thread-safe: may be called from a different thread than the one
        executing the VM.  The flag is checked at the next cancellation
        check point in the C execution loop.
        """
        _c_vm_cancel(self._state)

    def enable_profiling(self) -> None:
        """Reset profiling counters and enable per-opcode profiling."""
        _c_vm_enable_profiling(self._state)

    def get_profile_data(self) -> dict[str, int]:
        """
        Return profiling data as a dict mapping opcode name to execution count.

        The special key "__total__" maps to total instruction count.
        Returns an empty dict if profiling was never enabled or the VM
        was not built with MENAI_PROFILE=1.
        """
        raw = _c_vm_get_profile_data(self._state)
        result: dict[str, int] = {}
        for key, count in raw.items():
            if key == "__total__":
                result["__total__"] = count

            else:
                result[Opcode(int(key)).name] = count

        return result
