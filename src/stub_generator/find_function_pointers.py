#!/usr/bin/env python3
import ctypes.util
import sys
import json
import re
import shutil
import subprocess
import os
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import clang.cindex
from clang.cindex import CursorKind

from stub_generator.makefile_helpers import (
    build_analysis_args,
    resolve_linked_source_files,
)

_LIBCLANG_CONFIGURED = False


def configure_libclang():
    """Configure libclang discovery before creating an index."""
    global _LIBCLANG_CONFIGURED

    if _LIBCLANG_CONFIGURED:
        return

    if getattr(clang.cindex.Config, "loaded", False):
        _LIBCLANG_CONFIGURED = True
        return

    candidates = []
    env_path = os.environ.get("LIBCLANG_PATH")
    if env_path:
        if os.path.isdir(env_path):
            candidates.append(("path", env_path))
        else:
            candidates.append(("file", env_path))

    discovered = ctypes.util.find_library("clang")
    if discovered:
        candidates.append(("file", discovered))

    last_error = None
    for candidate_type, candidate in candidates:
        try:
            if candidate_type == "path":
                clang.cindex.Config.set_library_path(candidate)
            else:
                clang.cindex.Config.set_library_file(candidate)
            clang.cindex.conf.get_cindex_library()
            _LIBCLANG_CONFIGURED = True
            return
        except Exception as exc:
            last_error = exc

    try:
        clang.cindex.conf.get_cindex_library()
        _LIBCLANG_CONFIGURED = True
        return
    except Exception as exc:
        last_error = exc

    raise RuntimeError(
        "Unable to locate libclang. Set LIBCLANG_PATH to the libclang shared library "
        f"or its directory. Last error: {last_error}"
    )


def _create_index():
    configure_libclang()
    return clang.cindex.Index.create()

def get_diagnostics(translation_unit):
    return [d.spelling for d in translation_unit.diagnostics]

def get_clang_resource_dir():
    """
    Attempt to find the Clang resource directory containing standard headers
    like stddef.h, stdarg.h, etc.
    """
    # 1. Try asking the clang executable if it's in the PATH
    clang_exe = shutil.which('clang')
    if clang_exe:
        try:
            # Run: clang -print-resource-dir
            result = subprocess.run(
                [clang_exe, '-print-resource-dir'],
                capture_output=True,
                text=True,
                check=True
            )
            resource_dir = result.stdout.strip()
            if resource_dir and os.path.isdir(resource_dir):
                include_dir = os.path.join(resource_dir, 'include')
                if os.path.isdir(include_dir):
                    return include_dir
        except Exception:
            pass
            
    # 2. As a fallback, look in common locations if the executable approach failed
    # (e.g. if we are running in an environment where clang binary isn't the same as libclang)
    # The user observed /usr/lib/clang/10.0.0/include/ exists in their environment
    base_search_paths = [
        '/usr/lib/clang',
        '/usr/local/lib/clang'
    ]
    
    for base in base_search_paths:
        if os.path.isdir(base):
            # Sort versions descending to pick the newest
            try:
                versions = sorted(os.listdir(base), reverse=True)
                for v in versions:
                    include_path = os.path.join(base, v, 'include')
                    if os.path.isdir(include_path):
                         return include_path
            except OSError:
                continue
                
    return None

