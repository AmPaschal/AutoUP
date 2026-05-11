""" Manage Run File"""

# System
from collections import defaultdict
import json
from typing import Optional
import argparse
import signal
import time
import uuid
import os

# Utils
from dotenv import load_dotenv

# AutoUP
from coverage_debugger.coverage_debugger import CoverageDebugger
from makefile.makefile_debugger import MakefileDebugger
from makefile_generator.makefile_generator import MakefileGenerator
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from debugger.debugger import ProofDebugger
from commons.project_container import ProjectContainer
from logger import init_logging, setup_logger
from commons.metric_summary import process_metrics
from commons.apptainer_tool import ApptainerProjectContainer
from commons.docker_tool import DockerProjectContainer
from stub_generator.handle_function_pointers import FunctionPointerHandler
from vuln_aware_refiner.vuln_aware_refiner import VulnAwareRefiner
from stub_generator.gen_function_stubs import StubGenerator
from commons.models import Generable
from validator.precondition_validator import PreconditionValidator
from vscode_bridge.progress import VSCodeJobProgress


# Global project container
project_container: Optional[ProjectContainer] = None
current_progress: Optional[VSCodeJobProgress] = None


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


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""
    parser = argparse.ArgumentParser(
        description="Tool for harness generation and proof debugging using DockerExecutor."
    )
    parser.add_argument(
        "mode",
        choices=["harness", "debugger", "function-stubs", "function-pointers", "coverage", "vuln-aware", "precondition", "makefile-gen", "all"],
        help=(
            "Execution mode: "
            "'harness' to generate harness/makefile, "
            "'debugger' to run proof debugger, "
            "'function-stubs' to run function stub generator, "
            "'function-pointers' to run function pointer handler, "
            "'coverage' to run coverage debugger, "
            "'vuln-aware' to run vulnerability-aware harness refiner, "
            "'precondition' to run precondition validator, "
            "'makefile-gen' to run standalone makefile generator, or "
            "'all' to run all 'harness', 'debugger' and 'coverage' modes sequentially."
        )
    )
    parser.add_argument(
        "--target_function_name",
        help="Target function name (required for harness mode).",
        required=True,
    )
    parser.add_argument(
        "--root_dir",
        help="Root directory of the project.",
        required=True,
    )
    parser.add_argument(
        "--harness_path",
        help="Path to the harness directory.",
        required=True,
    )
    parser.add_argument(
        "--target_file_path",
        help="Path to target function source file (required for harness mode).",
        required=True,
    )
    parser.add_argument(
        "--log_file",
        help="Path where log file should be saved."
    )
    parser.add_argument(
        "--metrics_file",
        help="Path where metrics file should be saved."
    )
    parser.add_argument(
        "--container_engine",
        choices=["docker", "apptainer"],
        default="docker",
        help="Container engine to use (default: docker).",
    )
    parser.add_argument(
        "--llm_model",
        default="gpt-5.3-codex",
        help="LLM model to use (default: gpt-5.2)"
    )
    parser.add_argument(
        "--scope_bound",
        type=positive_int,
        default=None,
        help=(
            "Optional maximum depth for scope widening. "
            "When set without a time budget, widening remains compile-only "
            "at each level and integrated model generation runs once at the "
            "final accepted scope."
        ),
    )
    parser.add_argument(
        "--scope_time_budget",
        type=positive_float,
        default=None,
        help=(
            "Optional wall-clock budget in minutes for the single full "
            "verification run performed after integrated model generation "
            "at each accepted scope widening level."
        ),
    )
    parser.add_argument(
        "--job_id",
        default=None,
        help="Optional VS Code job identifier used for structured progress events.",
    )
    parser.add_argument(
        "--execution_host",
        default=None,
        help="Optional execution host label used for VS Code manifests.",
    )
    return parser


def get_parser():
    """Parse CLI options."""
    return build_parser().parse_args()


