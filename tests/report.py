""" Generate final reports """

# System
from datetime import datetime
from pathlib import Path
import argparse
import logging
import json
import math
import re

# Utils
import matplotlib.pyplot as plt
import numpy as np

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
)
logger = logging.getLogger(__name__)


def collect_compilation_statuses(input_directory: Path) -> list[str]:
    """ Collect compilation statuses from all jsonl files in the input directory """
    logger.info("Collecting compilation statuses from %s", input_directory)
    compilation_statuses = []
    for log_file in input_directory.glob("*.log"):
        compilation_status = None
        with log_file.open("r") as f:
            # TODO: INITIAL HARNESS GENERATION IS TRUE
            compilation_status = "Agent 'ProofDebugger' succeed" in f.read()
            compilation_statuses.append(compilation_status)
    logger.info("Compilation statuses collected from %i files...", len(compilation_statuses))
    return compilation_statuses

def collect_token_counts(input_directory: Path) -> list[int]:
    """ Collect token counts from all jsonl files in the input directory """
    logger.info("Collecting token counts from %s", input_directory)
    token_counts = []
    for jsonl_file in input_directory.glob("*.jsonl"):
        with jsonl_file.open("r") as f:
            result = {}
            for line in f:
                data = json.loads(line.strip())
                if data["type"] == "task_attempt":
                    token_usage = data.get("llm_data", {}).get("token_usage", {})
                    if len(token_usage) > 0:
                        if data["agent_name"] not in result:
                            result[data["agent_name"]] = {
                                "input_tokens": 0,
                                "cached_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_tokens": 0,
                                "total_tokens": 0,
                            }
                        result[data["agent_name"]]["input_tokens"] += token_usage.get("input_tokens", 0)
                        result[data["agent_name"]]["cached_tokens"] += token_usage.get("cached_tokens", 0)
                        result[data["agent_name"]]["output_tokens"] += token_usage.get("output_tokens", 0)
                        result[data["agent_name"]]["reasoning_tokens"] += token_usage.get("reasoning_tokens", 0)
                        result[data["agent_name"]]["total_tokens"] += token_usage.get("total_tokens", 0)
            token_counts.append(result)
    logger.info("Token counts collected from %i files...", len(token_counts))
    return token_counts

def collect_final_verification_time(input_directory: Path) -> list[float | str]:
    """ Collect final verification time from all jsonl files in the input directory """
    logger.info("Collecting final verification time from %s", input_directory)
    for jsonl_file in input_directory.glob("*.jsonl"):
        pass

def collect_final_coverage(input_directory: Path) -> list[float]:
    """ Collect final coverage from all jsonl files in the input directory """
    logger.info("Collecting final coverage from %s", input_directory)
    final_coverage = []
    for jsonl_file in input_directory.glob("*.jsonl"):
        last_coverage = None
        with jsonl_file.open("r") as f:
            for line in f:
                data = json.loads(line.strip())
                if data["type"] == "agent_result" and data["agent_name"] == "debugger":
                    last_coverage = data.get("data", {}).get("debugger_final_coverage", {}).get("percentage", None)
        if last_coverage is not None:
            final_coverage.append(last_coverage)
    logger.info("Final coverage collected from %i files...", len(final_coverage))
    return final_coverage

def collect_final_error(input_directory: Path) -> list[float]:
    """ Collect final error from all jsonl files in the input directory """
    logger.info("Collecting final error from %s", input_directory)
    final_error = []
    for jsonl_file in input_directory.glob("*.jsonl"):
        last_error = None
        with jsonl_file.open("r") as f:
            for line in f:
                data = json.loads(line.strip())
                if data["type"] == "agent_result" and data["agent_name"] == "debugger":
                    last_error = data.get("data", {}).get("final_errors", None)
        if last_error is not None:
            final_error.append(last_error)
    logger.info("Final error collected from %i files...", len(final_error))
    return final_error

