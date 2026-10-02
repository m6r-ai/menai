#!/usr/bin/env python3
"""
Menai Disassembler - Compile and disassemble Menai modules with detailed annotations.

This tool compiles Menai source files and generates annotated bytecode disassembly
showing:
- Constants table
- Variable assignments and function names
- Annotated instructions with what they do
- Source line numbers for each function
- Nested function hierarchy

Usage:
    menai-disassemble <file.menai>
    menai-disassemble <file.menai> --output disasm.txt
    menai-disassemble <file.menai> --trace  # Also show function call trace
    menai-disassemble --prelude              # Disassemble the prelude only
    menai-disassemble <file.menai> --prelude # Disassemble prelude then the file
"""

import argparse
from pathlib import Path
import sys
import traceback

from menai import Menai, MenaiError
from menai.ast.menai_ast_prelude_injector import MenaiASTPreludeInjector
from menai.menai_compiler import MenaiCompiler
from menai.bytecode.menai_bytecode import Opcode, CodeObject
from menai_render.menai_render_colour import green, grey, yellow
from menai_render.menai_render_instruction import (
    clean_name,
    instructions,
    render_code_metadata,
    render_instruction_lines,
)


def disassemble(code: CodeObject) -> str:
    """Return the disassembly of a single code object (no nested objects) as text."""
    return "\n".join(disassemble_with_nested(code))


def disassemble_with_nested(code: CodeObject, depth: int = 0, name: str | None = None, color: bool = False) -> list[str]:
    """Recursively disassemble code object and all nested code objects."""
    indent = "  " * depth
    display_name = name or code.name or "<top-level>"

    # Add source line info to display name if available
    loc_parts = []
    if code.source_file:
        loc_parts.append(code.source_file)

    if code.source_line and code.source_line > 0:
        loc_parts.append(f"line {code.source_line}")

    if loc_parts:
        display_name = f"{display_name} [{':'.join(loc_parts)}]"

    output = []
    output.append(f"{indent}{'-'*70}")                                    # plain: function opener
    output.append(f"{indent}{yellow('Function: ' + display_name, color)}")
    output.append(grey(f"{indent}{'-'*70}", color))

    output.extend(render_code_metadata(code, indent, color))

    # Show annotated disassembly
    output.append(f"{indent}{green('Instructions: ' + str(len(code.instructions)), color)}")
    output.append(grey(f"{indent}{'-'*70}", color))

    output.extend(render_instruction_lines(code, lambda _index, _instr: "", indent, color))

    output.append(f"{indent}{'-'*70}")                                    # plain: function closer
    output.append(f"{indent}")

    # Recursively disassemble nested code objects
    for i, nested_code in enumerate(code.code_objects):
        nested_name = clean_name(nested_code.name) if nested_code.name else f"<nested-{i}>"
        nested_output = disassemble_with_nested(nested_code, depth + 1, nested_name, color)
        output.extend(nested_output)

    return output


def analyze_function_flow(code: CodeObject) -> dict[int, str]:
    """Track which functions are stored in which variables."""
    var_map = {}

    for instr in instructions(code):
        if instr.opcode == Opcode.MAKE_CLOSURE:
            closure_idx = instr.src0
            var_idx = instr.dest
            if closure_idx < len(code.code_objects):
                nested_code = code.code_objects[closure_idx]
                func_name = clean_name(nested_code.name) if nested_code.name else f"<closure-{closure_idx}>"
                loc_parts = []
                if nested_code.source_file:
                    loc_parts.append(nested_code.source_file)

                if nested_code.source_line and nested_code.source_line > 0:
                    loc_parts.append(f"line {nested_code.source_line}")

                line_info = f" [{':'.join(loc_parts)}]" if loc_parts else ""
                var_map[var_idx] = f"{func_name}{line_info}"

    return var_map


def trace_calls(code: CodeObject, var_map: dict[int, str]) -> list[str]:
    """Trace function calls."""
    traces = []

    for i, instr in enumerate(instructions(code)):
        if instr.opcode == Opcode.CALL:
            arg_count = instr.src1
            func_reg = instr.src0
            func_desc = var_map.get(func_reg, f"r{func_reg}")
            traces.append(f"Instr {i:3}: CALL r{func_reg} ({arg_count} args) -> {func_desc}")

        elif instr.opcode == Opcode.TAIL_CALL:
            arg_count = instr.src1
            func_reg = instr.src0
            func_desc = var_map.get(func_reg, f"r{func_reg}")
            traces.append(f"Instr {i:3}: TAIL_CALL r{func_reg} ({arg_count} args) -> {func_desc}")

    return traces


