"""
Build script for the Menai C VM extension.

The extension definition lives here (rather than in pyproject.toml) because
it requires platform-specific compile flags (-Werror / /WX) that cannot be
expressed declaratively in the [tool.setuptools.ext-modules] table.

Everything else — package metadata, discovery, scripts — is handled by
pyproject.toml.  This file only contributes the ext-modules.

Usage:
    python setup.py build_ext --inplace    # development build
"""

import sys
import os
import subprocess

from setuptools import Extension, setup

_MENAI_VM_SRC = "src/menai/vm"

#
# Generate the opcode header from the Python Opcode enum before compiling.
# This ensures the C defines are always in sync regardless of how the build
# is invoked (make, setup.py, cibuildwheel, pip install from sdist).
#
subprocess.run([sys.executable, "-m", "tools.gen_opcode_defs"], check=True)

extensions = [
    Extension(
        name="menai.vm.menai_vm_c",
        sources=[
            f"{_MENAI_VM_SRC}/menai_vm_alloc.c",
            f"{_MENAI_VM_SRC}/menai_vm_bigint.c",
            f"{_MENAI_VM_SRC}/menai_vm_bridge.c",
            f"{_MENAI_VM_SRC}/menai_vm_bytes.c",
            f"{_MENAI_VM_SRC}/menai_vm_c.c",
            f"{_MENAI_VM_SRC}/menai_vm_code.c",
            f"{_MENAI_VM_SRC}/menai_vm_complex.c",
            f"{_MENAI_VM_SRC}/menai_vm_dict.c",
            f"{_MENAI_VM_SRC}/menai_vm_float.c",
            f"{_MENAI_VM_SRC}/menai_vm_function.c",
            f"{_MENAI_VM_SRC}/menai_vm_globals.c",
            f"{_MENAI_VM_SRC}/menai_vm_hashtable.c",
            f"{_MENAI_VM_SRC}/menai_vm_gc.c",
            f"{_MENAI_VM_SRC}/menai_vm_integer.c",
            f"{_MENAI_VM_SRC}/menai_vm_list.c",
            f"{_MENAI_VM_SRC}/menai_vm_string.c",
            f"{_MENAI_VM_SRC}/menai_vm_set.c",
            f"{_MENAI_VM_SRC}/menai_vm_state.c",
            f"{_MENAI_VM_SRC}/menai_vm_struct.c",
            f"{_MENAI_VM_SRC}/menai_vm_structtype.c",
            f"{_MENAI_VM_SRC}/menai_vm_symbol.c",
            f"{_MENAI_VM_SRC}/menai_vm_value.c",
        ],
        include_dirs=[_MENAI_VM_SRC],
        extra_compile_args=(
            ["/O2", "/std:c11", "/WX"] if sys.platform == "win32"
            else ["-O2", "-std=c11", "-Werror"]
        ),
        define_macros=(
            [("MENAI_DEBUG_LEAKS", "1")] if os.environ.get("MENAI_DEBUG_LEAKS") else []
        ) + (
            [("MENAI_DEBUG_MAGIC", "1")] if os.environ.get("MENAI_DEBUG_MAGIC") else []
        ),
    ),
]

setup(ext_modules=extensions)
