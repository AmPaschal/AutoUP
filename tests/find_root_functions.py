#!/usr/bin/env python3
"""
Script to identify root-level functions in a C file.

Root-level functions are functions that are not called by any other function
within the same file. These serve as entry points from which all other 
functions in the file should be reachable.

This script uses a regex-based approach to extract function definitions and
function calls, which works without requiring full preprocessing or header files.

Usage:
    python find_root_functions.py <path_to_c_file>
    python find_root_functions.py <path_to_c_file> -v  # verbose mode
    python find_root_functions.py <path_to_c_file> --json  # JSON output
"""

import sys
import os
import re
import argparse
from typing import Set, Dict, List, Tuple, Optional


def remove_comments(code: str) -> str:
    """Remove C-style comments (/* */ and //) from code."""
    result = []
    i = 0
    in_string = False
    in_char = False
    
    while i < len(code):
        # Handle string literals
        if code[i] == '"' and not in_char:
            if not in_string:
                in_string = True
                result.append(code[i])
            elif i > 0 and code[i-1] != '\\':
                in_string = False
                result.append(code[i])
            else:
                result.append(code[i])
            i += 1
            continue
        
        # Handle char literals
        if code[i] == "'" and not in_string:
            if not in_char:
                in_char = True
                result.append(code[i])
            elif i > 0 and code[i-1] != '\\':
                in_char = False
                result.append(code[i])
            else:
                result.append(code[i])
            i += 1
            continue
        
        if in_string or in_char:
            result.append(code[i])
            i += 1
            continue
        
        # Handle multi-line comments
        if i < len(code) - 1 and code[i:i+2] == '/*':
            end = code.find('*/', i + 2)
            if end != -1:
                # Preserve newlines for line counting
                comment = code[i:end+2]
                result.append('\n' * comment.count('\n'))
                i = end + 2
            else:
                i += 1
            continue
        
        # Handle single-line comments
        if i < len(code) - 1 and code[i:i+2] == '//':
            end = code.find('\n', i)
            if end != -1:
                result.append('\n')
                i = end + 1
            else:
                break
            continue
        
        result.append(code[i])
        i += 1
    
    return ''.join(result)


def remove_strings(code: str) -> str:
    """Remove string contents while preserving structure."""
    result = []
    i = 0
    
    while i < len(code):
        if code[i] == '"':
            result.append('"')
            i += 1
            # Skip until end of string
            while i < len(code):
                if code[i] == '\\' and i + 1 < len(code):
                    i += 2
                    continue
                if code[i] == '"':
                    result.append('"')
                    i += 1
                    break
                if code[i] == '\n':
                    result.append('\n')
                i += 1
        elif code[i] == "'":
            result.append("'")
            i += 1
            while i < len(code) and code[i] != "'":
                if code[i] == '\\' and i + 1 < len(code):
                    i += 2
                    continue
                i += 1
            if i < len(code):
                result.append("'")
                i += 1
        else:
            result.append(code[i])
            i += 1
    
    return ''.join(result)


