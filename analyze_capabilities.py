#!/usr/bin/env python3
"""Analyze LLM function calls in AutoUP log files to identify capabilities per agent."""

import os
import re
import json
from collections import defaultdict

LOG_DIR = "output-2026-02-17_10-27-24"

# Patterns
FUNC_CALL_PATTERN = re.compile(
    r'Function call:\s*\n\s*Name:\s*(\S+)\s*\n\s*Args:\s*(\{.*\})',
    re.DOTALL
)
# Simpler pattern for single-line extraction
FUNC_CALL_LINE = re.compile(r'Function call:\s*$')
FUNC_NAME_LINE = re.compile(r'Name:\s*(\S+)')
FUNC_ARGS_LINE = re.compile(r'Args:\s*(\{.*)')

AGENT_SUCCEED = re.compile(r"Agent '(\w+)' succeed")

# Agent system prompt markers - map source file to agent name
AGENT_MARKERS = {
    'gen_harness.py': 'InitialHarnessGenerator',
    'makefile_debugger.py': 'MakefileGenerator',
    'makefile_generator.py': 'MakefileGenerator',
    'gen_function_stubs.py': 'StubGenerator',
    'handle_function_pointers.py': 'FunctionPointerHandler',
    'coverage_debugger.py': 'CoverageDebugger',
    'vuln_aware_refiner.py': 'VulnAwareRefiner',
    'debugger.py': 'ProofDebugger',
}

