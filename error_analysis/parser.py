import subprocess
from pathlib import Path
import os
import re
from hashlib import sha256
from uuid import uuid4
from bs4 import BeautifulSoup
from pprint import pp as pprint
from collections import defaultdict
import json
import sys


def run_command(command, cwd=None):
    """Runs a shell command and handles errors."""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True, capture_output=True, text=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise Exception(f"Command failed: {command}\n")

def convert_c_struct_to_json(struct_str):
    """
    Converts a C-struct string into a Python-style struct string, generated using LLM
    """
    json_str = re.sub('\n', '', struct_str)

    # Step 1: Remove 'u' suffix from unsigned integers
    json_str = re.sub(r'(\d+)u(?:ll)?', r'\1', json_str)

    # Step 2: Convert C-style arrays of ints or chars to JSON arrays
    json_str = re.sub(r'{\s*((?:[0-9\-]|\'.*\')+(?:\s*,\s*(?:[0-9\-]|\'.*\')+)*)\s*}', r'[\1]', json_str)
    
    # Step 3: Replace field names (.field=) with JSON keys ("field":)
    json_str = re.sub(r'\.([$a-zA-Z_][a-zA-Z0-9_]*)\s*=', r'"\1":', json_str)\

    # Step 4: Convert C chars to ints for easier parsing
    json_str = re.sub(r'\'(.)\'', str(ord(r'\1'[0])), json_str)

    # Step 5: Remove type casts like ((type*)NULL)
    json_str = re.sub(r'((?:\(\([^)]+\)\s*)?NULL\)?(?: \+ \d+)?)', r'"\1"', json_str)
    
    # Step 5.5: Deal with this invalid-XXX value that CBMC can sometimes assign to pointers by treating it like NULL
    json_str = re.sub(r'INVALID(-\d+)?', '"NULL"', json_str)

    # Step 6: Handle enum values (/*enum*/VALUE)
    json_str = re.sub(r'/\*enum\*/([A-Z_][A-Z0-9_]*)', r'"\1"', json_str)
    
    # Step 7: Turn dynamic object pointers into strings:
    json_str = re.sub(r'(&dynamic_object(?:\$\d)?)', r'"\1"', json_str)

    # Custom parsing logic for struct arrays, as they're too complex to deal with using regex
    open_bracket_stack = []
    for i, char in enumerate(json_str):
        if char == '{':
            # Check for the next non-whitespace character
            j = i + 1
            while j < len(json_str) and json_str[j].isspace():
                j += 1
            # If this is an array of objects
            if json_str[j] == '{':
                open_bracket_stack.append((i, True)) # True means we want to replace this with [] when we find the close
            else:
                open_bracket_stack.append((i, False))
        elif char == '}':
            last_open_bracket_idx, should_replace = open_bracket_stack.pop()
            if should_replace:
                json_str = json_str[:last_open_bracket_idx] + '[' + json_str[last_open_bracket_idx + 1:i] + ']' + json_str[i + 1:]
    
    # Try to parse and return the result
    try:
        parsed = json.loads(json_str)
        return parsed
    except json.JSONDecodeError as e:
        print(f"Conversion failed: {e}")
        print(f"Current JSON string: {json_str}")
        return None

def get_error_cluster(error_msg):
    if re.match(r'memcpy source region readable', error_msg):
        return 'memcpy_src'
    elif re.match(r'memcpy destination region readable', error_msg):
        return 'memcpy_dest'
    elif re.match(r"memcpy src/dst overlap", error_msg):
        return "memcpy_overlap"
    elif re.match(r'arithmetic overflow', error_msg):
        return 'arithmetic_overflow'
    elif re.match(r"dereference failure: pointer NULL", error_msg):
        return 'deref_null'
    elif re.match(r"dereference failure: pointer outside object bounds in .*\[", error_msg):
        return 'deref_arr_oob'
    elif re.match(r"dereference failure: pointer outside object bounds in .*->", error_msg):
        return 'deref_obj_oob'
    else:
        return 'misc'

