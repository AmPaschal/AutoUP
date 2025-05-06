import subprocess
from pathlib import Path
import os
import re
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

def analyze_error_report(errors_div, report_dir):
    extracted_errors = defaultdict(list)
    undefined_funcs = []
    # Traverse all <li> elements inside the errors div
    for li in errors_div.find_all("li", recursive=True):
        if li.text.strip().startswith("Other failures"):
            undef_funcs = li.find_all("li", recursive=True)
            for func in undef_funcs:
                func_name = re.match(r'(.*)\.no-body\.(.*)', func.text.strip()).groups()
                undefined_funcs.append(f"{func_name[1]} @ {func_name[0]}")

        # Get the error description (text content after trace link)
        text = li.text.strip()
        if not text.startswith("Function"):
            continue

        func_name = re.search(r'Function ([a-zA-Z0-9_]+)', text).group(1)
        
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

                error_output = f"{func_name} @ {line_num}: {error_msg.strip()}"
                trace_link = error_report.find("a", text='trace')
                trace_href = trace_link['href'] if trace_link else None
                extracted_errors[func_name].append({ 'error_msg': error_output, 'trace': os.path.join(report_dir, trace_href) })
            error_report = error_report.find_next_sibling('li')
    return extracted_errors, undefined_funcs

def analyze_traces(extracted_errors):
    html_files = dict()
    for errors in extracted_errors.values():
        for i, error in enumerate(errors):
            trace = error.pop('trace')
            with open(trace, "r") as f:
                soup = BeautifulSoup(f, "html.parser")

            # First function div sets the value of any relevant global vars
            global_def_steps = soup.find("div", class_="function")

            # *** TO DO LATER ***

            # Then extract all values of variables in the harness
            harness_vars = dict()
            harness_steps = global_def_steps.next_sibling.next_sibling.find(class_="function-body")
            skip_next = False
            for step in harness_steps.find_all("div", class_="step", recursive=False):
                if step == '\n':
                    continue
                # This will almost always be malloc calls so we want to skip over them
                
                if skip_next:
                    skip_next = False
                    continue
                
                var_def = step.find("div", class_="code").text.strip()
                var_val = step.find("div", class_="cbmc").text.strip()
                if "return_value" in var_val:
                    continue
                    
                if '=' in var_def:
                    var_name = step.find("div", class_="code").text.split('=', maxsplit=1)[0].strip()
                else:
                    var_name = var_def[:-1]
                var_val = re.sub(r"\([01\s]+\)", "", var_val.split('=', maxsplit=1)[1].strip()).strip()
                harness_vars[var_name] = var_val

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

    return html_files

def extract_func_definitions(html_files, report_dir):
    func_text = dict()
    stub_text = dict()
    harness_file = os.path.basename(html_files['harness'].split('#')[0])
    global_vars = []
    macros = []
    for func_name, trace_path in html_files.items():
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

def analyze_errors(harness_name):
    root_path = Path("..","..","RIOT")
    harness_path = Path(root_path, "cbmc", "proofs", harness_name)
    # First run make
    run_command('make', cwd=harness_path)
    print (f"Make command completed")

    report_dir = os.path.join(harness_path, Path("build", "report", "html"))
    error_report = os.path.join(report_dir, "index.html")
    with open(error_report, "r") as f:
        soup = BeautifulSoup(f, "html.parser")
    
    errors_div = soup.find("div", class_="errors")
    extracted_errors, undefined_funcs = analyze_error_report(errors_div, report_dir)
    if len(extracted_errors) == 0:
        print("No error traces found")
        return
    elif "harness" in extracted_errors:
        print("Found errors in harness, please ensure harness can actually run")
        return

    html_files = analyze_traces(extracted_errors)
    
    print(f"Extracted {len(html_files)} trace files")
    func_text, stub_text, global_vars, macros = extract_func_definitions(html_files, report_dir)
    
    llm_payload = {
        'errors': extracted_errors,
        'unit_proof': {
            'harness': func_text.pop('harness'),
        },
        'relevent_funcs': func_text
    }

    if len(stub_text) > 0:
        llm_payload['stubs'] = stub_text
    
    if len(global_vars) > 0:
        llm_payload['unit_proof']['global_vars'] = global_vars
    
    if len(macros) > 0:
        llm_payload['unit_proof']['macros'] = macros
    
    if len(undefined_funcs) > 0:
        llm_payload['undefined_funcs'] = undefined_funcs

    with open(f'{harness_name}_payload.json', 'w') as f:
        json.dump(llm_payload, f, indent=4)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: main.py <harness name>")
        sys.exit(1)

    harness_name = sys.argv[1]
    analyze_errors(harness_name)