def process_mode(args):
    """ Process the mode selected in the CLI"""

    logger = setup_logger(__name__)
    progress = getattr(args, "vscode_progress", None)

    logger.info("Running in '%s' mode.", args.mode)
    logger.info("Harness path: %s", args.harness_path)
    logger.info("Root directory: %s", args.root_dir)
    logger.info("Target function name: %s", args.target_function_name)
    logger.info("Target file path: %s", args.target_file_path)

    agents: list[Generable] = []
    if args.mode in ["harness", "all"]:
        agents.append(InitialHarnessGenerator(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["makefile"]:
        agents.append(MakefileDebugger(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["makefile-gen"]:
        agents.append(MakefileGenerator(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["function-stubs"]:
        agents.append(StubGenerator(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["function-pointers"]:
        agents.append(FunctionPointerHandler(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["coverage", "all"]:
        agents.append(CoverageDebugger(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["vuln-aware", "all"]:
        agents.append(VulnAwareRefiner(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["debugger", "all"]:
        agents.append(ProofDebugger(
            args=args,
            project_container=project_container
        ))

    for agent in agents:
        stage_name = agent.__class__.__name__
        if progress and progress.enabled:
            progress.stage_started(stage_name)
        start_time = time.perf_counter()
        result = agent.generate()
        elapsed_time = time.perf_counter() - start_time
        if args.metrics_file:
            with open(args.metrics_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "agent_name": agent.__class__.__name__,
                    "elapsed_time": elapsed_time
                }))
                f.write("\n")
        if not result:
            logger.error("Agent '%s' failed. Aborting.", str(agent))
            if progress and progress.enabled:
                progress.stage_completed(stage_name, success=False)
            return False
        if progress and progress.enabled:
            progress.stage_completed(stage_name, success=True)
        logger.info("Agent '%s' succeed", agent.__class__.__name__)
    return True

def log_final_result(metrics_file: str, data: list[dict]):
        if not metrics_file:
            return

        with open(metrics_file, 'a') as f:
            for entry in data:
                f.write(json.dumps(entry) + "\n")

def get_metrics_file_entry(agent_name: str, data: dict):
    return {
        "type": "run_result",
        "agent_name": agent_name,
        "data": data,
        "timestamp": time.time()
    }

def summarize_metrics_per_agent(metrics_file: str, logger):
    """ Summarize metrics from the given file and print to logger """
    with open(metrics_file, "r") as file:
        metrics_data = file.readlines()

    metrics = [json.loads(line) for line in metrics_data if line.strip()]

    entries = []

    logger.info("===== Overall Metrics Summary =====")
    overall_summary = process_metrics(metrics)
    entries.append(get_metrics_file_entry("overall", overall_summary))
    logger.info(json.dumps(overall_summary, indent=4))
    logger.info("\n\n")

    # ---- Group by agent_name ----
    metrics_by_agent = defaultdict(list)
    for entry in metrics:
        metrics_by_agent[entry.get("agent_name")].append(entry)

    logger.info("===== Metrics Summary per Agent =====")

    # ---- Summarize per agent ----
    for agent, agent_metrics in metrics_by_agent.items():
        agent_summary = process_metrics(agent_metrics)
        entries.append(get_metrics_file_entry(agent, agent_summary))

        logger.info(f"Agent '{agent}':")
        logger.info(json.dumps(agent_summary, indent=4))
        logger.info("\n\n")

    log_final_result(metrics_file, entries)

def run_with_args(args) -> bool:
    """Run the AutoUP pipeline with a pre-built arguments namespace.

    Inputs:
        args: Namespace containing the standard AutoUP runtime arguments.

    Returns:
        bool: True when the full pipeline succeeds and False otherwise.

    Behavior:
        Initializes logging and the container runtime, then delegates to the
        shared mode-processing logic used by both the CLI and the VS Code bridge.
    """
    global project_container, current_progress
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    load_dotenv()
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not hasattr(args, "vscode_progress"):
        # Build a disabled/no-op progress writer automatically for plain CLI runs.
        args.vscode_progress = VSCodeJobProgress.from_args(args)
    current_progress = args.vscode_progress

    init_logging(args.log_file)
    logger = setup_logger(__name__)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key is None:
        raise EnvironmentError("No OpenAI API key found")

    if args.container_engine == "apptainer":
        project_container = ApptainerProjectContainer(
            apptainer_def_path="container/tools.def",
            host_dir=args.root_dir,
            repo_dir=repo_dir,
        )
    else:
        container_name = f"autoup_{uuid.uuid4().hex[:8]}"
        project_container = DockerProjectContainer(
            dockerfile_path="container/tools.Dockerfile",
            host_dir=args.root_dir,
            container_name=container_name,
            repo_dir=repo_dir,
        )
    try:
        project_container.initialize()
    except Exception as e:
        logger.error(f"Error initializing Project container: {e}")
        progress = getattr(args, "vscode_progress", None)
        if progress and progress.enabled:
            progress.job_failed(f"Container initialization failed: {e}")
        return False
    
    success = process_mode(args)

    if args.metrics_file:
        # Summarize metrics and print results to log
        try:
            summarize_metrics_per_agent(args.metrics_file, logger)
        except Exception as e:
            logger.error(f"Error summarizing metrics: {e}")
    return success


def main():
    """Parse CLI arguments and run the AutoUP pipeline.

    Inputs:
        None. Reads process command line arguments.

    Returns:
        bool: True on success and False on failure.
    """
    args = get_parser()
    return run_with_args(args)


def cleanup(signum, _frame):
    """Handle process shutdown by cleaning up container and progress state.

    Inputs:
        signum: Signal number that triggered cleanup.
        _frame: Unused current stack frame.

    Returns:
        Never returns. Exits the process.
    """
    print(f"Caught signal {signum}, cleaning up container...")
    try:
        # Mark the job as cancelled before tearing down the container so the extension
        # sees a terminal state even on abrupt shutdown.
        if current_progress and current_progress.enabled:
            current_progress.job_cancelled()
        if project_container:
            project_container.terminate()
    finally:
        exit(0)



if __name__ == "__main__":
    try:
        succeeded = main()
        raise SystemExit(0 if succeeded else 1)
    except Exception as e:
        print(f"Error occurred while running main: {e}")
        raise e
    finally:
        if project_container:
            project_container.terminate()