def convert_python_to_c_struct(json_obj):
    """
    Converts a Python-style dict back into the original C string (minus a few small things), generated using LLM
    """
    def format_value(value):
        if isinstance(value, str):
            # Don't escape quotes bc true strings should basically never be a data type
            # escaped_value = value.replace('"', '')
            return value
        elif isinstance(value, bool):
            # Convert to C boolean (true/false)
            return 'true' if value else 'false'
        elif isinstance(value, (int, float)):
            # Convert numbers directly
            return str(value)
        elif value is None:
            # Represent null value
            return 'NULL'
        elif isinstance(value, list):
            # Format arrays
            elements = [format_value(item) for item in value]
            return '{ ' + ', '.join(elements) + ' }'
        elif isinstance(value, dict):
            # Recursively format nested objects
            return convert_python_to_c_struct(value)
        else:
            raise TypeError(f"Unsupported type: {type(value)}")
    
    # Start building the C struct string
    c_struct = "{"
    
    if isinstance(json_obj, list):
        elements = [format_value(item) for item in json_obj]
        return '{ ' + ', '.join(elements) + ' }'
    # Add each key-value pair
    elements = []
    for key, value in json_obj.items():
        formatted_value = format_value(value)
        elements.append(f".{key} = {formatted_value}")
    
    c_struct += ', '.join(elements)
    # Close the struct
    c_struct += "}"
    
    return c_struct

def analyze_error_report(errors_div, report_dir):
    error_clusters = defaultdict(list)
    undefined_funcs = []

    # Traverse all <li> elements inside the errors div
    for li in errors_div.find_all("li", recursive=True):
        text = li.text.strip()

        # Get undefined funcs
        if text.startswith("Other failures"):
            undef_funcs = li.find_all("li", recursive=True)
            for func in undef_funcs:
                func_name = re.match(r'(.*)\.no-body\.(.*)', func.text.strip()).groups()
                undefined_funcs.append(f"{func_name[1]} @ {func_name[0]}")

        # Get files
        elif re.match(r'^File (<builtin\-library\-.*>|.*\.(c|h))', text):
            if re.match(r'File <builtin\-library\-.*>', text):
                is_built_in = True
            else:
                is_built_in = False
                
            # Get each function erroring in this file
            for func in li.ul.find_all('li', recursive=False):
                func_name = re.search(r'Function ([a-zA-Z0-9_]+)', text).group(1)
                # Get the error description (text content after trace link)
        
            # Get the li holding line info
            error_report = li.find('li')
            while error_report != None:
                line_num = re.search(r'\s*(Line \d+)',error_report.text).group(1)
                error_msgs = set(re.findall(r'\[trace\]\s*((?:[^\s]+\s?)+)\s*',error_report.text))

                if len(error_msgs) > 1:
                    is_null_pointer_deref = any(re.match(r'dereference failure: pointer NULL', msg) for msg in error_msgs)
                else:
                    is_null_pointer_deref = False

                for error_msg in error_msgs:

                    # Skip pointer relations
                    if 'pointer relation' in error_msg:
                        continue
                    
                    # Derefs lead to a bunch of different errors and we'd rather focus on 
                    if is_null_pointer_deref and 'dereference failure' in error_msg and not "pointer NULL" in error_msg:
                        continue
                    
                    trace_link = error_report.find("a", text='trace')
                    trace_href = os.path.join(report_dir, trace_link['href'] if trace_link else None)
                    error_hash = sha256(f"{line_num} of {func_name}: {error_msg.strip()}".encode()).hexdigest(), # Create a unique ID for the error by taking a hash of the complete error info
                    error_obj = {
                        "id": error_hash,
                        "function": func_name,
                        "line": line_num,
                        "msg": error_msg.strip(),
                        'trace': trace_href,
                        "is_built_in": is_built_in
                    }

                    cluster = get_error_cluster(error_obj['msg'])
                    error_clusters[cluster].append(error_obj)
                error_report = error_report.find_next_sibling('li')
    return error_clusters, undefined_funcs

