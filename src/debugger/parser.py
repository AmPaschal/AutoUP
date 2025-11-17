import subprocess
from pathlib import Path
import os
import re
from hashlib import sha256
from bs4 import BeautifulSoup
from collections import defaultdict
import json
import sys
from debugger.error_classes import CoverageError, PreconditionError

def run_command(command, cwd=None):

    """Runs a shell command and handles errors."""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True, capture_output=True, text=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Subprocess run failed with error {e}")
        raise Exception(f"Command failed: {command}\n")

def convert_c_struct_to_json(struct_str):
    """
    Converts a C-struct string into a Python-style struct string, generated using LLM
    """
    # Problem comes from a statically defined array of struct POINTERS

    json_str = re.sub('\n', '', struct_str)

    # Step 1: Remove 'u' suffix from unsigned integers
    json_str = re.sub(r'(\d+)u(?:ll)?', r'\1', json_str)

    # Step 2: Convert C-style arrays of ints or chars to JSON arrays
    json_str = re.sub(r'{\s*((?:[0-9\-]|\'.*\'|&.*)+(?:\s*,\s*(?:[0-9\-]|\'.*\'|&.*)+)*)\s*}', r'[\1]', json_str)
    
    # Step 3: Replace field names (.field=) with JSON keys ("field":)
    json_str = re.sub(r'\.([$a-zA-Z_][a-zA-Z0-9_]*)\s*=', r'"\1":', json_str)\

    # Step 4: Convert C chars to ints for easier parsing
    json_str = re.sub(r'\'(.)\'', str(ord(r'\1'[0])), json_str)

    # Step 5: Remove type casts like ((type*)NULL), and function ptr casts like 
    json_str = re.sub(r'((?:\(\([^)]+(?:\(\*\)\([^()]*\))?\)\s*)?NULL\)?(?: \+ \d+)?)', r'"\1"', json_str)
    
    # Step 5.5: Deal with this invalid-XXX value that CBMC can sometimes assign to pointers by treating it like NULL
    json_str = re.sub(r'INVALID(-\d+)?', '"NULL"', json_str)

    # Step 6: Handle enum values (/*enum*/VALUE)
    json_str = re.sub(r'/\*enum\*/([A-Z_][A-Z0-9_]*)', r'"\1"', json_str)
    
    # Step 7: Turn dynamic object pointers into strings:
    json_str = re.sub(r'(&[A-Za-z0-9_\$\.]+)', r'"\1"', json_str)

    # Step 8: Convert C-style booleans (true/false) to JSON booleans
    json_str = re.sub(r'(TRUE|FALSE)', lambda m: 'true' if m.group(0) == 'TRUE' else 'false', json_str)

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
    elif re.match(r'memcpy destination region writeable', error_msg):
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

