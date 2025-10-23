import signal
import sys
import argparse
import os
import json
import uuid
import time
from pathlib import Path
from typing import Optional
import uuid
from dotenv import load_dotenv
from coverage_debugger.coverage_debugger import CoverageDebugger
from makefile.gen_makefile import LLMMakefileGenerator
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from debugger.debugger import ProofDebugger
from logger import init_logging, setup_logger

from commons.utils import Status
from commons.docker_tool import ProjectContainer
load_dotenv()


project_container: Optional[ProjectContainer] = None

project_container: Optional[ProjectContainer] = None


def cleanup(signum, frame):
    print("Caught signal, cleaning up container...")
    if project_container:
        project_container.terminate()
    sys.exit(1)

signal.signal(signal.SIGINT, cleanup)   # Ctrl+C
signal.signal(signal.SIGTERM, cleanup)  # `kill` command

def get_parser():
    parser = argparse.ArgumentParser(
        description="Tool for harness generation and proof debugging using DockerExecutor."
    )

    parser.add_argument(
        "mode",
        choices=["harness", "debugger", "coverage"],
        help="Execution mode: 'harness' to generate harness/makefile, 'debugger' to run proof debugger, or 'coverage' to run coverage debugger."
    )

    parser.add_argument(
        "--target_function_name",
        help="Target function name (required for harness mode)."
    )
    parser.add_argument(
        "--root_dir",
        help="Root directory of the project.",
        required=True
    )
    parser.add_argument(
        "--harness_path",
        required=True,
        help="Path to the harness directory."
    )
    parser.add_argument(
        "--target_file_path",
        help="Path to target function source file (required for harness mode)."
    )

    args = parser.parse_args()

    # ---- Validate mode-specific required arguments ----
    if args.mode == "harness":
        missing = [
            arg for arg in
            ["target_function_name", "root_dir", "target_file_path"]
            if getattr(args, arg) is None
        ]
        if missing:
            parser.error(
                f"'harness' mode requires the following arguments: {', '.join('--' + m for m in missing)}"
            )

    elif args.mode == "debugger":
        # For debugger mode, only harness_path is required (already enforced)
        pass

    return args

def process_mode(args, project_container: ProjectContainer, openai_api_key: str):
    logger = setup_logger(__name__)
    # -----------------autoup_project_container
    # Harness mode
    # -----------------
    if args.mode == "harness":
        logger.info(
            f"Running in harness mode with args: {args.target_function_name}, {args.root_dir}, {args.harness_path}, {args.target_file_path}"
        )

        harness_dir = Path(args.harness_path)
        harness_dir.mkdir(parents=True, exist_ok=True)

        # Generate harness
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
            sys.exit(1)

        # Generate Makefile
        makefile_generator = LLMMakefileGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        makefile_generator.generate_makefile()

    elif args.mode == "debugger":
        logger.info("Running in debugger mode.")
        logger.info("Harness path: %s", args.harness_path)
        logger.info("Root directory: %s", args.root_dir)
        logger.info("Target function name: %s", args.target_function_name)
        try:
            proof_writer = ProofDebugger(
                harness_path=args.harness_path,
                root_dir=args.root_dir,
                target_function_name=args.target_function_name,
                project_container=project_container
            )
            harness_report = proof_writer.generate()
            proof_writer.generate_report()
            logger.info("Harness report: %s\n", harness_report)
        except Exception as e:
            logger.error("Error in Proof debugger")
            logger.error(e)

    elif args.mode == "coverage":
        logger.info(f"Running in coverage debugger mode with arg: {args.harness_path}")
        coverage_debugger = CoverageDebugger(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_file_path,
            project_container=project_container
        )
        coverage_debugger.debug_coverage()

def main():

    global project_container
    
    # -----------------
    # Parse arguments
    # -----------------
    args = get_parser()
    init_logging(Path(args.target_function_name).name)
    logger = setup_logger(__name__)
    # Initialize Model API key
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    # Initialize Docker execution environment
    container_name = f"autoup_{uuid.uuid4().hex[:8]}"  # 8-character random string
    project_container = ProjectContainer(dockerfile_path="tools.Dockerfile", host_dir=args.root_dir, container_name=container_name)
    try:
        project_container.initialize()
    except Exception as e:
        logger.error(f"Error initializing Project container: {e}")
        return

    process_mode(args, project_container, openai_api_key)

    project_container.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # logger.error(f"Error occurred while running main: {e}")
        cleanup(None, None)
        raise e