def _walk_function_body(func_cursor, func_name, file_contents):
    """
    Walk a single function body and detect:
    - Function pointer (indirect) calls -> returned as fp_results
    - Direct callees -> returned as a set of names
    
    This is the core detection logic extracted from find_function_calls
    so it can be reused by both single-file and multi-file analysis.
    """
    fp_results = []
    direct_callees = set()

    for node in func_cursor.walk_preorder():
        if node.kind != CursorKind.CALL_EXPR:
            continue

        ref = node.referenced
        is_indirect = False
        heuristic_reason = None
        callee_name = node.spelling

        if ref:
            if ref.kind != CursorKind.FUNCTION_DECL:
                is_indirect = True
                heuristic_reason = "ref_not_func"
        else:
            children = list(node.get_children())
            if children:
                callee = children[0]
                if callee.kind in (CursorKind.MEMBER_REF_EXPR, CursorKind.ARRAY_SUBSCRIPT_EXPR):
                    is_indirect = True
                    heuristic_reason = "syntax"
                elif callee.kind == CursorKind.UNEXPOSED_EXPR and not node.spelling:
                    is_indirect = True
                    heuristic_reason = "unexposed_heuristic"

        if is_indirect:
            # Get line content
            if node.location.file:
                fname = node.location.file.name
                if fname not in file_contents:
                    try:
                        with open(fname, 'r', encoding='utf-8', errors='ignore') as f:
                            file_contents[fname] = f.readlines()
                    except IOError:
                        file_contents[fname] = []

                lines = file_contents[fname]
                line_idx = node.location.line - 1
                line_content = lines[line_idx].strip() if 0 <= line_idx < len(lines) else ""

                if not callee_name and 'callee' in dir():
                    start = callee.extent.start
                    end = callee.extent.end
                    if start.file and start.file.name == fname:
                        s_line = start.line - 1
                        s_col = start.column - 1
                        e_line = end.line - 1
                        e_col = end.column - 1
                        if s_line == e_line:
                            callee_name = lines[s_line][s_col:e_col]
                        else:
                            parts = [lines[s_line][s_col:]]
                            for k in range(s_line + 1, e_line):
                                parts.append(lines[k])
                            parts.append(lines[e_line][:e_col])
                            callee_name = "".join(parts).replace('\n', ' ').strip()

                if callee_name:
                    callee_name = re.sub(r'\s+', '', callee_name)
                    if is_indirect and heuristic_reason == "unexposed_heuristic":
                        if re.match(r'^[a-zA-Z_]\w*$', callee_name):
                            is_indirect = False
            else:
                line_content = ""

            if is_indirect:
                fp_results.append({
                    "callee_name": callee_name if callee_name else "indirect_call",
                    "line": node.location.line,
                    "line_content": line_content,
                    "containing_function": func_name,
                    "file": node.location.file.name if node.location.file else "",
                })
        else:
            # Direct call
            if ref:
                direct_callees.add(ref.spelling)

    return fp_results, direct_callees


def find_function_calls(cursor, entry_point_name):
    # Map: Function Name -> Cursor
    function_definitions = {}
    
    # Cache for file contents
    file_contents = {}
    
    # First pass: Index all function definitions
    for node in cursor.walk_preorder():
        if node.kind == CursorKind.FUNCTION_DECL and node.is_definition():
             function_definitions[node.spelling] = node
    
    if entry_point_name not in function_definitions:
        print(f"Error: Entry point '{entry_point_name}' not found.", file=sys.stderr)
        return []

    # Set of visited functions to avoid cycles
    visited_functions = set()
    # Results list
    results = []

    # Stack: (Current Function Name, Call Path List)
    stack = [(entry_point_name, [entry_point_name])]
    
    while stack:
        current_func_name, current_path = stack.pop()
        
        if current_func_name in visited_functions:
            continue
        visited_functions.add(current_func_name)

        if current_func_name not in function_definitions:
            continue

        func_cursor = function_definitions[current_func_name]
        fp_results, direct_callees = _walk_function_body(
            func_cursor, current_func_name, file_contents
        )

        for r in fp_results:
            r["path"] = list(current_path)
        results.extend(fp_results)

        for target_name in direct_callees:
            if target_name in function_definitions and target_name not in visited_functions:
                stack.append((target_name, current_path + [target_name]))

    return results

def _parse_file(file_path, extra_args):
    """Parse a single file with libclang and return (translation_unit, function_defs_dict)."""
    index = _create_index()
    resource_include = get_clang_resource_dir()
    final_args = list(extra_args)
    if resource_include:
        final_args.append(f"-I{resource_include}")

    try:
        tu = index.parse(file_path, args=final_args)
    except Exception as e:
        print(f"Error parsing file {file_path}: {e}", file=sys.stderr)
        return None, {}

    if not tu:
        print(f"Error: Failed to create TU for {file_path}", file=sys.stderr)
        return None, {}

    func_defs = {}
    for node in tu.cursor.walk_preorder():
        if node.kind == CursorKind.FUNCTION_DECL and node.is_definition():
            func_defs[node.spelling] = node

    return tu, func_defs