def analyze_error_report(errors_div, report_dir, new_precon_lines=[]):
    error_clusters = defaultdict(dict)
    undefined_funcs = []

    # Traverse all <li> elements inside the errors div
    for li in errors_div.find_all("li", recursive=True):
        text = li.text.strip()

        # Get undefined funcs
        if text.startswith("Other failures"):
            undef_funcs = li.find_all("li", recursive=True)
            for func in undef_funcs:
                if 'recursion' in func.text: # No idea what this 'recursion' failure is but it causes an error on an edge case
                    continue
                func_name = re.match(r'(.*)\.no-body\.(.*)', func.text.strip()).groups()
                undefined_funcs.append(func_name[1])

        # Get files
        elif re.match(r'^File (<builtin\-library\-.*>|.*\.(c|h))', text):
            if re.match(r'File <builtin\-library\-.*>', text):
                is_built_in = True
            else:
                is_built_in = False
                
            # Get each function erroring in this file
            # for func in li.ul.find_all('li', recursive=False):
                
                # Get the error description (text content after trace link)
        
            # Get the li holding line info
            error_report = li.find('li')
            while error_report is not None:
                func_name = re.search(r'Function ([a-zA-Z0-9_]+)', error_report.text).group(1)

                if not is_built_in:
                    func_file_path = re.match(r'(?:\.+/)?((?:.*)\.(c|h))\.html', error_report.find("a")['href']).group(1) # Strip out the ./ and .html from this path
                else:
                    func_file_path = None

                for error_block in error_report.find('ul').find_all('li', recursive=False):


                    error_msgs = set(re.findall(r'\[trace\]\s*((?:[^\s]+\s?)+)\s*',error_block.text))

                    if len(error_msgs) > 1:
                        is_null_pointer_deref = any(re.match(r'dereference failure: pointer NULL', msg) for msg in error_msgs)
                    else:
                        is_null_pointer_deref = False

                    line_num = int(re.search(r'\s*Line (\d+)',error_block.text).group(1))

                    
                    if func_file_path != None and re.match(r'.*_harness.c', func_file_path):
                        if line_num in new_precon_lines:
                            # If this error was caused by a precondition that was added, then return an error to the LLM
                            # I think we can assume that this will always be the newest added precondition

                            new_errors = [re.match(r'\s*\[trace\]\s*((?:[^\s]+\s?)+)\s*',error_line.text).group(1).strip() for error_line in error_block.find('ul').find_all('li', recursive=False)]
                            raise PreconditionError(f"ERROR: Precondition inserted at line {line_num} introduced new errors to harness", errors=new_errors)


                        # Adjust line number if a precondition was added before this line
                        # Otherwise the same error could be reported twice, one line apart
                        for new_line in new_precon_lines:
                            if line_num > new_line:
                                line_num -= 1

                    for error_line in error_block.find('ul').find_all('li', recursive=False):
                        error_id = re.match(r'\s*\[<a href="./traces/(.+).html">trace</a>\]\s*', error_line.decode_contents()).group(1).strip()
                        error_msg = re.match(r'\s*\[trace\]\s*((?:[^\s]+\s?)+)\s*',error_line.text).group(1).strip()
                        trace_link = error_line.find("a", text='trace')
                        trace_href = os.path.join(report_dir, trace_link['href'] if trace_link else None)                    
                        # Skip pointer relations and redundant derefs
                        if ('pointer relation' in error_msg or 
                            (is_null_pointer_deref and 'dereference failure' in error_msg and "pointer NULL" not in error_msg)):
                            continue
                        
                        
                        error_obj = {
                            "function": func_name,
                            "line": line_num,
                            "msg": error_msg,
                            "id": error_id,
                            'trace': trace_href,
                            'file': func_file_path,
                            "is_built_in": is_built_in
                        }

                        cluster = get_error_cluster(error_obj['msg'])
                        error_clusters[cluster][error_id] = error_obj
                error_report = error_report.find_next_sibling('li')
    return error_clusters, undefined_funcs