def classify_capability(tool_name, reason, cmd):
    """Classify a function call into a capability category."""
    reason_lower = reason.lower() if reason else ""
    cmd_lower = cmd.lower() if cmd else ""
    
    # CScope-based capabilities
    if tool_name == 'run_cscope_command':
        if '-1' in cmd:
            return ('find_definition', 'Find where a symbol/function is defined')
        elif '-0' in cmd:
            return ('find_references', 'Find all references to a symbol/function')
        elif '-2' in cmd:
            return ('find_callees', 'Find functions called by a given function')
        elif '-3' in cmd:
            return ('find_callers', 'Find functions calling a given function')
        elif '-7' in cmd:
            return ('find_file_path', 'Find full path to a file')
        elif '-8' in cmd:
            return ('find_includers', 'Find files including a header')
        elif '-9' in cmd:
            return ('find_type_definition', 'Find struct/typedef definitions')
        elif '-4' in cmd:
            return ('text_search', 'Find occurrences of text pattern')
        elif '-6' in cmd:
            return ('find_assignments', 'Find assignments to a symbol')
        else:
            return ('cscope_other', 'Other cscope query')
    
    # Condition satisfiability tool
    if tool_name == 'get_condition_satisfiability':
        return ('check_condition_sat', 'Check satisfiability of conditions in an IF statement')
    
    # Bash-based capabilities
    if tool_name == 'run_bash_command':
        # Code search / grep
        if 'grep' in cmd_lower and ('search' in reason_lower or 'find' in reason_lower or 'locate' in reason_lower or 'check' in reason_lower or 'look' in reason_lower or 'inspect' in reason_lower):
            if any(x in reason_lower for x in ['definition', 'defined', 'signature', 'prototype', 'declaration']):
                return ('code_search_definition', 'Search for function/type definitions using grep')
            if any(x in reason_lower for x in ['include', 'header']):
                return ('code_search_includes', 'Search for include statements or header files')
            if any(x in reason_lower for x in ['struct', 'typedef', 'type']):
                return ('code_search_types', 'Search for type/struct definitions using grep')
            if any(x in reason_lower for x in ['error', 'failure', 'fail']):
                return ('code_search_errors', 'Search build output or logs for error patterns')
            return ('code_search', 'Search codebase using grep for relevant information')
        
        if 'grep' in cmd_lower:
            if any(x in reason_lower for x in ['error', 'failure', 'status', 'result', 'verification', 'cbmc']):
                return ('analyze_verification_results', 'Analyze CBMC verification results/errors')
            return ('code_search', 'Search codebase using grep for relevant information')
        
        # File reading / inspection
        if any(x in cmd_lower for x in ['cat ', 'head ', 'tail ', 'sed -n', 'nl -ba', 'sed -n']):
            if any(x in reason_lower for x in ['harness', 'current harness']):
                return ('read_harness', 'Read/inspect the current harness file')
            if any(x in reason_lower for x in ['makefile']):
                return ('read_makefile', 'Read/inspect the current Makefile')
            if any(x in reason_lower for x in ['source', 'implementation', 'function', 'target', 'signature', 'definition']):
                return ('read_source_code', 'Read source code to understand function implementation')
            if any(x in reason_lower for x in ['error', 'cbmc', 'report', 'xml', 'coverage', 'loop', 'viewer']):
                return ('analyze_verification_results', 'Analyze CBMC verification results/errors')
            if any(x in reason_lower for x in ['helper', 'stub', 'include']):
                return ('read_dependencies', 'Read helper/dependency files')
            return ('read_file', 'Read file contents for inspection')
        
        # Build / make commands
        if 'make' in cmd_lower:
            if 'make clean' in cmd_lower or 'make compile' in cmd_lower or 'make -j' in cmd_lower or 'make v=' in cmd_lower:
                return ('build_and_verify', 'Run make to build and verify the harness')
            if 'make -n' in cmd_lower:
                return ('inspect_build_steps', 'Inspect build steps without running')
            return ('build_and_verify', 'Run make to build and verify the harness')
        
        # File modification / writing
        if any(x in cmd_lower for x in ['cat >', 'tee ', 'echo >', 'apply_patch', 'perl -pi']):
            if 'harness' in cmd_lower or 'harness' in reason_lower:
                return ('write_harness', 'Write/update the harness file')
            if 'makefile' in cmd_lower or 'makefile' in reason_lower or 'unwind' in reason_lower:
                return ('write_makefile', 'Write/update the Makefile')
            return ('write_file', 'Write/modify a file')
        
        # Directory listing
        if cmd_lower.startswith('ls ') or 'ls -' in cmd_lower:
            return ('list_directory', 'List directory contents')
        
        # jq / JSON processing
        if 'jq ' in cmd_lower:
            return ('analyze_verification_results', 'Analyze CBMC verification results/coverage data')
        
        # realpath / find
        if 'realpath' in cmd_lower or 'find ' in cmd_lower:
            return ('find_file_path', 'Find file location or path')
        
        # Fallback classification based on reason
        if any(x in reason_lower for x in ['error', 'failure', 'compilation', 'build']):
            return ('diagnose_build_error', 'Diagnose compilation or build errors')
        if any(x in reason_lower for x in ['verification', 'cbmc', 'property', 'unwind']):
            return ('analyze_verification_results', 'Analyze CBMC verification/property results')
        if any(x in reason_lower for x in ['coverage', 'uncovered', 'covered']):
            return ('analyze_coverage', 'Analyze code coverage results')
        if any(x in reason_lower for x in ['inspect', 'check', 'look', 'view', 'see', 'read']):
            return ('read_file', 'Read file contents for inspection')
        if any(x in reason_lower for x in ['update', 'modify', 'patch', 'overwrite', 'write']):
            return ('write_file', 'Write/modify a file')
        
        return ('bash_other', 'Other bash command')
    
    return ('unknown', 'Unknown tool call')


def parse_log_file(filepath):
    """Parse a single log file and extract function calls per agent."""
    with open(filepath, 'r', errors='replace') as f:
        lines = f.readlines()
    
    # Track current agent based on system prompt markers
    current_agent = None
    agent_calls = defaultdict(list)  # agent_name -> [(tool, capability, reason, cmd)]
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect agent transitions via source file markers in log
        for marker, agent_name in AGENT_MARKERS.items():
            if f'({marker}:' in line and ('System Prompt:' in line or 'LLM Prompt:' in line):
                current_agent = agent_name
                break
        
        # Also detect agent from INFO markers like gen_harness.py, coverage_debugger.py
        for marker, agent_name in AGENT_MARKERS.items():
            if f'({marker}:' in line and 'Processing task' in line:
                current_agent = agent_name
                break
        
        # Detect function calls
        if 'Function call:' in line and 'Function call response:' not in line:
            # Next line should have the name
            tool_name = None
            args_str = None
            
            for j in range(i, min(i+5, len(lines))):
                name_match = FUNC_NAME_LINE.search(lines[j])
                if name_match:
                    tool_name = name_match.group(1)
                args_match = FUNC_ARGS_LINE.search(lines[j])
                if args_match:
                    args_str = args_match.group(1)
            
            if tool_name and args_str:
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = {}
                
                reason = args.get('reason', '')
                cmd = args.get('cmd', args.get('command', ''))
                
                cap_id, cap_desc = classify_capability(tool_name, reason, cmd)
                
                agent = current_agent or 'Unknown'
                agent_calls[agent].append({
                    'tool': tool_name,
                    'capability': cap_id,
                    'capability_desc': cap_desc,
                    'reason': reason,
                    'cmd': cmd[:200] if cmd else ''
                })
        
        i += 1
    
    return agent_calls


