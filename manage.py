"""Manage file"""

# System
import argparse
import logging
import sys

# Utils
from dotenv import load_dotenv

# AutoUp
from src.debugger.new.new_debugger import ProofDebugger
from src.commons.docker_tool import ProjectContainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    """Entry Point for AutoUp"""
    parser = argparse.ArgumentParser(
        description="AutoUp CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    harness_parser = subparsers.add_parser("harness")
    harness_parser.add_argument("target_function_name")
    harness_parser.add_argument("harness_path")
    harness_parser.add_argument("target_func_path")

    debugger_parser = subparsers.add_parser("debugger")
    debugger_parser.add_argument("harness_dir")
    debugger_parser.add_argument("target_function_name")

    args = parser.parse_args()

    project_container = ProjectContainer(
        "tools.Dockerfile",
        host_dir=args.root_dir,
        container_name="autoup_project_container",
    )
    try:
        project_container.initialize()
    except Exception as e:
        logging.error("Error initializing Project container: %s", e)
        sys.exit(1)

    if args.command == "harness":
        run_harness(args.target_function_name,
                    args.harness_path, args.target_func_path)
    elif args.command == "debugger":
        run_debugger(args.harness_dir, args.target_function_name)


def run_harness(target_function_name, harness_path, target_func_path):
    """Harness mode"""
    logger.info("Running in 'harness' mode...")


def run_debugger(harness_dir, target_function_name, project_container):
    """Harness mode"""
    logger.info("Running in 'debugger' mode...")
    proof_writer = ProofDebugger(
        harness_dir=harness_dir,
        target_func=target_function_name,
        target_file_path=f"{harness_dir}/{target_function_name}_harness.c",
        project_container=project_container
    )
    result = proof_writer.generate()
    if result:
        logger.info("Debugger completed sucessfully.")
    else:
        logger.warning("Debugger completed with errors.")


if __name__ == "__main__":
    load_dotenv()
    main()
