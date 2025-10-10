import enum
import logging
import os
import subprocess
import json
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

class Status(enum.Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"

def get_coverage_dict(json_path: str) -> dict:
    with open(json_path, "r") as f:
        data = json.load(f)
    # Navigate to the overall_coverage section
    return data.get("viewer-coverage", {}).get("overall_coverage", {})

def get_reachable_functions(json_path: str) -> dict:
    with open(json_path, "r") as f:
        data = json.load(f)
    reachable = data.get("viewer-reachable", {}).get("reachable", {})
    num_files = len(reachable)
    num_functions = sum(len(funcs) for funcs in reachable.values())
    return {"num_files": num_files, "num_functions": num_functions}

def run_proof_command(entry, base_dir, output_root):
    """
    Run the harness command for a single proof/source file pair.
    Returns a tuple: (source_file_stem, status, success_flag)
    """
    function_name = entry["function_name"]
    proof_dir = Path(entry["proof_dir"])
    src_file = Path(entry["source_file"])

    log_file = output_root / f"{proof_dir.stem}.log"
    cmd = [
        "python", "src/run.py",
        "harness",
        "--target_function_name", function_name,
        "--root_dir", str(base_dir),
        "--harness_path", str(proof_dir),
        "--target_func_path", str(src_file)
    ]


    try:
        with open(log_file, "w") as f:
            process = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
        status = Status.SUCCESS if process.returncode == 0 else Status.FAILURE
    except Exception as e:
        status = Status.ERROR

    # Check log file for success message
    success = False
    if log_file.exists():
        with open(log_file, "r") as f:
            content = f.read()
            if "Makefile successfully generated and build succeeded." in content:
                status = Status.SUCCESS
            else:
                status = Status.FAILURE

    return function_name, proof_dir, status

def print_coverage(proof_dir: Path):
    print(f"Report for {proof_dir}:")
    report_path = os.path.join(proof_dir, "build/report/json")
    coverage_report = os.path.join(report_path, "coverage.json")
    if os.path.exists(coverage_report):
        coverage_dict = get_coverage_dict(coverage_report)
        print(f"Coverage:\n{coverage_dict}")
    reachability_report = os.path.join(report_path, "reachability.json")
    if os.path.exists(reachability_report):
        reachable_dict = get_reachable_functions(reachability_report)
        print(f"Reachable functions:\n{reachable_dict}")

def main():
    parser = argparse.ArgumentParser(description="Run proofs for CBMC makefiles.")
    parser.add_argument("input_json", help="Path to JSON file containing proof directories and source files")
    parser.add_argument("-b", "--base_dir", default="../RIOT", help="Base project directory (default: ../RIOT)")
    parser.add_argument("-o", "--output", help="Directory to store logs (default: output-${timestamp})")
    parser.add_argument("-j", "--jobs", type=int, default=10, help="Number of parallel jobs")
    args = parser.parse_args()

    # Determine output directory
    if args.output:
        output_root = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_root = Path(f"output-{timestamp}")
    output_root.mkdir(parents=True, exist_ok=True)

    # Load input JSON
    with open(args.input_json, "r") as f:
        entries = json.load(f)

    results = []

    # Run proofs in parallel
    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_proof_command, entry, Path(args.base_dir), output_root): entry for entry in entries}

        for future in as_completed(futures):
            src_stem, proof_dir, status = future.result()
            results.append((src_stem, proof_dir, status))
            print(f"[{src_stem}] Status: {status}")
            if status == Status.SUCCESS:
                print_coverage(proof_dir)

    # Summary
    total = len(results)
    succeeded = sum(1 for r in results if r[2] == Status.SUCCESS)
    print("\n=== SUMMARY ===")
    for src_stem, proof_dir, status in results:
        print(f"{src_stem}: Status={status} (Proof Dir: {proof_dir})")
    print(f"\nOverall: {succeeded}/{total} succeeded")

if __name__ == "__main__":
    main()