def main():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), LOG_DIR)
    log_files = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')])
    
    print(f"Found {len(log_files)} log files\n")
    
    # Aggregate across all logs
    global_agent_caps = defaultdict(lambda: defaultdict(lambda: {'count': 0, 'desc': '', 'reasons': []}))
    per_log_data = {}
    
    for log_file in log_files:
        filepath = os.path.join(log_dir, log_file)
        agent_calls = parse_log_file(filepath)
        per_log_data[log_file] = agent_calls
        
        for agent, calls in agent_calls.items():
            for call in calls:
                cap = call['capability']
                global_agent_caps[agent][cap]['count'] += 1
                global_agent_caps[agent][cap]['desc'] = call['capability_desc']
                if len(global_agent_caps[agent][cap]['reasons']) < 3:
                    global_agent_caps[agent][cap]['reasons'].append(call['reason'][:100])
    
    # Print report
    print("=" * 100)
    print("AGENT CAPABILITY ANALYSIS REPORT")
    print("=" * 100)
    
    # Order agents by the pipeline order
    agent_order = [
        'InitialHarnessGenerator', 'MakefileGenerator', 'StubGenerator',
        'FunctionPointerHandler', 'CoverageDebugger', 'VulnAwareRefiner',
        'ProofDebugger', 'Unknown'
    ]
    
    for agent in agent_order:
        if agent not in global_agent_caps:
            continue
        
        caps = global_agent_caps[agent]
        total_calls = sum(c['count'] for c in caps.values())
        
        print(f"\n{'─' * 100}")
        print(f"AGENT: {agent} (Total LLM function calls: {total_calls})")
        print(f"{'─' * 100}")
        print(f"{'Capability':<35} {'Count':>6} {'%':>6}  Description")
        print(f"{'─'*35} {'─'*6} {'─'*6}  {'─'*45}")
        
        sorted_caps = sorted(caps.items(), key=lambda x: x[1]['count'], reverse=True)
        for cap_id, cap_data in sorted_caps:
            pct = cap_data['count'] / total_calls * 100
            print(f"{cap_id:<35} {cap_data['count']:>6} {pct:>5.1f}%  {cap_data['desc']}")
        
        print(f"\n  Sample reasons for top capabilities:")
        for cap_id, cap_data in sorted_caps[:5]:
            print(f"  [{cap_id}]:")
            for r in cap_data['reasons']:
                print(f"    - {r}")
    
    # Summary table
    print(f"\n\n{'=' * 100}")
    print("SUMMARY: Total calls per agent")
    print(f"{'=' * 100}")
    for agent in agent_order:
        if agent in global_agent_caps:
            total = sum(c['count'] for c in global_agent_caps[agent].values())
            print(f"  {agent:<30} {total:>5} calls")
    
    # Grand total
    grand_total = sum(sum(c['count'] for c in caps.values()) for caps in global_agent_caps.values())
    print(f"  {'TOTAL':<30} {grand_total:>5} calls")
    
    # Output JSON data for the report
    json_output = {}
    for agent in agent_order:
        if agent in global_agent_caps:
            caps = global_agent_caps[agent]
            total_calls = sum(c['count'] for c in caps.values())
            json_output[agent] = {
                'total_calls': total_calls,
                'capabilities': {
                    cap_id: {
                        'count': cap_data['count'],
                        'percentage': round(cap_data['count'] / total_calls * 100, 1),
                        'description': cap_data['desc'],
                        'sample_reasons': cap_data['reasons']
                    }
                    for cap_id, cap_data in sorted(caps.items(), key=lambda x: x[1]['count'], reverse=True)
                }
            }
    
    json_path = os.path.join(log_dir, 'capability_analysis.json')
    with open(json_path, 'w') as f:
        json.dump(json_output, f, indent=2)
    print(f"\nJSON data saved to: {json_path}")


if __name__ == '__main__':
    main()