def _postprocess_results(results):
    """Add call_id to results and format final output."""
    func_counters = {}
    final_output = []
    for r in results:
        func_name = r["containing_function"]
        if func_name not in func_counters:
            func_counters[func_name] = 0
        func_counters[func_name] += 1

        order_num = func_counters[func_name]
        call_id = f"{func_name}.function_pointer_call.{order_num}"

        final_output.append({
            "function_name": r["containing_function"],
            "line_number": r["line"],
            "line_content": r["line_content"],
            "call_sequence": r["path"],
            "call_id": call_id,
            "callee_name": r["callee_name"]
        })

    return final_output


def analyze_file(file_path, entry_point, extra_args=[]):
    """Analyze a single file for function pointer calls (original behavior)."""
    index = _create_index()

    resource_include = get_clang_resource_dir()
    final_args = list(extra_args)
    if resource_include:
        final_args.append(f"-I{resource_include}")

    try:
        tu = index.parse(file_path, args=final_args)
    except Exception as e:
        print(f"Error parsing file: {e}", file=sys.stderr)
        return []

    if not tu:
         print("Error: Failed to create TranslationUnit", file=sys.stderr)
         return []

    results = find_function_calls(tu.cursor, entry_point)
    return _postprocess_results(results)


def analyze_files(target_file, entry_point, linked_files, extra_args=[]):
    """
    Multi-file iterative analysis for function pointer detection.
    
    Extends single-file analysis to walk across file boundaries using
    the linked source files from the Makefile's LINK variable.
    
    Algorithm:
      1. Parse all files (target + linked) with libclang independently.
      2. Build a global function lookup: func_name -> (file_path, cursor).
      3. BFS/DFS from entry_point, walking each function body for:
         - Function pointer calls (reported as results)
         - Direct callees (queued for further walking)
      4. Because each file is parsed independently, the walk naturally
         crosses file boundaries via the global lookup.
    
    Args:
        target_file: Path to the main target/harness file.
        entry_point: Name of the entry point function.
        linked_files: List of additional .c file paths to analyze.
        extra_args: Extra clang arguments (includes, defines).
    
    Returns:
        List of function pointer call result dicts (same format as analyze_file).
    """
    all_fp_results = []
    visited_functions = set()
    file_contents = {}

    # Parse all files upfront and build global function lookup
    all_files = [target_file] + [f for f in linked_files if f != target_file]
    global_func_lookup = {}  # func_name -> (file_path, cursor)

    for fpath in all_files:
        _, func_defs = _parse_file(fpath, extra_args)
        for fname, cursor in func_defs.items():
            if fname not in global_func_lookup:
                global_func_lookup[fname] = (fpath, cursor)

    # Check entry point exists
    if entry_point not in global_func_lookup:
        print(f"Error: Entry point '{entry_point}' not found in any file.", file=sys.stderr)
        return []

    # BFS/DFS from entry point
    stack = [(entry_point, [entry_point])]

    while stack:
        func_name, call_path = stack.pop()

        if func_name in visited_functions:
            continue
        visited_functions.add(func_name)

        if func_name not in global_func_lookup:
            continue

        _, func_cursor = global_func_lookup[func_name]
        fp_results, direct_callees = _walk_function_body(
            func_cursor, func_name, file_contents
        )

        # Attach path info to FP results
        for r in fp_results:
            r["path"] = list(call_path)
        all_fp_results.extend(fp_results)

        # Queue direct callees (cross-file resolution via global lookup)
        for callee in direct_callees:
            if callee not in visited_functions and callee in global_func_lookup:
                stack.append((callee, call_path + [callee]))

    return _postprocess_results(all_fp_results)


def analyze_from_makefile(file_path, entry_point, makefile_path):
    with open(makefile_path, "r", encoding="utf-8") as makefile:
        makefile_content = makefile.read()

    harness_dir = os.path.dirname(os.path.abspath(makefile_path))
    default_root = os.environ.get("AUTOUP_ROOT_DIR")
    extra_args = build_analysis_args(
        makefile_content,
        harness_dir,
        default_root=default_root,
    )
    linked_files = resolve_linked_source_files(
        makefile_content,
        harness_dir,
        default_root=default_root,
    )

    if linked_files:
        return analyze_files(file_path, entry_point, linked_files, extra_args)
    return analyze_file(file_path, entry_point, extra_args)

def main():
    if len(sys.argv) < 4:
        print("Usage: python3 find-function-pointers.py <file_path> <entry_point> <makefile_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    entry_point = sys.argv[2]
    makefile_path = sys.argv[3]

    results = analyze_from_makefile(file_path, entry_point, makefile_path)

    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
