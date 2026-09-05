.DEFAULT_GOAL := build

#
# Detect the operating system.
#
UNAME := $(shell uname -s)

#
# Python interpreter — uses the venv if present, otherwise system python3.
#
PYTHON := $(shell test -f venv/bin/python && echo venv/bin/python || echo python3)

#
# Extension entry point — the single .c file that defines the Python module.
#
SO_CORE_SOURCES := src/menai/vm/menai_vm_c.c

#
# All C source and header files in the menai VM directory — any change to any
# of them triggers a rebuild.
#
C_SOURCES := $(wildcard src/menai/vm/menai_vm_*.[ch])

#
# Generated header — opcode #defines produced from the Python Opcode enum.
#
GEN_OPCODE_HEADER := src/menai/vm/menai_vm_opcodes.h

#
# Derive the expected .so name from the Python ABI tag.
#
EXT_SUFFIX := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")

SO_FILES := \
	$(patsubst src/menai/vm/%.c, src/menai/vm/%$(EXT_SUFFIX), $(SO_CORE_SOURCES))

#
# Build all extensions in-place.
#
.PHONY: build

build: $(SO_FILES)

$(GEN_OPCODE_HEADER): src/menai/bytecode/menai_bytecode.py tools/gen_opcode_defs.py
	$(PYTHON) -m tools.gen_opcode_defs

$(SO_FILES): $(C_SOURCES) $(GEN_OPCODE_HEADER)
	@rm -f $(SO_FILES)
	@rm -rf build/temp.* build/lib.*
	$(PYTHON) setup.py build_ext --inplace
	@mkdir -p build && touch $@

#
# Build with the leak detector enabled (MENAI_DEBUG_LEAKS).
#
.PHONY: build-leaks

build-leaks: $(GEN_OPCODE_HEADER)
	@rm -f $(SO_FILES)
	@rm -rf build/temp.* build/lib.*
	MENAI_DEBUG_LEAKS=1 $(PYTHON) setup.py build_ext --inplace
	@mkdir -p build && touch $(SO_FILES)

#
# Run the full test suite.
#
.PHONY: test

test:
	$(PYTHON) -m pytest tests/

#
# Remove the compiled .so files and generated opcode header.
#
.PHONY: clean

clean:
	rm -f $(SO_FILES)
	rm -f $(GEN_OPCODE_HEADER)

#
# Remove all build artefacts.
#
.PHONY: realclean

realclean: clean
	rm -rf build/temp.* build/lib.*
	find src -name "*.pyc" -delete
	find src -name "__pycache__" -type d -exec rm -rf {} +
