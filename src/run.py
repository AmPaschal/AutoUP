import sys
import os
import logging
import json
from pathlib import Path
from dotenv import load_dotenv
from debugger.debugger import LLMProofDebugger
from makefile.gen_makefile import LLMMakefileGenerator
from initial_harness_generator.gen_harness import InitialHarnessGenerator
from commons.utils import Status
load_dotenv()

# Configure logging once, usually at the entry point of your program
logging.basicConfig(
    level=logging.INFO,  # Set minimum log level
    format="%(asctime)s [%(levelname)s] %(message)s"
)

if __name__ == "__main__":
    args = sys.argv[1:]  # skip the script name

    if not args:
        print("Error: Missing arguments. First argument must be 'makefile' or 'debugger'.")
        sys.exit(1)
    
    mode = args[0]

    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    if mode == "harness":
        if len(args) != 5:
            logging.error("Error: 'harness' mode requires args: <target_function_name> <root_dir> <harness_path> <target_func_path>.")
            sys.exit(1)
        _, arg1, arg2, arg3, arg4 = args
        logging.info(f"Running in harness mode with args: {arg1}, {arg2}, {arg3}, {arg4}")

        cwd = Path.cwd()

        # If harness dir does not exist, create it
        harness_dir = Path(arg3)
        harness_dir.mkdir(parents=True, exist_ok=True)

        # First, we generate the harness
        harness_generator = InitialHarnessGenerator(root_dir=arg2, harness_dir=arg3, target_func=arg1, target_file_path=arg4)
        success = harness_generator.generate_harness()
        if not success:
            logging.error("Error: Harness generation failed. Aborting makefile generation.")
            sys.exit(1)

        # Then, we generate the Makefile
        makefile_generator = LLMMakefileGenerator(root_dir=arg2, harness_dir=arg3, target_func=arg1, target_file_path=arg4)
        makefile_generator.generate_makefile()

        

    elif mode == "debugger":
        if len(args) != 2:
            logging.error("Error: 'debugger' mode requires args: <harness_path>")
            sys.exit(1)
        _, arg1 = args
        logging.info(f"Running in debugger mode with arg: {arg1}")
        proof_writer = LLMProofDebugger(openai_api_key, arg1, test_mode=True)
        harness_report = proof_writer.iterate_proof(max_attempts=3)
        logging.info(f"Harness report:\n{harness_report}")

    else:
        logging.error("Error: First argument must be either 'harness' or 'debugger'.")
        sys.exit(1)