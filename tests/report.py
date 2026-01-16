""" Generate final reports """

# System
from datetime import datetime
from pathlib import Path
import argparse
import logging
import json
import math

# Utils
import matplotlib.pyplot as plt
import numpy as np


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
)
logger = logging.getLogger(__name__)

def collect_final_coverage(input_directory: Path) -> list[float]:
    """ Collect final coverage from all jsonl files in the input directory """
    logger.info("Collecting final coverage from %s", input_directory)
    final_coverage = []
    for jsonl_file in input_directory.glob("*.jsonl"):
        last_coverage = 0
        with jsonl_file.open("r") as f:
            for line in f:
                data = json.loads(line.strip())
                if data["type"] == "agent_result" and data["agent_name"] == "debugger":
                    last_coverage = data.get("data", {}).get("debugger_final_coverage", {}).get("percentage", 0)
        if last_coverage > 0:
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

def collect_final_verification_time(input_directory: Path) -> list[float]:
    """ Collect final verification time from all jsonl files in the input directory """
    logger.info("Collecting final verification time from %s", input_directory)
    final_verification_time = []
    for jsonl_file in input_directory.glob("*.jsonl"):
        last_verification_time = None
        with jsonl_file.open("r") as f:
            for line in f:
                data = json.loads(line.strip())
                if data["type"] == "agent_result" and data["agent_name"] == "debugger":
                    last_verification_time = data.get("data", {}).get("final_verification_time", None)
        if last_verification_time is not None:
            final_verification_time.append(last_verification_time)
    logger.info("Final verification time collected from %i files...", len(final_verification_time))
    return final_verification_time

def collect_tokens_per_agent(input_directory: Path) -> dict:
    """ Collect token usage per agent of all jsonl files in the input directory """
    logger.info("Collecting tokens per agent from %s", input_directory)
    tokens_per_agent = {
        "StubGenerator": [],
        "CoverageDebugger": [],
        "debugger": [],
        "PreconditionValidator": [],
    }
    for jsonl_file in input_directory.glob("*.jsonl"):
        with jsonl_file.open("r") as f:
            for line in f:
                token_count = 0
                data = json.loads(line.strip())
                if data["type"] == "agent_result" and data["agent_name"] in tokens_per_agent:
                    token_count = data.get("data", {}).get("tokens_used", 0)
        if token_count > 0:
            tokens_per_agent[data["agent_name"]].append(token_count)
    logger.info("Tokens per agent collected")
    return tokens_per_agent

def histogram_final_coverage(final_coverage: list[float], output_directory: Path):
    """ Generate histogram of verification time of final harnesses """
    logger.info("Generating histogram of verification time in %s", output_directory)

    if not final_coverage:
        logger.warning("No coverage data provided, skipping histogram generation.")
        return

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

def histogram_final_errors(final_error: list[float], output_directory: Path):
    """ Generate histogram of final error of final harnesses """
    logger.info("Generating histogram of final error in %s", output_directory)

    if not final_error:
        logger.warning("No error data provided, skipping histogram generation.")
        return

    bins = np.arange(0.0, max(final_error) + 1, 1.0)

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

def histogram_final_verification_time(final_verification_time: list[float], output_directory: Path):
    """ Generate histogram of verification time of final harnesses """
    logger.info("Generating histogram of final verification time in %s", output_directory)

    if not final_verification_time:
        logger.warning("No verification time data provided, skipping histogram generation.")
        return

    num_bins = math.ceil(math.sqrt(len(final_verification_time)))

    plt.figure()
    plt.hist(final_verification_time, bins=num_bins)
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

    final_coverage = collect_final_coverage(input_directory)
    final_error = collect_final_error(input_directory)
    final_verification_time = collect_final_verification_time(input_directory)

    ## For RQ1 quality of FINAL harnesses

    # Histogram of final coverage of final harness
    histogram_final_coverage(final_coverage, output_directory) # Needed to save data

    # Histogram of final error of final harness
    histogram_final_errors(final_error, output_directory)

    # Histogram of last unit proof verification time
    histogram_final_verification_time(final_verification_time, output_directory)

    # (Maybe useful) Ratio function LOC / harness LOC
    # (How long is the function compared to the harness?)


    ## Probably useful plots for other RQ:

    # Box diagram per agent by execution time
    # Box diagram per agent by coverage percentage

    # Histogram of number of agent iterations
    # Number of functions called by the llm
    # Number of tokens used by the llm



if __name__ == "__main__":
    main()