def histogram_final_coverage(final_coverage: list[float], output_directory: Path) -> list[tuple[str, int]]:
    """ Generate histogram of verification time of final harnesses """
    logger.info("Generating histogram of verification time in %s", output_directory)

    if len(final_coverage) == 0:
        logger.warning("No coverage data provided, skipping histogram generation.")
        return []

    bins = np.arange(0.0, 1.05, 0.05)

    plt.figure()
    plt.hist(final_coverage, bins=bins)
    plt.xlabel("Final Coverage")
    plt.ylabel("Count")
    plt.title("Histogram of Final Coverage (5% intervals)")
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)

    output_path = output_directory / "final_coverage_histogram.png"
    plt.savefig(output_path)
    plt.close()

    logger.info("Histogram saved to %s", output_path)
    counts, bin_edges = np.histogram(final_coverage, bins=bins)
    table = []
    for idx, count in enumerate(counts):
        interval = f"{bin_edges[idx]:.2f}-{bin_edges[idx + 1]:.2f}"
        table.append((interval, int(count)))
    return table

def plot_token_usage(
    final_token_counts: list[dict[str, dict[str, int]]],
    output_directory: Path
) -> list[dict[str, dict[str, int]]]:
    """ Plot token usage of final harnesses """
    logger.info("Plotting token usage in %s", output_directory)
    token_types = [
        "input_tokens",
        "cached_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    ]

    totals_by_agent: dict[str, dict[str, int]] = {}
    for token_count in final_token_counts:
        for agent_name, token_data in token_count.items():
            agent_totals = totals_by_agent.setdefault(
                agent_name,
                {token_type: 0 for token_type in token_types},
            )
            for token_type in token_types:
                agent_totals[token_type] += token_data.get(token_type, 0)

    agent_names = sorted(totals_by_agent.keys())
    x = np.arange(len(agent_names))
    width = 0.8 / max(len(token_types), 1)

    plt.figure()
    for idx, token_type in enumerate(token_types):
        values = [totals_by_agent[agent][token_type] for agent in agent_names]
        plt.bar(x + idx * width, values, width, label=token_type)

    plt.xlabel("Agent")
    plt.ylabel("Token Usage")
    plt.title("Token Usage by Agent and Token Type")
    plt.xticks(x + width * (len(token_types) - 1) / 2, agent_names, rotation=20, ha="right")
    plt.legend()
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)

    output_path = output_directory / "token_usage.png"
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()

    logger.info("Token usage plot saved to %s", output_path)
    return [{agent: totals_by_agent[agent]} for agent in agent_names]

def histogram_final_errors(final_error: list[float], output_directory: Path) -> list[tuple[str, int]]:
    """ Generate histogram of final error of final harnesses """
    logger.info("Generating histogram of final error in %s", output_directory)
    if len(final_error) == 0:
        logger.warning("No error data provided, skipping histogram generation.")
        return []

    max_error = max(final_error)
    if max_error <= 0:
        bins = np.array([0.0, 1.0])
    else:
        bins = np.arange(0.0, max_error + 1, 1.0)

    plt.figure()
    plt.hist(final_error, bins=bins)
    plt.xlabel("Final Error")
    plt.ylabel("Count")
    plt.title("Histogram of Final Error")
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)

    output_path = output_directory / "final_error_histogram.png"
    plt.savefig(output_path)
    plt.close()

    logger.info("Histogram saved to %s", output_path)
    counts, bin_edges = np.histogram(final_error, bins=bins)
    print(counts, bin_edges)
    table = []
    for idx, count in enumerate(counts):
        interval = f"{bin_edges[idx]:.0f}-{bin_edges[idx + 1]:.0f}"
        table.append((interval, int(count)))
    return table

def histogram_final_verification_time(final_verification_time: list[float | str], output_directory: Path):
    """ Generate histogram of verification time of final harnesses """
    logger.info("Generating histogram of final verification time in %s", output_directory)

    numeric_times = [value for value in final_verification_time if isinstance(value, float)]
    if not numeric_times:
        logger.warning("No verification time data provided, skipping histogram generation.")
        return

    num_bins = math.ceil(math.sqrt(len(numeric_times)))

    plt.figure()
    plt.hist(numeric_times, bins=num_bins)
    plt.xlabel("Final Verification Time")
    plt.ylabel("Count")
    plt.title("Histogram of Final Verification Time")
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)

    output_path = output_directory / "final_verification_time_histogram.png"
    plt.savefig(output_path)
    plt.close()

    logger.info("Histogram saved to %s", output_path)