def analyze_traces(extracted_errors, json_path, new_precon_lines=[]):
    with open(os.path.join(json_path, "viewer-trace.json"), 'r') as file:
        error_traces = json.load(file)
    
    html_files = dict()
    for errors in extracted_errors.values():
        errs_to_remap = dict()
        for error_hash, error in errors.items():
            trace_file = error.pop('trace')
            with open(trace_file, "r") as f:
                soup = BeautifulSoup(f, "html.parser")

            trace_key = os.path.basename(trace_file).replace(".html", "")
            var_trace = error_traces['viewer-trace']['traces'][trace_key]
            harness_vars = defaultdict(dict)
            for trace in var_trace:
                
                # Skip over lines that are not variable assignments and that are not in the harness file (where preconditions can be applied)
                # Null function indicates global var assignment which we need
                if not (trace['location']['function'] is None or re.match(r'.*_harness.c', trace['location']['file'])) or trace['kind'] != 'variable-assignment': 
                    continue

                func = trace['location']['function']
                if func is None:
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
                            root_key, idx = re.match(r'(.*)\[(\d+)\]', key).groups()
                            idx = int(idx)
                            # Root key must already exist if we're writing to an index
                            if j != len(keys) - 1: 
                                curr_scope = curr_scope[root_key][idx]
                            else:
                                root_key_scope = curr_scope.get(root_key, dict())
                                root_key_scope[idx] = value
                            continue
                        else:
                            if key not in curr_scope:
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
                elif root_var not in harness_vars[func]:
                    harness_vars[func][root_var] = value

            for func, func_vars in harness_vars.items():
                for key, var in func_vars.items():
                    if isinstance(var, dict) or isinstance(var, list):
                        harness_vars[func][key] = re.sub(r'\s+', ' ', convert_python_to_c_struct(var))
            error['harness_vars'] = harness_vars

            func_calls = soup.find_all("div", class_="function-call")[1:] # Skip over the CPROVER_initialize call
            # Get the trace files for each function call so we can extract the function definitions
            # Built-in functions have no "a" tag so they are ignored
            for call in func_calls:
                called_func = call.find(class_ = "step").find(class_="cbmc").find('a')
                if called_func:
                    func_name = called_func.text
                    origin_file = called_func['href']
                    if func_name not in html_files:
                        html_files[func_name] = origin_file

            # Determine the stack trace for this error
            stack_trace = [(error['function'], error['line'])]

            # Find the div that contains the error message, which should be unique
            error_div = soup.find_all("div", class_="cbmc", string=re.compile(fr'failure: {trace_key}: {re.escape(error["msg"])}')) # Should be unique
            if len(error_div) != 1:
                raise ValueError("Why are there 2 of you")

            caller = error_div[0].find_parent("div", class_="function")

            while True:
                func_call = caller.find("div", class_="function-call").find("div", class_="header")
                if error['is_built_in'] and error['file'] is None: # If it's a built-in func get coverage of the place where it was called
                    m = re.match(r'(?:\.+/)?((?:.*)\.c)', func_call.find("a")['href'])
                    if m:
                        error['file'] = m.group(1)
                caller_func_name, file_name, line_num = re.match(r'Step \d+: Function (.*), File (.*), Line (\d+)', func_call.text).groups()
                line_num = int(line_num)
                if caller_func_name == 'None':
                    break

                # if file_name != None and re.match(r'.*_harness.c', file_name): # Make the line number adjustment in the harness file
                #     for new_line in new_precon_lines:
                #         if line_num > new_line:
                #             line_num -= 1
                stack_trace.append((caller_func_name, line_num))

                caller = caller.find_parent("div", class_="function")

            error['stack'] = stack_trace
            # Re-define the error hash to include the stack trace
            # It technically is super redundant to redefine the hash, but we need to include the stack trace in the hash and it only gets defined here
        #     hash_str = error_hash + str(error['stack'])
        #     new_error_hash = sha256(hash_str.encode()).hexdigest()
        #     errs_to_remap[error_hash] = new_error_hash
        
        # # Swap out the hashes, can't do it in place
        # for error_hash, new_error_hash in errs_to_remap.items():
        #     errors[new_error_hash] = errors.pop(error_hash)

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
                    match = re.match(r'\d+\s+extern\s+(.*);', full_def)
                    if match:
                        global_vars.append(match.group(1))
                else:
                    raise Exception(f"Unexpected global variable definition: {full_def}")
        try:
            func_definition = soup.find('div', id=str(line_num)) # Try to find the function definition line

            if func_definition:
                full_func_text = ""

                # Look for the opening curly brace
                # Might need to add a failsafe against functions initializations without definitions
                line = func_definition
                while '{'  not in line.text or ';' in line.text:
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
                print(f"Failed to find matching function name for {func_name}")
        except Exception as e:
            print(f"Failed to extract function definition for {func_name}: {e}")
            func_text[func_name] = "Parsing failed"        

    return func_text, stub_text, global_vars, macros

