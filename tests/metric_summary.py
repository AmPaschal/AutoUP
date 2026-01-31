from collections import defaultdict
import json
import os

def process_metrics(metrics: list[dict]) -> dict:
    # Aggregates
    num_tasks = 0
    num_success = 0
    attempts_success = []
    error_counts = defaultdict(int)

    total_tool_calls = 0
    total_token_usage = 0
    total_attempt_entries = 0

    for entry in metrics:

        entry_type = entry.get("type")

        # ---- Task attempt ----
        if entry_type == "task_attempt":
            total_attempt_entries += 1

            total_tool_calls += entry.get("llm_data", {}).get("function_call_count", 0)

            total_token_usage += entry.get("llm_data", {}).get("token_usage", {}).get("total_tokens", 0)

            # Error cause stats
            if entry.get("error"):
                error_counts[entry.get("error")] += 1

        # ---- Task result ----
        elif entry_type == "task_result":
            num_tasks += 1
            success = entry.get("success", False)

            if success:
                num_success += 1
                attempts_success.append(entry.get("total_attempts", 0))

    # ---- Final metrics computation ----
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

    attempt_error_causes = [f"{err}: {count}" for err, count in error_counts.items()]

    response = {
        'num_tasks': num_tasks,
        'num_resolved': num_success,
        'resolution_rate': resolution_rate,
        'attempts_per_success_attempt': attempts_per_success,
        'attempt_error_causes': attempt_error_causes, # ['cause 1: count', 'cause 2: count', ...]
        'avg_tool_calls_per_attempt': tool_calls_per_attempt,
        'avg_tokens_per_attempt': tokens_per_attempt,
    }

    return response

def summarize_metrics_file(metrics_file):
    """ Summarize the metrics from the given metrics file. """
    
    if not os.path.exists(metrics_file):
        raise FileNotFoundError(f"Metrics file '{metrics_file}' does not exist.")
    
    with open(metrics_file, 'r') as file:
        metrics_logs = file.readlines()

    metrics = [json.loads(line) for line in metrics_logs if line.strip()]

    return process_metrics(metrics)

