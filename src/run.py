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
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from debugger.debugger import ProofDebugger
from commons.docker_tool import ProjectContainer
from logger import init_logging, setup_logger
from commons.metric_summary import process_metrics
from stub_generator.handle_function_pointers import FunctionPointerHandler
from vuln_aware_refiner.vuln_aware_refiner import VulnAwareRefiner
from stub_generator.gen_function_stubs import StubGenerator
from commons.models import Generable
from validator.precondition_validator import PreconditionValidator


# Global project container
project_container: Optional[ProjectContainer] = None


def get_parser():
    """ Create parser for CLI options """
    parser = argparse.ArgumentParser(
        description="Tool for harness generation and proof debugging using DockerExecutor."
    )
    parser.add_argument(
        "mode",
        choices=["harness", "debugger", "function-stubs", "function-pointers", "coverage", "vuln-aware", "precondition", "all"],
        help=(
            "Execution mode: "
            "'harness' to generate harness/makefile, "
            "'debugger' to run proof debugger, "
            "'function-stubs' to run function stub generator, "
            "'function-pointers' to run function pointer handler, "
            "'coverage' to run coverage debugger, "
            "'vuln-aware' to run vulnerability-aware harness refiner, "
            "'precondition' to run precondition validator, or "
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
        "--llm_model",
        default="gpt-5",
        help="LLM model to use (default: gpt-5)"
    )
    return parser.parse_args()


def process_mode(args):
    """ Process the mode selected in the CLI"""

    logger = setup_logger(__name__)

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
    if args.mode in ["function-stubs", "all"]:
        agents.append(StubGenerator(
            args=args,
            project_container=project_container
        ))
    if args.mode in ["function-pointers", "all"]:
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
        start_time = time.perf_counter()
        result = agent.generate()
        elapsed_time = time.perf_counter() - start_time
        with open(args.metrics_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "agent_name": agent.__class__.__name__,
                "elapsed_time": elapsed_time
            }))
            f.write("\n")
        if not result:
            logger.error("Agent '%s' failed. Aborting.", str(agent))
            return
        logger.info("Agent '%s' succeed", agent.__class__.__name__)

def summarize_metrics_per_agent(metrics_file: str, logger):
    """ Summarize metrics from the given file and print to logger """
    with open(metrics_file, "r") as file:
        metrics_data = file.readlines()

    metrics = [json.loads(line) for line in metrics_data if line.strip()]

    logger.info("===== Overall Metrics Summary =====")
    overall_summary = process_metrics(metrics)
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

        logger.info(f"Agent '{agent}':")
        logger.info(json.dumps(agent_summary, indent=4))
        logger.info("\n\n")

def main():
    """Entry point"""
    global project_container
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    load_dotenv()

    args = get_parser()

    init_logging(args.log_file)
    logger = setup_logger(__name__)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key is None:
        raise EnvironmentError("No OpenAI API key found")

    container_name = f"autoup_{uuid.uuid4().hex[:8]}"
    project_container = ProjectContainer(
        dockerfile_path="docker/tools.Dockerfile",
        host_dir=args.root_dir,
        container_name=container_name
    )
    try:
        project_container.initialize()
    except Exception as e:
        logger.error(f"Error initializing Project container: {e}")
        return
    
    process_mode(args)

    if args.metrics_file:
        # Summarize metrics and print results to log
        try:
            summarize_metrics_per_agent(args.metrics_file, logger)
        except Exception as e:
            logger.error(f"Error summarizing metrics: {e}")


def cleanup(signum, _frame):
    """ Clean up container """
    print(f"Caught signal {signum}, cleaning up container...")
    if project_container:
        project_container.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error occurred while running main: {e}")
        raise e
    finally:
        if project_container:
            project_container.terminate()
