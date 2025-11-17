""" Running Tests"""

# System
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime
import subprocess
import json
import glob
import os

# Utils
import requests
import random

# Constants
PATH = "/home/rcalvome/Documents/AutoUp/framework/RIOT/cbmc/harness_gen_tests_4"
ROOT_DIR = "/home/rcalvome/Documents/AutoUp/framework/RIOT"
WEBHOOK_URL = "https://hooks.slack.com/triggers/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"

MAX_PROCESSES = 4


def process_metrics(metrics: list[dict]) -> dict:
    """Computed the metrics of a list"""

    num_tasks = 0
    num_success = 0
    attempts_success = []
    error_counts = defaultdict(int)

    total_tool_calls = 0
    total_token_usage = 0
    total_attempt_entries = 0

    for entry in metrics:
        entry_type = entry.get("type")
        if entry_type == "task_attempt":
            total_attempt_entries += 1
            total_tool_calls += entry.get("llm_data",
                                          {}).get("function_call_count", 0)
            total_token_usage += entry.get("llm_data", {}
                                           ).get("token_usage", {}).get("total_tokens", 0)
            if entry.get("error"):
                error_counts[entry.get("error")] += 1
        elif entry_type == "task_result":
            num_tasks += 1
            success = entry.get("success", False)
            if success:
                num_success += 1
                attempts_success.append(entry.get("total_attempts", 0))
    resolution_rate = num_success / num_tasks if num_tasks > 0 else 0.0
    attempts_per_success = (
        sum(attempts_success) / len(attempts_success)
        if attempts_success else 0.0
    )
    tool_calls_per_attempt = (
        total_tool_calls / total_attempt_entries
        if total_attempt_entries else 0.0
    )
    tokens_per_attempt = (
        total_token_usage / total_attempt_entries
        if total_attempt_entries else 0.0
    )
    attempt_error_causes = [
        f"{err}: {count}" for err, count in error_counts.items()]

    response = {
        "num_tasks": num_tasks,
        "resolution_rate": resolution_rate,
        "attempts_per_success_attempt": attempts_per_success,
        # ['cause 1: count', 'cause 2: count', ...]
        "attempt_error_causes": attempt_error_causes,
        "avg_tool_calls_per_attempt": tool_calls_per_attempt,
        "avg_tokens_per_attempt": tokens_per_attempt,
    }

    return response


def summarize_metrics_per_agent(metrics_dir: str):  # TODO
    """Summarize metrics from all *.jsonl files in a directory and print it"""

    pattern = os.path.join(metrics_dir, "*.jsonl")
    metric_files = glob.glob(pattern)

    if not metric_files:
        print(f"No metrics files found in directory: {metrics_dir}")
        return

    metrics = []

    for metrics_file in metric_files:
        try:
            with open(metrics_file, "r", encoding="utf-8") as file:
                metrics_data = file.readlines()
            file_metrics = [json.loads(line)
                            for line in metrics_data if line.strip()]
            metrics.extend(file_metrics)
        except Exception as e:
            print(f"Failed to read metrics file {metrics_file}: {e}")

    if not metrics:
        print(f"No metrics data found in files under: {metrics_dir}")
        return

    print("===== Overall Metrics Summary =====")
    overall_summary = process_metrics(metrics)
    print(json.dumps(overall_summary, indent=4))
    print("\n\n")

    metrics_by_agent = defaultdict(list)
    for entry in metrics:
        metrics_by_agent[entry.get("agent_name")].append(entry)

    print("===== Metrics Summary per Agent =====")

    for agent, agent_metrics in metrics_by_agent.items():
        agent_summary = process_metrics(agent_metrics)

        print(f"Agent '{agent}':")
        print(json.dumps(agent_summary, indent=4))
        print("\n\n")


def build_cscope_database():
    """Build the cscope database"""
    with subprocess.Popen(
        ["cscope", "-Rbqk"],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            print("Database created")
        except subprocess.TimeoutExpired:
            proc.kill()
            print("Timeout expired creating database")


def get_target_file_by_cscope(sample: str) -> str:
    """Get the path to the file where the function is implemented"""
    with subprocess.Popen(
        ["cscope", "-dL", "-1", sample],
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            stdout, _stderr = proc.communicate()
            path = ""
            for line in stdout.splitlines():
                path = line.split()[0]
                if not path.startswith("cbmc"):
                    break
            return path
        except subprocess.TimeoutExpired:
            proc.kill()
            print("Timeout expired query function implementation")
            return ""


def run_sample(sample, timestamp: str):
    """Run single test sample"""
    folder_path = os.path.join(PATH, sample)
    target_file_path = get_target_file_by_cscope(sample)
    os.makedirs(f"logs/{timestamp}", exist_ok=True)
    os.makedirs(f"metrics/{timestamp}", exist_ok=True)
    cmd = [
        "python", "src/run.py", "debugger",
        f"--root_dir={ROOT_DIR}",
        f"--target_function_name={sample}",
        f"--harness_path={folder_path}",
        f"--target_file_path={target_file_path}",
        f"--log_file=logs/{timestamp}/{sample}.log",
        f"--metrics_file=metrics/{timestamp}/{sample}.jsonl",
    ]
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                print(f"Error in {sample}:\n{stderr}")
            return stdout
        except subprocess.TimeoutExpired:
            proc.kill()
            return f"Timeout expired for {sample}"


def main():
    """Entry point"""
    build_cscope_database()
    folders = [
        d for d in os.listdir(PATH)
        if os.path.isdir(os.path.join(PATH, d)) and "_receive" in d
    ]
    #folders = random.sample(folders, 5)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    with ThreadPoolExecutor(max_workers=MAX_PROCESSES) as executor:
        futures = {
            executor.submit(
                run_sample,
                sample,
                timestamp,
            ): sample for sample in folders
        }
        for future in as_completed(futures):
            result = future.result()
            print(result)
    summarize_metrics_per_agent(f"metrics/{timestamp}")
    requests.post(
        WEBHOOK_URL,
        json={
            "message": f"Execution of proof debugger finished: metrics/{timestamp}",
        },
        timeout=10,
    )


if __name__ == "__main__":
    main()