def find_function_definitions(code: str) -> List[Tuple[str, int, int]]:
    """
    Find all function definitions in the code.
    Returns a list of tuples: (function_name, start_line, end_line)
    
    Uses brace matching to find function boundaries.
    """
    # Remove comments and strings first
    clean_code = remove_comments(code)
    clean_code = remove_strings(clean_code)
    
    functions = []
    lines = clean_code.split('\n')
    
    # Keywords that can have braces but aren't functions
    non_func_keywords = {'if', 'else', 'while', 'for', 'switch', 'do', 
                         'struct', 'union', 'enum', 'typedef'}
    
    brace_depth = 0
    in_function = False
    current_func = None
    func_start = 0
    signature_buffer = ""
    
    for line_num, line in enumerate(lines, 1):
        open_braces = line.count('{')
        close_braces = line.count('}')
        
        if not in_function:
            signature_buffer += " " + line
            
            if '{' in line:
                # Try to extract function name
                # Pattern: type name(params) {
                match = re.search(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)\s*\{', signature_buffer)
                
                if match:
                    func_name = match.group(1)
                    if func_name not in non_func_keywords:
                        in_function = True
                        current_func = func_name
                        func_start = line_num
                        brace_depth = open_braces - close_braces
                        
                        if brace_depth == 0:
                            functions.append((current_func, func_start, line_num))
                            in_function = False
                            current_func = None
                        
                        signature_buffer = ""
                        continue
                
                # Not a function, track braces anyway
                brace_depth = open_braces - close_braces
                if brace_depth == 0:
                    signature_buffer = ""
        else:
            brace_depth += open_braces - close_braces
            
            if brace_depth <= 0:
                functions.append((current_func, func_start, line_num))
                in_function = False
                current_func = None
                brace_depth = 0
                signature_buffer = ""
    
    return functions


def find_function_calls(func_body: str, defined_functions: Set[str], calling_func: str) -> Set[str]:
    """
    Find all function calls in a function body.
    Only returns calls to functions that are defined in the same file.
    """
    # Clean the body
    body = remove_comments(func_body)
    body = remove_strings(body)
    
    calls = set()
    
    # Pattern for function calls: identifier immediately followed by (
    # Must NOT be preceded by . or -> (method/struct member)
    # Must NOT have = immediately before (function pointer assignment)
    
    call_pattern = re.compile(r'''
        (?<![.\w])              # Not preceded by . or word char
        (?<!->)                 # Not preceded by ->
        ([a-zA-Z_][a-zA-Z0-9_]*)  # Function name
        \s*                       # Optional whitespace  
        \(                        # Opening parenthesis
    ''', re.VERBOSE)
    
    keywords = {'if', 'while', 'for', 'switch', 'sizeof', 'typeof',
               'return', 'case', '__attribute__', '__asm__', 
               '__extension__', '__builtin_offsetof', 'defined',
               'else', 'do', 'struct', 'union', 'enum', 'typedef',
               'goto', 'break', 'continue', 'default', '__builtin_va_list'}
    
    for match in call_pattern.finditer(body):
        func_name = match.group(1)
        
        # Skip keywords
        if func_name in keywords:
            continue
        
        # Skip self-recursion tracking (still valid, but not for call graph)
        if func_name == calling_func:
            continue
        
        # Only count calls to functions defined in this file
        if func_name in defined_functions:
            # Check if this is actually a call vs declaration/pointer
            # Look at what comes before
            start = match.start()
            preceding = body[max(0, start-50):start].strip()
            
            # Skip if it looks like a function pointer assignment
            # Pattern: .field = func or ->field = func
            if re.search(r'[.>]\s*[a-zA-Z_][a-zA-Z0-9_]*\s*=\s*[&]?\s*$', preceding):
                continue
            
            calls.add(func_name)
    
    return calls


def find_root_functions(filepath: str) -> Dict:
    """
    Find root-level functions in a C file.
    
    Returns a dict with:
        - root_functions: list of function names that are not called by any other function
        - all_functions: list of all defined functions
        - call_graph: dict mapping each function to the functions it calls
        - function_info: detailed info about each function
    """
    with open(filepath, 'r') as f:
        code = f.read()
    
    # Find all function definitions
    function_defs = find_function_definitions(code)
    defined_functions = {name for name, _, _ in function_defs}
    
    # Build call graph
    call_graph = {}
    lines = code.split('\n')
    
    for func_name, start_line, end_line in function_defs:
        # Extract the function body (from the original code)
        func_body = '\n'.join(lines[start_line-1:end_line])
        
        # Find calls within this function
        calls = find_function_calls(func_body, defined_functions, func_name)
        call_graph[func_name] = calls
    
    # Find functions that are called by other functions in the file
    called_functions: Set[str] = set()
    for caller, callees in call_graph.items():
        called_functions.update(callees)
    
    # Root functions are defined but never called within the file
    root_functions = defined_functions - called_functions
    
    # Build detailed function info
    function_info = {}
    for func_name, start_line, end_line in function_defs:
        called_by = [
            caller for caller, callees in call_graph.items()
            if func_name in callees
        ]
        
        function_info[func_name] = {
            'line': start_line,
            'end_line': end_line,
            'is_root': func_name in root_functions,
            'calls': sorted(list(call_graph.get(func_name, set()))),
            'called_by': sorted(called_by)
        }
    
    return {
        'root_functions': sorted(list(root_functions)),
        'all_functions': sorted(list(defined_functions)),
        'call_graph': {k: sorted(list(v)) for k, v in call_graph.items()},
        'function_info': function_info
    }


def print_results(results: Dict, verbose: bool = False):
    """Print the results in a readable format."""
    if 'error' in results:
        print(f"Error: {results['error']}")
        return
    
    print("=" * 60)
    print("ROOT-LEVEL FUNCTIONS (Entry Points)")
    print("=" * 60)
    
    for func in results['root_functions']:
        info = results['function_info'].get(func, {})
        line = info.get('line', '?')
        end_line = info.get('end_line', '?')
        print(f"  • {func} (lines {line}-{end_line})")
        
        if verbose:
            calls = info.get('calls', [])
            if calls:
                print(f"    └── calls: {', '.join(calls)}")
    
    print()
    print(f"Total functions defined: {len(results['all_functions'])}")
    print(f"Root-level functions: {len(results['root_functions'])}")
    
    if verbose:
        print()
        print("=" * 60)
        print("ALL FUNCTIONS")
        print("=" * 60)
        
        for func in sorted(results['all_functions']):
            info = results['function_info'].get(func, {})
            calls = info.get('calls', [])
            called_by = info.get('called_by', [])
            line = info.get('line', '?')
            
            marker = "[ROOT]" if info.get('is_root') else ""
            print(f"\n{func} (line {line}) {marker}")
            
            if called_by:
                print(f"  ← called by: {', '.join(called_by)}")
            else:
                print("  ← called by: (none - this is a root function)")
            
            if calls:
                print(f"  → calls: {', '.join(calls)}")
            else:
                print("  → calls: (none)")


def main():
    parser = argparse.ArgumentParser(
        description='Find root-level functions in a C file. '
                    'Root-level functions are functions not called by any other function in the file.'
    )
    parser.add_argument('filepath', help='Path to the C file to analyze')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed call graph information')
    parser.add_argument('--json', action='store_true',
                        help='Output results as JSON')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.filepath):
        print(f"Error: File '{args.filepath}' not found", file=sys.stderr)
        sys.exit(1)
    
    results = find_root_functions(args.filepath)
    
    if args.json:
        import json
        print(json.dumps(results, indent=2))
    else:
        print_results(results, verbose=args.verbose)


if __name__ == '__main__':
    main()