def analyze_traces(extracted_errors, json_path):
    with open(os.path.join(json_path, "viewer-trace.json"), 'r') as file:
        error_traces = json.load(file)
    
    html_files = dict()
    for cluster, errors in extracted_errors.items():
        for i, error in enumerate(errors):
            trace = error.pop('trace')
            is_built_in = error.pop('is_built_in')
            with open(trace, "r") as f:
                soup = BeautifulSoup(f, "html.parser")

            trace_key = os.path.basename(trace).replace(".html", "")
            var_trace = error_traces['viewer-trace']['traces'][trace_key]
            harness_vars = defaultdict(dict)
            for trace in var_trace:
                
                # Skip over lines that are not variable assignments and that are not in the harness file (where preconditions can be applied)
                # Null function indicates global var assignment which we need
                if not (trace['location']['function'] == None or re.match(r'.*_harness.c', trace['location']['file'])) or trace['kind'] != 'variable-assignment': 
                    continue

                func = trace['location']['function']
                if func == None:
                    func = 'global'

                root_var = trace['detail']['lhs-lexical-scope'].split('::')[-1]
                if root_var.startswith('dynamic_object'):
                    root_var = '&' + root_var
                elif root_var.startswith('tmp_if_expr'):
                    continue

                actual_var = trace['detail']['lhs']
                if "return_value" in actual_var:
                    continue

                if actual_var.startswith('dynamic_object'):
                    actual_var = '&' + actual_var

                if trace["location"]["function"] == 'malloc' or trace["location"]["function"] == 'memcpy':
                    continue

                value = trace['detail']['rhs-value']
                if '{' in value:
                    value = convert_c_struct_to_json(value)
                
                elif value.startswith('dynamic_object'):
                    value = '&' + value

                # If we are assigning to a subfield, rather than the var itself
                if root_var != actual_var:
                    keys = actual_var.split('.')
                    curr_scope = harness_vars[func]
                    if re.sub(r'\[\d+\]', "", keys[0]) in harness_vars['global']:
                        curr_scope = harness_vars['global']

                    for j, key in enumerate(keys):
                        if '[' in key: # If this is also an array index
                            root_key, idx = re.match(fr'(.*)\[(\d+)\]', key).groups()
                            idx = int(idx)
                            # Root key must already exist if we're writing to an index
                            if j != len(keys) - 1: 
                                curr_scope = curr_scope[root_key][idx]
                            else:
                                curr_scope[root_key][idx] = value
                            continue
                        else:
                            if not key in curr_scope:
                                if j != len(keys) - 1: 
                                    curr_scope[key] = dict()
                                    curr_scope = curr_scope[key]
                                else:
                                    curr_scope[key] = value
                            else:
                                if j != len(keys) - 1: 
                                    curr_scope = curr_scope[key]
                                else:
                                    curr_scope[key] = value
                
                elif root_var in harness_vars['global']:
                    harness_vars['global'][root_var] = value
                elif not root_var in harness_vars[func]:
                    harness_vars[func][root_var] = value

            for func, func_vars in harness_vars.items():
                for key, var in func_vars.items():
                    if isinstance(var, dict) or isinstance(var, list):
                        harness_vars[func][key] = re.sub(r'\s+', ' ', convert_python_to_c_struct(var))
            errors[i]['harness_vars'] = harness_vars

            func_calls = soup.find_all("div", class_="function-call")[1:] # Skip over the CPROVER_initialize call
            # Get the trace files for each function call so we can extract the function definitions
            # Built-in functions have no "a" tag so they are ignored
            for call in func_calls:
                called_func =  call.find(class_ = "step").find(class_="cbmc").find('a')
                if called_func:
                    func_name = called_func.text
                    origin_file = called_func['href']
                    if not func_name in html_files:
                        html_files[func_name] = origin_file

            # If the error occured in a built-in function, find the actual line it occured in
            if is_built_in:
                builtin_func_name, error_msg = error['function'], error['msg']
                error_div = soup.find_all("div", class_="cbmc", string=re.compile(fr'{re.escape(error_msg)}')) # Should be unique
                if len(error_div) > 1:
                    raise ValueError("Why are there 2 of you")
                header = error_div[0].find_parent("div", class_="function").find("div", class_="function-call").find("div", class_="header")
                true_func_name, line_num = re.match(r'Step \d+: Function (.*), File .*, (Line \d+)', header.text).groups()
                error['function'] = f"{true_func_name} (in {builtin_func_name})"
                error['line'] = line_num

    return html_files

