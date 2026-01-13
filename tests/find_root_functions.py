#!/usr/bin/env python3
import sys
import os
import clang.cindex

import csv
import glob

def get_root_functions(file_path):
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' not found.", file=sys.stderr)
        return []

    try:
        index = clang.cindex.Index.create()
    except Exception as e:
        print(f"Error initializing clang: {e}", file=sys.stderr)
        return []

    # Parse the file
    tu = index.parse(file_path)

    # Map function name -> list of extents (start_offset, end_offset) where it is declared/defined
    func_ranges = {}
    defined_funcs = set()

    def visit_decls(cursor):
        # We only care about declarations in the target file
        try:
            if cursor.location.file and os.path.abspath(cursor.location.file.name) == os.path.abspath(file_path):
                if cursor.kind == clang.cindex.CursorKind.FUNCTION_DECL:
                    name = cursor.spelling
                    if name not in func_ranges:
                        func_ranges[name] = []
                    # Record the extent of the declaration/definition
                    # We use offsets for easy comparison
                    func_ranges[name].append((cursor.extent.start.offset, cursor.extent.end.offset))
                    
                    if cursor.is_definition():
                        defined_funcs.add(name)
        except:
            pass
        
        for child in cursor.get_children():
            visit_decls(child)

    visit_decls(tu.cursor)

    called_funcs = set()

    # Iterate over all tokens in the file to find usages
    try:
        tokens = list(tu.get_tokens(extent=tu.cursor.extent))
    except:
        return []
    
    for i, token in enumerate(tokens):
        if token.kind == clang.cindex.TokenKind.IDENTIFIER:
            name = token.spelling
            if name in defined_funcs:
                # Check if this token is inside one of the function's own declarations
                is_decl = False
                if name in func_ranges:
                    token_offset = token.extent.start.offset
                    for (start, end) in func_ranges[name]:
                        if start <= token_offset < end:
                            is_decl = True
                            break
                
                if not is_decl:
                    # It is a usage. Now check if it is a CALL.
                    # Heuristic: followed immediately by '('
                    if i + 1 < len(tokens):
                        next_token = tokens[i+1]
                        if next_token.spelling == '(':
                            called_funcs.add(name)

    # Root functions: defined but never used outside their own definition/proto
    root_funcs = defined_funcs - called_funcs
    return sorted(list(root_funcs))

import argparse

def main():
    parser = argparse.ArgumentParser(description="Find root-level functions in C files.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-f", "--file", help="Path to a single C file")
    group.add_argument("-d", "--directory", help="Path to a directory to recursively search for C files")
    parser.add_argument("-o", "--output", default="root_functions.csv", help="Output CSV file name (default: root_functions.csv)")

    args = parser.parse_args()

    files_to_process = []
    
    if args.file:
        if not os.path.isfile(args.file):
            print(f"Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)
        files_to_process.append(args.file)
    elif args.directory:
        if not os.path.isdir(args.directory):
            print(f"Error: Directory '{args.directory}' not found.", file=sys.stderr)
            sys.exit(1)
        # Find all .c files recursively
        files_to_process = glob.glob(os.path.join(args.directory, '**', '*.c'), recursive=True)
        
    output_csv = args.output
    print(f"Processing {len(files_to_process)} files... Output will be in '{output_csv}'")
    
    with open(output_csv, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['source_file', 'function_name'])
        
        for file_path in files_to_process:
            # print(f"Processing {file_path}...")
            roots = get_root_functions(file_path)
            for root in roots:
                writer.writerow([os.path.abspath(file_path), root])

if __name__ == "__main__":
    main()
