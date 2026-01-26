#!/usr/bin/env python3
import sys
import json
import clang.cindex
from clang.cindex import CursorKind, TypeKind

def get_diagnostics(translation_unit):
    return [d.spelling for d in translation_unit.diagnostics]

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
                else:
                    # Look at children to find the callee expression
                    children = list(node.get_children())
                    if children:
                        callee = children[0]
                        if callee.kind in (CursorKind.MEMBER_REF_EXPR, CursorKind.ARRAY_SUBSCRIPT_EXPR):
                            is_indirect = True

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
                    else:
                        line_content = ""

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

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 find-function-pointers.py <file_path> <entry_point> [clang_args...]")
        sys.exit(1)

    file_path = sys.argv[1]
    entry_point = sys.argv[2]
    extra_args = sys.argv[3:]

    index = clang.cindex.Index.create()
    
    # We don't have compilation flags, so we rely on heuristic parsing.
    # might need to add some basic includes or defines if parsing fails badly.
    try:
        tu = index.parse(file_path, args=extra_args)
    except Exception as e:
        print(f"Error parsing file: {e}", file=sys.stderr)
        sys.exit(1)
        
    if not tu:
         print("Error: Failed to create TranslationUnit", file=sys.stderr)
         sys.exit(1)

    results = find_function_calls(tu.cursor, entry_point)
    
    # Clean up output to match requirements exactly
    # Requirement: "each json entry contains the function name and line number of the function pointer call, and the call sequences (a -> b ->c)"
    # My result has "function" (name of called ptr var?), "line", "path".
    
    final_output = []
    for r in results:
        final_output.append({
            "function_name": r["containing_function"],
            "line_number": r["line"],
            "line_content": r["line_content"],
            "call_sequence": r["path"]
        })

    print(json.dumps(final_output, indent=2))

if __name__ == "__main__":
    main()