def check_error_is_covered(error, json_report_dir, new_lines=[]):
    try:
        with open(os.path.join(json_report_dir, "viewer-coverage.json"), 'r') as file:
            coverage_data = json.load(file)['viewer-coverage']['coverage']
            file = error.file
            if error.is_built_in:
                func, line_num = error.stack[1]
            else:
                func = error.func
                line_num = int(error.line)

            if re.match(r'.*_harness.c', error.file) and not line_num in new_lines:
                # If lines have been added to the harness, we need to adjust the line number for the error
                for new_line in new_lines:
                    if line_num > new_line:
                        line_num += 1

            line_num = str(line_num)
            if coverage_data[file][func][line_num] != 'miss':
                return True
            else:
                # Get the block of missing lines around the target error to provide context to the LLM
                missed_lines = []
                found_target_line = False
                for line, status in coverage_data[file][func].items():
                    if line == line_num:
                        found_target_line = True

                    if status == 'miss':
                        missed_lines.append(line)
                    else:
                        if found_target_line:
                            # If we found the target line, we can return the block as an error
                            raise CoverageError(f"ERROR: Line {line_num} in function {func} is no longer covered by the harness", lines=missed_lines)
                        else:
                            missed_lines = []
                    

    except Exception as e:
        if isinstance(e, CoverageError):
            raise e
        print('Shi broke')
        raise e

def extract_errors_and_payload(harness_name, harness_path, check_for_coverage=None, new_precon_lines=[]):
    """
    Runs the harness in the specified directory and extracts all information needed by the LLM from the CBMC reports
    Can optionally pass in an error dictionary to check if it is still covered in the current run, and will raise an error if not
    """

    harness_dir = os.path.dirname(harness_path)

    html_report_dir = os.path.join(harness_dir, Path("build", "report", "html"))
    json_report_dir = os.path.join(harness_dir, Path("build", "report", "json"))

    if check_for_coverage is not None:
        # This call will throw a custom error if the error is not covered by the harness
        check_error_is_covered(check_for_coverage, json_report_dir, new_precon_lines)

    error_report = os.path.join(html_report_dir, "index.html")
    with open(error_report, "r") as f:
        soup = BeautifulSoup(f, "html.parser")
    
    errors_div = soup.find("div", class_="errors")
    error_clusters, undefined_funcs = analyze_error_report(errors_div, html_report_dir, new_precon_lines)
    if len(error_clusters) == 0:
        print("No error traces found")
        return {}
    
    try:

        html_files = analyze_traces(error_clusters, json_report_dir, new_precon_lines)
        # print(f"Extracted {len(html_files)} trace files")
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

        if not os.path.exists(f'./payloads/{harness_name}'):
            os.makedirs(f'./payloads/{harness_name}')

        with open(f'./payloads/{harness_name}/{harness_name}_functions.json', 'w') as f:
            json.dump(func_text,f,indent=4)
        
        with open(f'./payloads/{harness_name}/{harness_name}_harness.json', 'w') as f:
            json.dump(harness_info, f, indent=4)
    except Exception as e:
        print(f"Failed to extract function definitions: {e}")
        

    return error_clusters

def get_json_errors(harness_path) -> set[str]:
    """Get the positive errors from the JSON resport generated by CBMC"""
    report = {}
    json_report_dir = os.path.join(harness_path, Path("build", "report", "json"))
    with open(f"{json_report_dir}/viewer-result.json", "r", encoding="utf-8") as f:
        report = json.loads(f.read())
    return set(report["viewer-result"]["results"]["false"])

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parser.py <harness path>")
        sys.exit(1)

    harness_name = os.path.basename(sys.argv[1]).replace('_harness.c', "")
    harness_dir = os.path.dirname(sys.argv[1])

    if not os.path.exists(harness_dir):
        raise FileNotFoundError(f"Harness directory {harness_dir} does not exist")

    extracted_errors = extract_errors_and_payload(harness_name, harness_dir)
    print(f"Extracted errors: {extracted_errors}")