#!/usr/bin/env python3
import sys
import json
import re
import shutil
import subprocess
import os
import clang.cindex
from clang.cindex import CursorKind, TypeKind

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

    entry_cursor = function_definitions[entry_point_name]
    
    # Set of visited functions to avoid cycles
    visited_functions = set()
    # Results list
    results = []

    # Stack: (Current Function Name, Call Path List)
    # Path is a list of strings: ["entry", "intermediate1", ...]
    stack = [(entry_point_name, [entry_point_name])]
    
    while stack:
        current_func_name, current_path = stack.pop()
        
        if current_func_name in visited_functions:
            continue
        visited_functions.add(current_func_name)

        if current_func_name not in function_definitions:
            # Should not happen if we only push known functions, but good safety
            continue

        func_cursor = function_definitions[current_func_name]

        # Iterate over calls in the current function body
        for node in func_cursor.walk_preorder():
            if node.kind == CursorKind.CALL_EXPR:
                # Check if it is a function pointer call
                # A direct call usually has a referenced cursor pointing to a FUNCTION_DECL
                ref = node.referenced
                
                # If ref is None or it's not a function decl, it might be a function pointer
                # However, libclang usually resolves direct calls.
                # If it's a function pointer call, the 'referenced' might be a VAR_DECL or PARM_DECL (the pointer itself)
                # or None if it's a complex expression.
                
                is_indirect = False
                heuristic_reason = None
                callee_name = node.spelling
                
                # Heuristic: 
                # 1. If we can resolve the reference and it's NOT a function declaration, it's a function pointer.
                #    (e.g. calls to parameters, local variables, or known global pointers)
                # 2. If we CANNOT resolve the reference (ref is None), we look at the syntax (Callee Kind).
                #    - MEMBER_REF_EXPR (struct access) -> Likely function pointer (e.g. ops->recv())
                #    - ARRAY_SUBSCRIPT_EXPR -> Likely array of function pointers
                #    - UNEXPOSED_EXPR -> Ambiguous. Usually an unresolved function call (missing header). Treat as Direct to avoid FP.
                
                is_indirect = False
                if ref:
                    if ref.kind != CursorKind.FUNCTION_DECL:
                        is_indirect = True
                        heuristic_reason = "ref_not_func"
                else:
                    # Look at children to find the callee expression
                    children = list(node.get_children())
                    if children:
                        callee = children[0]
                        if callee.kind in (CursorKind.MEMBER_REF_EXPR, CursorKind.ARRAY_SUBSCRIPT_EXPR):
                            is_indirect = True
                            heuristic_reason = "syntax"
                        elif callee.kind == CursorKind.UNEXPOSED_EXPR and not node.spelling:
                            # Heuristic: If it's UNEXPOSED and has no name, it's likely a complex call
                            # (like ptr->func) that failed full parsing due to missing headers.
                            # We treat this as indirect.
                            is_indirect = True
                            heuristic_reason = "unexposed_heuristic"

                if is_indirect:
                    # Found a function pointer call
                    
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
                        if 0 <= line_idx < len(lines):
                            line_content = lines[line_idx].strip()
                        else:
                            line_content = ""
                            
                        # If callee_name is empty (common with UNEXPOSED_EXPR heuristic), extract it from source
                        if not callee_name and 'callee' in locals():
                            # Extract source for the callee expression
                            start = callee.extent.start
                            end = callee.extent.end
                            if start.file and start.file.name == fname:
                                 # 1-based to 0-based
                                 s_line = start.line - 1
                                 s_col = start.column - 1
                                 e_line = end.line - 1
                                 e_col = end.column - 1
                                 
                                 if s_line == e_line:
                                     callee_name = lines[s_line][s_col:e_col]
                                 else:
                                     # Multi-line expression, just take it all
                                     # (Simplification: join lines)
                                     parts = []
                                     parts.append(lines[s_line][s_col:])
                                     for k in range(s_line+1, e_line):
                                         parts.append(lines[k])
                                     parts.append(lines[e_line][:e_col])
                                     callee_name = "".join(parts).replace('\n', ' ').strip()
                                     
                        # Clean up whitespace
                        if callee_name:
                            callee_name = re.sub(r'\s+', '', callee_name)
                            
                            # Verification: If we inferred indirect via UNEXPOSED_EXPR heuristic (or just missing ref),
                            # check if the extracted name actually looks like an indirect call.
                            # If it's just a simple identifier (e.g. "func_name"), it's likely a direct call
                            # to an undeclared function, not a function pointer.
                            if is_indirect and heuristic_reason == "unexposed_heuristic":
                                # Regular expression for a simple C identifier
                                if re.match(r'^[a-zA-Z_]\w*$', callee_name):
                                    # It's a direct call
                                    is_indirect = False
                                else:
                                    pass
                    else:
                        line_content = ""

                    if is_indirect:
                        results.append({
                            "callee_name": callee_name if callee_name else "indirect_call",
                            "line": node.location.line,
                            "line_content": line_content,
                            "path": current_path,
                            "containing_function": current_func_name
                        })
                    
                else:
                    # Direct call, recurse
                    if ref:
                        target_name = ref.spelling
                        if target_name in function_definitions and target_name not in visited_functions:
                            stack.append((target_name, current_path + [target_name]))

    return results

def analyze_file(file_path, entry_point, extra_args=[]):
    index = clang.cindex.Index.create()
    
    # We don't have compilation flags, so we rely on heuristic parsing.
    # might need to add some basic includes or defines if parsing fails badly.
    
    # helper to inject standard headers
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
    
    # Post-process to add call_id
    # Sort results by containing_function to count order
    # Note: results are generally in traversal order.
    
    # We need to group by containing_function and assign incremental IDs
    # Map: function_name -> count
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

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 find-function-pointers.py <file_path> <entry_point> [clang_args...]")
        sys.exit(1)

    file_path = sys.argv[1]
    entry_point = sys.argv[2]
    extra_args = sys.argv[3:]

    results = analyze_file(file_path, entry_point, extra_args)

    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
