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

from setuptools import Extension, setup

_MENAI_VM_SRC = "src/menai/vm"

extensions = [
    Extension(
        name="menai.vm.menai_vm_c",
        sources=[
            f"{_MENAI_VM_SRC}/menai_vm_alloc.c",
            f"{_MENAI_VM_SRC}/menai_vm_bigint.c",
            f"{_MENAI_VM_SRC}/menai_vm_boolean.c",
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
            f"{_MENAI_VM_SRC}/menai_vm_integer.c",
            f"{_MENAI_VM_SRC}/menai_vm_list.c",
            f"{_MENAI_VM_SRC}/menai_vm_none.c",
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
    ),
]

setup(ext_modules=extensions)
