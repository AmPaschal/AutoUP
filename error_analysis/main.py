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

def analyze_errors(harness_name, branch_name):
    root_path = Path("..","..","RIOT")
    harness_path = Path(root_path, "cbmc", "proofs", harness_name)
    # First run make
    run_command(f"git checkout {branch_name}", cwd=root_path)
    print (f"Checked out branch {branch_name}")
    run_command('make', cwd=harness_path)
    print (f"Make command completed")

    report_dir = os.path.join(harness_path, Path("build", "report", "html"))
    error_report = os.path.join(report_dir, "index.html")
    with open(error_report, "r") as f:
        soup = BeautifulSoup(f, "html.parser")
    
    errors_div = soup.find("div", class_="errors")
    extracted_errors = defaultdict(list)
    traces = []
    # Traverse all <li> elements inside the errors div
    for li in errors_div.find_all("li", recursive=True):
        
        # Get the error description (text content after trace link)
        text = li.text.strip()
        if not text.startswith("Function"):
            continue

        func_name = re.search(r'Function ([a-zA-Z0-9_]+)', text).group(1)
        
        # Get the li holding line info
        error_report = li.find('li')
        while error_report != None:
            info = re.search(r'\s*(Line \d+)\s*\[trace\]\s*((?:[^\s]+\s?)+)\s*',error_report.text).groups()

            error_message = f"{func_name} @ {info[0]}: {info[1].strip()}"
            trace_link = error_report.find("a", text='trace') # This only uses the first trace per line, which is probably fine
            trace_href = trace_link['href'] if trace_link else None

            extracted_errors[func_name].append(error_message)
            traces.append(trace_href)
            error_report = error_report.find_next_sibling('li')

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
            origin_file = hyperlinks[1]['href']
            if not func_name in html_files:
                html_files[func_name] = origin_file
    
    print(f"Extracted {len(html_files)} trace files")
    func_text = dict()
    for func_name, trace_path in html_files.items():
        file_path = os.path.join(report_dir, Path('traces', trace_path))
        
        with open(file_path, "r") as f:
            soup = BeautifulSoup(f, "html.parser")

        func_definition = soup.find('a', string=func_name).find_parent() # Try to find the function definition line
        if func_definition:
            func_text[func_name] = func_definition.text.strip() + '\n'

            # These are typically 1-line functions without a definition
            if ';' in func_definition.text:
                continue

            # Look for the opening curly brace
            line = func_definition.next_sibling
            while not '{' in line.text:
                func_text[func_name] += line.text.strip() + '\n'
                line = line.next_sibling

            num_unmatched_braces = 1
            func_text[func_name] += line.text.strip() + '\n'
            while num_unmatched_braces != 0:
                line = line.next_sibling
                line_text = re.sub(r'//.*', '', line.text)
                if '{' in line.text:
                    num_unmatched_braces += 1
                elif '}' in line.text:
                    num_unmatched_braces -= 1
                func_text[func_name] += line_text.strip() + '\n'
        else:
            print("Failed to find matching function name for ")
    
    llm_payload = {
        'errors': extracted_errors,
        'unit_proof': func_text.pop('harness'),
        'relevent_funcs': func_text
    }

    with open(f'{harness_name}_payload.json', 'w') as f:
        json.dump(llm_payload, f, indent=4)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: main.py <harness name> <branch_name>")
        sys.exit(1)
    # This one has a specific error that I think should be very easy to analyze

    harness_name = sys.argv[1]
    branch_name = sys.argv[2]
    analyze_errors(harness_name, branch_name)