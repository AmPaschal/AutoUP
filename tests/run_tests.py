from collections import defaultdict
import csv
import enum
import glob
import os
import logging
import subprocess
import json
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import uuid

from tests.metric_summary import process_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
)
logger = logging.getLogger(__name__)


def positive_int(value: str) -> int:
    """Parse a strictly positive integer CLI argument."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def positive_float(value: str) -> float:
    """Parse a strictly positive float CLI argument."""
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed

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

def print_coverage(proof_dir: Path):
    print(f"Report for {proof_dir}:")
    report_path = os.path.join(proof_dir, "build/report/json")
    coverage_report = os.path.join(report_path, "viewer-coverage.json")
    if os.path.exists(coverage_report):
        coverage_dict = get_coverage_dict(coverage_report)
        print(f"Coverage:\n{coverage_dict}")
    reachability_report = os.path.join(report_path, "viewer-reachable.json")
    if os.path.exists(reachability_report):
        reachable_dict = get_reachable_functions(reachability_report)
        print(f"Reachable functions:\n{reachable_dict}")

def summarize_metrics_per_agent(metrics_dir: str):
    """Summarize metrics from all metrics-*.jsonl files in a directory and print to logger"""

    # ---- Gather all metrics files in the directory ----
    pattern = os.path.join(metrics_dir, "metrics-*.jsonl")
    metric_files = glob.glob(pattern)

    if not metric_files:
        logger.warning(f"No metrics files found in directory: {metrics_dir}")
        return
    
    logger.info(f"Found {len(metric_files)} metrics files in directory: {metrics_dir}")

    metrics = []

    # ---- Load and combine metrics from each file ----
    for metrics_file in metric_files:
        try:
            with open(metrics_file, "r") as file:
                metrics_data = file.readlines()

            file_metrics = [json.loads(line) for line in metrics_data if line.strip()]
            metrics.extend(file_metrics)

        except Exception as e:
            logger.error(f"Failed to read metrics file {metrics_file}: {e}")

    if not metrics:
        logger.warning(f"No metrics data found in files under: {metrics_dir}")
        return

    # ---- Overall summary ----
    logger.info("===== Overall Metrics Summary =====")
    overall_summary = process_metrics(metrics)
    logger.info(json.dumps(overall_summary, indent=4))
    logger.info("\n\n")

    # ---- Group by agent ----
    metrics_by_agent = defaultdict(list)
    for entry in metrics:
        metrics_by_agent[entry.get("agent_name")].append(entry)

    logger.info("===== Metrics Summary per Agent =====")

    for agent, agent_metrics in metrics_by_agent.items():
        agent_summary = process_metrics(agent_metrics)

        logger.info(f"Agent '{agent}':")
        logger.info(json.dumps(agent_summary, indent=4))
        logger.info("\n\n")

def build_parser() -> argparse.ArgumentParser:
    """Create the batch proof runner CLI parser."""
    parser = argparse.ArgumentParser(description="Run proofs for CBMC makefiles.")
    parser.add_argument("input_file", help="Path to file containing source files and target functions")
    parser.add_argument("-p", "--proof_dir", required=True, help="directory containing CBMC proofs")
    parser.add_argument("-m", "--mode", choices=["harness", "debugger", "coverage", "vuln-aware", "function-stubs", "function-pointers", "precondition", "all"], default="harness", help="Execution mode")
    parser.add_argument("-b", "--base_dir", default="../RIOT", help="Base project directory (default: ../RIOT)")
    parser.add_argument("-o", "--output", help="Directory to store logs (default: output-${timestamp})")
    parser.add_argument("-j", "--jobs", type=int, default=10, help="Number of parallel jobs")
    parser.add_argument("-s", "--scope_bound", type=positive_int, default=None, help="Optional maximum depth of verification scope")
    parser.add_argument("-st", "--scope_time_budget", type=positive_float, default=None, help="Optional full verification wall-clock budget in minutes for scope widening")
    parser.add_argument(
        "-c",
        "--container_engine",
        choices=["docker", "apptainer"],
        default="docker",
        help="Container engine to use (default: docker).",
    )
    return parser


def build_run_command(entry, args, metrics_file: Path, proof_dir: Path) -> list[str]:
    """Build the `src/run.py` command for a single proof."""
    base_dir = Path(args.base_dir)
    function_name = entry["function_name"]
    src_file = Path(entry["source_file"])

    cmd = [
        "python", "src/run.py",
        args.mode,
        "--target_function_name", function_name,
        "--root_dir", str(base_dir),
        "--harness_path", str(proof_dir),
        "--target_file_path", str(src_file),
        "--metrics_file", str(metrics_file),
        "--container_engine", args.container_engine,
    ]
    if getattr(args, "scope_bound", None) is not None:
        cmd.extend(["--scope_bound", str(args.scope_bound)])
    if getattr(args, "scope_time_budget", None) is not None:
        cmd.extend([
            "--scope_time_budget",
            str(args.scope_time_budget),
        ])
    return cmd


def resolve_entry_source_paths(entries: list[dict], root_path: str) -> list[dict]:
    """Resolve relative source_file values against the provided project root."""
    root_dir = Path(root_path).resolve()
    resolved_entries = []

    for entry in entries:
        normalized_entry = dict(entry)
        source_file = normalized_entry.get("source_file")

        if source_file:
            source_path = Path(source_file)
            if not source_path.is_absolute():
                normalized_entry["source_file"] = str((root_dir / source_path).resolve())

        resolved_entries.append(normalized_entry)

    return resolved_entries


def run_proof_command(entry, args, output_root):
    """
    Run the harness command for a single proof/source file pair.
    Returns a tuple: (source_file_stem, status, success_flag)
    """
    function_name = entry["function_name"]
    src_file = Path(entry["source_file"])
    src_file_name = src_file.stem
    proof_dir = Path(os.path.join(args.proof_dir, src_file_name, function_name))

    log_file = output_root / f"{src_file_name}-{function_name}.log"
    metrics_file = output_root / f"metrics-{src_file_name}-{function_name}.jsonl"
    cmd = build_run_command(entry, args, metrics_file, proof_dir)

    exp_id = uuid.uuid4().hex[:8].upper()

    ts = time.time()
    logger.info(f"[{ts}] Starting experiment {exp_id}: file {src_file_name}, function: {function_name}")

    try:
        with open(log_file, "w") as f:
            process = subprocess.run(cmd, stdout=f, stderr=f)
        status = Status.SUCCESS if process.returncode == 0 else Status.FAILURE
    except Exception as e:
        status = Status.ERROR

    te = time.time()
    logger.info(f"[{te}] Finished experiment {exp_id}: file {src_file_name}, function: {function_name}, status: {status}")

    # Check log file for success message
    if args.mode == "harness":
        if log_file.exists():
            with open(log_file, "r") as f:
                content = f.read()
                if "Makefile successfully generated and build succeeded." in content:
                    status = Status.SUCCESS
                else:
                    status = Status.FAILURE

    return function_name, proof_dir, status

def main():
    args = build_parser().parse_args()

    # Determine output directory
    if args.output:
        output_root = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_root = Path(f"output-{timestamp}")
    output_root.mkdir(parents=True, exist_ok=True)

    # Create proof directory if it doesn't exist
    Path(args.proof_dir).mkdir(parents=True, exist_ok=True)

    # Extract the file path from args
    file_path = Path(args.input_file)

    if file_path.suffix.lower() == ".json":
        # Logic for JSON files
        with open(file_path, "r", encoding="utf-8") as f:
            entries = json.load(f)

    elif file_path.suffix.lower() == ".csv":
        # Logic for CSV files
        with open(file_path, "r", encoding="utf-8", newline="") as f:
            # Using DictReader transforms each row into a dictionary
            reader = csv.DictReader(f)
            entries = list(reader)

    else:
        raise ValueError(f"Unsupported file format: {file_path.suffix}")

    entries = resolve_entry_source_paths(entries, args.base_dir)

    results = []

    # Run proofs in parallel
    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_proof_command, entry, args, output_root): entry for entry in entries}

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

    # Print logged metrics
    summarize_metrics_per_agent(str(output_root))

if __name__ == "__main__":
    main()
