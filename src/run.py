""" Manage Run File"""

# System
from typing import Optional
from pathlib import Path
import argparse
import signal
import uuid
import os

# Utils
from dotenv import load_dotenv

# AutoUP
from coverage_debugger.coverage_debugger import CoverageDebugger
from makefile.gen_makefile import LLMMakefileGenerator
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from debugger.debugger import ProofDebugger
from commons.docker_tool import ProjectContainer
from logger import init_logging, setup_logger
from stub_generator.gen_function_stubs import StubGenerator


# Global project container
project_container: Optional[ProjectContainer] = None


def get_parser():
    """ Create parser for CLI options """
    parser = argparse.ArgumentParser(
        description="Tool for harness generation and proof debugging using DockerExecutor."
    )
    parser.add_argument(
        "mode",
        choices=["harness", "debugger", "function-stubs", "coverage", "all"],
        help=(
            "Execution mode: "
            "'harness' to generate harness/makefile, "
            "'debugger' to run proof debugger, "
            "'function-stubs' to run function stub generator, "
            "'coverage' to run coverage debugger, or "
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
    return parser.parse_args()


def process_mode(args):
    """ Process the mode selected in the CLI"""

    logger = setup_logger(__name__)

    logger.info("Running in '%s' mode.", args.mode)
    logger.info("Harness path: %s", args.harness_path)
    logger.info("Root directory: %s", args.root_dir)
    logger.info("Target function name: %s", args.target_function_name)
    logger.info("Target file path: %s", args.target_file_path)

    if args.mode in ["harness", "all"]:
        harness_dir = Path(args.harness_path)
        harness_dir.mkdir(parents=True, exist_ok=True)
        harness_generator = InitialHarnessGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        success = harness_generator.generate_harness()
        if not success:
            logger.error(
                "Error: Harness generation failed. Aborting makefile generation.")
            return
        makefile_generator = LLMMakefileGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        makefile_generator.generate_makefile()

    if args.mode in ["coverage", "all"]:
        coverage_debugger = CoverageDebugger(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        coverage_debugger.debug_coverage()
        
    if args.mode in ["stubs", "all"]:
        stub_generator = StubGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        success = stub_generator.generate_stubs()

    if args.mode in ["debugger", "all"]:
        proof_writer = ProofDebugger(
            harness_path=args.harness_path,
            root_dir=args.root_dir,
            target_function_name=args.target_function_name,
            project_container=project_container
        )
        harness_report = proof_writer.generate()
        proof_writer.generate_report()
        logger.info("Harness report: %s\n", harness_report)


def main():
    """Entry point"""
    global project_container
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    load_dotenv()

    args = get_parser()

    init_logging(Path(args.target_function_name).name)
    logger = setup_logger(__name__)

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if openai_api_key is None:
        raise EnvironmentError("No OpenAI API key found")

    container_name = f"autoup_{uuid.uuid4().hex[:8]}"
    project_container = ProjectContainer(
        dockerfile_path="tools.Dockerfile",
        host_dir=args.root_dir,
        container_name=container_name
    )
    try:
        project_container.initialize()
    except Exception as e:
        logger.error(f"Error initializing Project container: {e}")
        return
    process_mode(args)


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