def generate_trace(code: CodeObject, depth: int = 0, name: str | None = None) -> list[str]:
    """Generate function call trace."""
    indent = "  " * depth
    display_name = name or code.name or "<top-level>"

    loc_parts = []
    if code.source_file:
        loc_parts.append(code.source_file)

    if code.source_line and code.source_line > 0:
        loc_parts.append(f"line {code.source_line}")

    if loc_parts:
        display_name = f"{display_name} [{':'.join(loc_parts)}]"

    output = []
    output.append(f"\n{indent}{'='*70}")
    output.append(f"{indent}Function: {display_name}")
    output.append(f"{indent}Instructions: {len(code.instructions)}")
    output.append(f"{indent}{'='*70}")

    var_map = analyze_function_flow(code)

    if var_map:
        output.append(f"{indent}")
        output.append(f"{indent}Variable Assignments:")
        output.append(f"{indent}{'-'*70}")
        for var_idx in sorted(var_map.keys()):
            output.append(f"{indent}  var[{var_idx:2}] = {var_map[var_idx]}")

        output.append(f"{indent}{'-'*70}")

    traces = trace_calls(code, var_map)

    if traces:
        output.append(f"{indent}")
        output.append(f"{indent}Function Calls:")
        output.append(f"{indent}{'-'*70}")
        for trace in traces:
            output.append(f"{indent}{trace}")

        output.append(f"{indent}{'-'*70}")

    for i, nested_code in enumerate(code.code_objects):
        nested_name = nested_code.name or f"<closure-{i}>"
        nested_output = generate_trace(nested_code, depth + 1, nested_name)
        output.extend(nested_output)

    return output


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Disassemble Menai bytecode with detailed annotations",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('file', nargs='?', help='Menai source file to disassemble')
    parser.add_argument('--prelude', action='store_true', help='Also disassemble the prelude')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    parser.add_argument('--trace', '-t', action='store_true', help='Also generate function call trace')
    parser.add_argument('--no-color', action='store_true', help='Disable ANSI colour output')
    parser.add_argument('--color', '-c', action='store_true', help='Force ANSI colour output')
    parser.add_argument(
        '--module-path', action='append', default=[], metavar='DIR', dest='module_path',
        help='Prepend DIR to the module search path (repeatable)',
    )

    args = parser.parse_args()
    color = (not args.no_color and not args.output and sys.stdout.isatty()) or args.color

    if not args.file and not args.prelude:
        parser.error('at least one of <file> or --prelude is required')

    output_lines: list[str] = []
    total_code_objects = 0

    # --- Prelude disassembly ---

    if args.prelude:
        print("Compiling: <prelude>", file=sys.stderr)
        # The prelude is compiled as an ordinary program.  That wraps it in its
        # own bindings, but the inner copy shadows the outer one and the outer
        # copy is unused, so dead code elimination leaves exactly the prelude's
        # own functions.
        prelude_code = MenaiCompiler().compile(
            MenaiASTPreludeInjector.prelude_source(), name="<prelude>"
        )
        output_lines.extend(disassemble_with_nested(prelude_code, name="<prelude>", color=color))
        total_code_objects += len(prelude_code.code_objects) + 1

        if args.trace:
            output_lines.append("\n\n")
            output_lines.append("="*80)
            output_lines.append("PRELUDE FUNCTION CALL TRACE")
            output_lines.append("="*80)
            trace_lines = generate_trace(prelude_code, name="<prelude>")
            output_lines.extend(trace_lines)

    # --- User file disassembly ---

    # Read source file
    if args.file:
        source_path = Path(args.file)
        if not source_path.exists():
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return 1

        with open(source_path, 'r', encoding='utf-8') as f:
            source = f.read()

        # Compile
        print(f"Compiling: {args.file}", file=sys.stderr)

        # Build a deduplicated module search path: explicit --module-path
        # directories first, then the file's own directory and MENAI_PATH
        # application libraries, then the standard library.
        file_dir = str(source_path.parent.absolute())
        module_path: list[str] = []
        for d in list(args.module_path) + Menai.build_module_path(file_dir):
            if d not in module_path:
                module_path.append(d)

        menai = Menai(module_path=module_path)

        try:
            compiler = MenaiCompiler(module_loader=menai)
            code = compiler.compile(source, name=str(source_path))

        except MenaiError as e:
            print(f"Error compiling: {e}", file=sys.stderr)
            return 1

        except Exception as e:
            print(f"Error compiling: {e}", file=sys.stderr)
            traceback.print_exc()
            return 1

        # Generate disassembly
        output_lines.extend(disassemble_with_nested(code, name=args.file, color=color))
        total_code_objects += len(code.code_objects) + 1

        # Add trace if requested
        if args.trace:
            output_lines.append("\n\n")
            output_lines.append("="*80)
            output_lines.append("FUNCTION CALL TRACE")
            output_lines.append("="*80)
            trace_lines = generate_trace(code, name=args.file)
            output_lines.extend(trace_lines)

    # Output
    output_text = '\n'.join(output_lines)

    if args.output:
        output_path = Path(args.output)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_text)

        print(f"✓ Disassembly written to: {output_path}", file=sys.stderr)
        print(f"  Total lines: {len(output_lines)}", file=sys.stderr)
        print(f"  Total code objects: {total_code_objects}", file=sys.stderr)

    else:
        print(output_text)

    return 0


if __name__ == '__main__':
    sys.exit(main())
