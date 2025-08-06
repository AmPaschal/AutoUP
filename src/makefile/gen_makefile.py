import re
import sys
import os
import subprocess
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class LLMMakefileGenerator:

    SYSTEM_PROMPT = """
        Hello!
        """

    def __init__(self, target_func, target_file_path, openai_api_key):
        self.func = target_func
        self.harness_dir = os.getcwd()
        self.func_file = target_file_path
        self.client = OpenAI(api_key=openai_api_key)

    def run_bash_command(self, cmd):
        """Run a command-line command and return the output."""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True
            )
            return {"cmd": cmd, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except subprocess.CalledProcessError as e:
            print(f"Command failed with error:\n{e.stderr}")
            return None

    def run_make(self, harness_path):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=harness_path, timeout=60
            )
            return None
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                print("Make command timed out.")
                return "timeout"
            else:
                print(f"An error occurred while running make: {e}")
                return result.stderr


    def generate_makefile(self):

        # First, go through and insert anything we need programmatically

        makefile_lines = []
        with open('./Makefile.template', 'r') as file:
            for line in file.readlines():
                if line.startswith('LINK ='):
                    # Insert the file path
                    makefile_lines.append('LINK = ' + re.sub(r".*?\/(.*)", r"$(ROOT)/\1", file_path) + '\n')
                elif line.startswith('H_ENTRY ='):
                    # Insert the target function
                    makefile_lines.append(f'H_ENTRY = {target_function}\n')
                else:
                    makefile_lines.append(line)
        
        print('\n'.join(makefile_lines))
        
        # What information do we want to provide the LLM?

        # For now the only thing we'll provide it is the target file in a vector store


if __name__ == "__main__":

    if len(sys.argv) < 3:
        print("Usage: python gen_makefile.py <target function> <file path>")

    target_function = sys.argv[1]
    file_path = sys.argv[2]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    generate_makefile(target_function, file_path, openai_api_key)