def main():
    """ Entry point"""
    parser = argparse.ArgumentParser(description="Generate final reports from output folder.")
    parser.add_argument("directory", help="Path to the directory containing metrics files")
    parser.add_argument("-o", "--output", help="Directory to store results (default: report-${timestamp})")
    args = parser.parse_args()

    input_directory = Path(args.directory)
    if not input_directory.exists() or not input_directory.is_dir():
        logger.error("Input directory %s does not exist or is not a directory.", input_directory)
        return

    if args.output:
        output_directory = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        output_directory = Path(f"report-{timestamp}")
    output_directory.mkdir(parents=True, exist_ok=True)

    ## Responding RQ1:

    #(1) Compile successfully,
    compilation_statuses = collect_compilation_statuses(input_directory)

    #(2) Complete verification within a fixed time budget,
    # TODO: Function pointer handler agent results
    
    #(3) Achieve high code coverage of the target unit,
    final_coverage = collect_final_coverage(input_directory)
    coverage_histogram_table = histogram_final_coverage(final_coverage, output_directory)

    #(4) Resolve all reported verification errors via refined models,
    final_error = collect_final_error(input_directory)
    error_histogram_table = histogram_final_errors(final_error, output_directory)
    # TODO: (initial - final) / initial

    #(5) false-positivivity rate per proof.
    # TODO: TBD

    #(1) generation time,
    # TODO: 

    #(2) API usage costs.
    final_token_counts = collect_token_counts(input_directory)
    final_token_counts = [x for x in final_token_counts if x is not None]
    plot_token_usage_table = plot_token_usage(final_token_counts, output_directory)

    final_error_0 = len([x for x in final_error if x == 0])
    with open(output_directory / "summary.txt", "w", encoding="utf-8") as summary_file:
        summary_file.write("Final Report Summary\n")
        summary_file.write("====================\n\n")
        summary_file.write("(1) Compile successfully,\n")
        compilation_statuses_success = len([x for x in compilation_statuses if x])
        summary_file.write(f"Success: {compilation_statuses_success}/{len(compilation_statuses)}")
        summary_file.write(" = ")
        summary_file.write(f"{(compilation_statuses_success / len(compilation_statuses)) * 100}%")
        summary_file.write("\n")

        summary_file.write("(2) Complete verification within a fixed time budget,\n")
        summary_file.write("\n")

        summary_file.write("(3) Achieve high code coverage of the target unit,\n")
        count_coverage_90 = len([x for x in final_coverage if x > 0.9])
        summary_file.write(f"Coverage > 90%: {count_coverage_90}/{len(final_coverage)}")
        summary_file.write(" = ")
        summary_file.write(f"{(count_coverage_90 / len(final_coverage)) * 100}%")
        summary_file.write("\n")

        summary_file.write("(4) Resolve all reported verification errors via refined models,\n")
        summary_file.write(f"Zero error: {final_error_0}/{len(final_error)}")
        summary_file.write(" = ")
        summary_file.write(f"{(final_error_0 / len(final_error)) * 100}%")
        summary_file.write("\n")

        summary_file.write("(5) false-positivivity rate per proof.\n")
        summary_file.write("TODO: TBD\n")

        summary_file.write("(1) generation time,\n")
        summary_file.write("\n")

        summary_file.write("(2) API usage costs.\n")
        final_price = 0
        for agent_entry in plot_token_usage_table:
            for agent_name, totals in agent_entry.items():
                summary_file.write(f"-> {agent_name}\n")
                price_per_million = {
                    "input_tokens": 1.750,
                    "cached_tokens": 0.175,
                    "output_tokens": 14.000,
                }
                total_price = 0.0
                for token_type, token_total in totals.items():
                    price = price_per_million.get(token_type)
                    if price is None:
                        continue
                    cost = (token_total / 1_000_000) * price
                    total_price += cost
                    summary_file.write(f"\t{token_type} count: {token_total}\n")
                    summary_file.write(f"\t{token_type} price: ${cost:.6f}\n")
                summary_file.write(f"\ttotal_price: ${total_price:.6f}\n")
                final_price += total_price
        summary_file.write(f"\nOverall API usage cost: ${final_price:.6f}\n")
        summary_file.write("\n")




if __name__ == "__main__":
    main()


# Price
# Input:
# $1.750 / 1M tokens
# Cached input:
# $0.175 / 1M tokens
# Output:
# $14.000 / 1M tokens