def extract_func_definitions(html_files, report_dir, undefined_funcs):
    func_text = dict()
    stub_text = dict()
    harness_file = os.path.basename(html_files['harness'].split('#')[0])
    global_vars = []
    macros = []
    for func_name, trace_path in html_files.items():
        if func_name in undefined_funcs:
            func_text[func_name] = "Undefined"
            continue

        file_path = os.path.join(report_dir, Path('traces', trace_path))
        real_path, line_num = file_path.split('#')
        with open(real_path, "r") as f:
            soup = BeautifulSoup(f, "html.parser")
        
        if os.path.basename(real_path) == harness_file and func_name == 'harness':
            global_defs = soup.find_all(string=re.compile(r'\s*\d+\s*(?:extern|\#define)')) #This only actually matches the start of the string
            for definition in global_defs:
                full_def = definition.parent.text.strip()
                if '#define' in full_def:
                    macros.append(re.match(r'\s*\d+\s*(#define .*)', full_def).group(1))
                elif 'extern' in full_def:
                    global_vars.append(re.match(r'\d+\s+extern\s+(.*);', full_def).group(1))
                else:
                    raise Exception(f"Unexpected global variable definition: {full_def}")

        func_definition = soup.find('div', id=str(line_num)) # Try to find the function definition line

        if func_definition:
            full_func_text = ""

            # Look for the opening curly brace
            line = func_definition
            while not '{' in line.text or ';' in line.text:
                full_func_text += line.text.strip() + '\n'
                # print(line.text.strip())
                line = line.next_sibling

            full_func_text += line.text.strip() + '\n'
            # print(line.text.strip())
            # These are typically static functions without an immediate definition
            if ';' in line.text:
                continue
            num_unmatched_braces = 1

            while num_unmatched_braces != 0:
                line = line.next_sibling


                 # Remove the comment from each line so we don't count potentially count brackets in comments
                if '//' in line.text:
                    text_to_check = line.text.split('//', 1)[0]
                else:
                    text_to_check = line.text
                
                # Remove comments as to not give any "hints" from our pre-written harness
                if os.path.basename(real_path) == harness_file:
                    line_text = re.sub(r'//.*', '', line.text)
                else:
                    line_text = line.text


                if '{' in text_to_check:
                    num_unmatched_braces += 1
                if '}' in text_to_check:
                    num_unmatched_braces -= 1
                full_func_text += line_text.strip() + '\n'
                # print(line.text.strip())
            
            # If it's a stub
            if os.path.basename(real_path) == harness_file and func_name != 'harness':
                stub_text[func_name] = re.sub(r' +', ' ', full_func_text)
            else:
                func_text[func_name] = re.sub(r' +', ' ', full_func_text)
        else:
            print("Failed to find matching function name for ")

    return func_text, stub_text, global_vars, macros

def extract_errors_and_payload(harness_name, tag_name):
    # Need to rename these because it's the only instance where the harness name is not the same as the file name
    if harness_name == '_rbuf_add':
        harness_name = '_rbuf_add2'
    root_path = Path("..","..","RIOT")
    harness_path = Path(root_path, "cbmc", "proofs", harness_name)
    html_report_dir = os.path.join(harness_path, Path("build", "report", "html"))
    json_report_dir = os.path.join(harness_path, Path("build", "report", "json"))
    # Check if we're currently on the same tag, this prevents issues with checking out due to harness changes
    curr_tag = run_command('git describe --exact-match --tags', cwd=root_path).strip()
    if curr_tag != tag_name:
        # Make sure we're on the right branch, then check out the commit with the tag
        run_command('git checkout AutoUP-test', cwd=root_path)
        if tag_name:
            try:
                run_command(f'git checkout {tag_name}', cwd=root_path)
            except:
                print("Invalid tag name")
                return


    # First run make
    run_command('make', cwd=harness_path)
    print (f"Make command completed")


    error_report = os.path.join(html_report_dir, "index.html")
    with open(error_report, "r") as f:
        soup = BeautifulSoup(f, "html.parser")
    
    errors_div = soup.find("div", class_="errors")
    error_clusters, undefined_funcs = analyze_error_report(errors_div, html_report_dir)
    if len(error_clusters) == 0:
        print("No error traces found")
        return {}
    elif "harness" in error_clusters:
        print("Found errors in harness, please ensure harness can actually run")
        raise Exception("Errors found in harness")

    html_files = analyze_traces(error_clusters, json_report_dir)
    print(f"Extracted {len(html_files)} trace files")
    func_text, stub_text, global_vars, macros = extract_func_definitions(html_files, html_report_dir, undefined_funcs)
    
    harness_info = {
        'harness_definition': func_text.pop('harness'),
    }
    
    if len(stub_text) > 0:
        harness_info['function_models'] = stub_text
    
    if len(global_vars) > 0:
        harness_info['global_vars'] = global_vars
    
    if len(macros) > 0:
        harness_info['macros'] = macros

    if not os.path.exists(f'./payloads_v2/{tag_name}'):
        os.makedirs(f'./payloads_v2/{tag_name}')

    with open(f'./payloads_v2/{tag_name}/{tag_name}_functions.json', 'w') as f:
        json.dump(func_text,f,indent=4)
    
    with open(f'./payloads_v2/{tag_name}/{tag_name}_harness.json', 'w') as f:
        json.dump(harness_info, f, indent=4)

    with open(f'./payloads_v2/{tag_name}/{tag_name}_errors.json', 'w') as f:
        json.dump(error_clusters, f, indent=4)

    return error_clusters

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <harness name> <tag name>")
        sys.exit(1)

    harness_name = sys.argv[1]
    tag_name = sys.argv[2] if len(sys.argv) == 3 else None
    extract_errors_and_payload(harness_name, tag_name)