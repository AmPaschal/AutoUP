import subprocess
from pathlib import Path
import os
import re
from bs4 import BeautifulSoup
from pprint import pp as pprint
from collections import defaultdict
import json
import sys

VALID_FUNC_TYPES = [
    'int',
    "void"
]
VALID_FUNC_MATCHER = '|'.join(VALID_FUNC_TYPES)

def run_command(command, cwd=None):
    """Runs a shell command and handles errors."""
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, check=True, capture_output=True, text=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise Exception(f"Command failed: {command}\n")

# def extract_func(file_path, func_name)

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
    extracted_errors = defaultdict(list)
    undefined_funcs = []
    traces = []
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
                extracted_errors[func_name].append(error_output)
            trace_link = error_report.find("a", text='trace') # This only uses the first trace per line, which is probably fine
            trace_href = trace_link['href'] if trace_link else None
            traces.append(trace_href)
            error_report = error_report.find_next_sibling('li')

    if len(traces) == 0:
        print("No error traces found")
        return
    elif "harness" in extracted_errors:
        print("Found errors in harness, please ensure harness can actually run")
        return

    html_files = dict()

    for trace in traces:

        trace_file = os.path.join(report_dir, trace)
        with open(trace_file, "r") as f:
            soup = BeautifulSoup(f, "html.parser")


        # print(soup.get_text())
        trace_steps = soup.find_all("div", class_="step")
        # print(trace_steps.prettify)
        for step in trace_steps:

            # Skip over steps that don't actually contain code
            if not step.find('div', class_="code"):
                continue

            header = step.find("div", class_="header")
            hyperlinks  = header.find_all('a')
            if len(hyperlinks) != 3:
                continue
            # The first 2 hyperlinks per header should always have the function name and the trace file
            func_name = hyperlinks[0].text
            origin_file = hyperlinks[0]['href']
            if not func_name in html_files:
                html_files[func_name] = origin_file

    
    print(f"Extracted {len(html_files)} trace files")
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
    # This one has a specific error that I think should be very easy to analyze

    harness_name = sys.argv[1]
    analyze_errors(harness_name)