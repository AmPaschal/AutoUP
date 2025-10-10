import signal
import sys
import argparse
import os
import logging
import json
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from debugger.debugger import LLMProofDebugger
from makefile.gen_makefile import LLMMakefileGenerator
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from commons.utils import Status
from commons.docker_tool import ProjectContainer
load_dotenv()

# Configure logging once, usually at the entry point of your program
logging.basicConfig(
    level=logging.INFO,  # Set minimum log level
    format="%(asctime)s [%(levelname)s] %(message)s"
)

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
        choices=["harness", "debugger"],
        help="Execution mode: 'harness' to generate harness/makefile, or 'debugger' to run proof debugger."
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
        "--target_func_path",
        help="Path to target function source file (required for harness mode)."
    )

    args = parser.parse_args()

    # ---- Validate mode-specific required arguments ----
    if args.mode == "harness":
        missing = [
            arg for arg in
            ["target_function_name", "root_dir", "target_func_path"]
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


def main():
    

    # -----------------
    # Parse arguments
    # -----------------
    args = get_parser()

    # Initialize Model API key
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    # Initialize Docker execution environment
    project_container = ProjectContainer("tools.Dockerfile", host_dir=args.root_dir, container_name="autoup_project_container")
    try:
        project_container.initialize()
    except Exception as e:
        logging.error(f"Error initializing Project container: {e}")
        sys.exit(1)

    # -----------------
    # Harness mode
    # -----------------
    if args.mode == "harness":
        logging.info(
            f"Running in harness mode with args: {args.target_function_name}, {args.root_dir}, {args.harness_path}, {args.target_func_path}"
        )

        harness_dir = Path(args.harness_path)
        harness_dir.mkdir(parents=True, exist_ok=True)

        # Generate harness
        harness_generator = InitialHarnessGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_func_path,
            project_container=project_container
        )
        success = harness_generator.generate_harness()
        if not success:
            logging.error("Error: Harness generation failed. Aborting makefile generation.")
            sys.exit(1)

        # Generate Makefile
        makefile_generator = LLMMakefileGenerator(
            root_dir=args.root_dir,
            harness_dir=args.harness_path,
            target_func=args.target_function_name,
            target_file_path=args.target_func_path,
            project_container=project_container
        )
        makefile_generator.generate_makefile()

    # -----------------
    # Debugger mode
    # -----------------
    elif args.mode == "debugger":
        logging.info(f"Running in debugger mode with arg: {args.harness_path}")
        proof_writer = LLMProofDebugger(openai_api_key, args.harness_path, test_mode=True)
        harness_report = proof_writer.iterate_proof(max_attempts=3)
        logging.info(f"Harness report:\n{harness_report}")

    project_container.terminate()


if __name__ == "__main__":
    main()
