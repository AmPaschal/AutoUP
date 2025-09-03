import sys
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from debugger.debugger import LLMProofDebugger
from makefile.gen_makefile import LLMMakefileGenerator
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

    if mode == "makefile":
        if len(args) != 5:
            print("Error: 'makefile' mode requires args: <target_function_name> <harness_path> <target_func_path>.")
            sys.exit(1)
        _, arg1, arg2, arg3, arg4 = args
        print(f"Running in makefile mode with args: {arg1}, {arg2}, {arg3}")

        cwd = Path.cwd()

        generator = LLMMakefileGenerator(root_dir=arg2, harness_dir=arg3, target_func=arg1, target_file_path=arg4)
        # generator = LLMMakefileGenerator(target_func=arg1, harness_dir=(cwd / arg2).resolve(), target_file_path=(cwd / arg3).resolve(), openai_api_key=openai_api_key, test_mode=False)
        generator.generate_makefile()
    
    elif mode == "debugger":
        if len(args) != 2:
            print("Error: 'debugger' mode requires args: <harness_path>")
            sys.exit(1)
        _, arg1 = args
        print(f"Running in debugger mode with arg: {arg1}")
        proof_writer = LLMProofDebugger(openai_api_key, arg1, test_mode=True)
        harness_report = proof_writer.iterate_proof(max_attempts=3)
        print(harness_report)

    else:
        print("Error: First argument must be either 'makefile' or 'debugger'.")
        sys.exit(1)