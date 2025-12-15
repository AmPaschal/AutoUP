
import sys
import os
import subprocess
import json
import shutil
import time
from typing import Any, Callable, Type
import uuid
from dotenv import load_dotenv
from agent import AIAgent
from pathlib import Path
from makefile.output_models import MakefileFields
from commons.models import GPT, Generable
from commons.utils import Status
from logger import setup_logger

load_dotenv()
logger = setup_logger(__name__)

class MakefileDebugger(AIAgent, Generable):


    def __init__(self, args, project_container):
        super().__init__(
            "MakefileGenerator",
            args,
            project_container
        )
        
        self._max_attempts = 10

    def get_coverage_dict(self, json_path: str) -> dict:
        with open(json_path, "r") as f:
            data = json.load(f)
        # Navigate to the overall_coverage section
        return data.get("viewer-coverage", {}).get("overall_coverage", {})

    def get_reachable_functions(self, json_path: str) -> dict:
        with open(json_path, "r") as f:
            data = json.load(f)
        reachable = data.get("viewer-reachable", {}).get("reachable", {})
        num_files = len(reachable)
        num_functions = sum(len(funcs) for funcs in reachable.values())
        return {"num_files": num_files, "num_functions": num_functions}

    def print_coverage(self, proof_dir: Path):
        print(f"Report for {proof_dir}:")
        report_path = os.path.join(proof_dir, "build/report/json")
        coverage_report = os.path.join(report_path, "viewer-coverage.json")
        if os.path.exists(coverage_report):
            coverage_dict = self.get_coverage_dict(coverage_report)
            print(f"Coverage:\n{coverage_dict}")
        reachability_report = os.path.join(report_path, "viewer-reachable.json")
        if os.path.exists(reachability_report):
            reachable_dict = self.get_reachable_functions(reachability_report)
            print(f"Reachable functions:\n{reachable_dict}")

    

    def run_make(self, compile_only: bool = True) -> dict:
        make_cmd = "make compile -j4" if compile_only else "make -j4"
        make_results = self.execute_command(make_cmd, workdir=self.harness_dir, timeout=600)
        logger.info('Stdout:\n' + make_results.get('stdout', ''))
        logger.info('Stderr:\n' + make_results.get('stderr', ''))
        return make_results

    def prepare_prompt(self, make_results):
        # Create the system prompt
        with open('prompts/gen_makefile_system.prompt', 'r') as file:
            system_prompt = file.read()

        with open('src/makefile/Makefile.example', 'r') as file:
            example_makefile = file.read()

        system_prompt = system_prompt.replace('{SAMPLE_MAKEFILE}', example_makefile)

        # Create the user prompt
        with open('prompts/gen_makefile_user.prompt', 'r') as file:
            user_prompt = file.read()

        makefile_content = self.get_makefile()
        harness_content = self.get_harness()

        user_prompt = user_prompt.replace('{TARGET_FUNC}', self.target_function)
        user_prompt = user_prompt.replace('{MAKEFILE_DIR}', self.harness_dir)
        user_prompt = user_prompt.replace('{PROJECT_DIR}', self.root_dir)
        user_prompt = user_prompt.replace('{MAKEFILE_CONTENT}', makefile_content)
        user_prompt = user_prompt.replace('{HARNESS_CONTENT}', harness_content)
        user_prompt = user_prompt.replace('{MAKE_ERROR}', make_results.get('stderr', ''))   

        return system_prompt, user_prompt

    def generate(self) -> bool:
        """
        Main function to generate the Makefile using the LLM.
        """

        # Next, we build and see if it succeeds
        make_results = self.run_make(compile_only=True)

        attempts = 1

        system_prompt, user_prompt = self.prepare_prompt(make_results)
        tools = self.get_tools()

        logger.info(f'System Prompt:\n{system_prompt}')

        status = Status.ERROR

        conversation = []

        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)
        
        # Finally, we iteratively call the LLM to fix any errors until it succeeds
        while user_prompt and attempts <= self._max_attempts:

            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, MakefileFields, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response:
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n"
                self.log_task_attempt("makefile_generation", attempts, llm_data, "invalid_response")
                continue

            if llm_response.updated_makefile:
                self.update_makefile(llm_response.updated_makefile)
            if llm_response.updated_harness:
                self.update_harness(llm_response.updated_harness)

            make_results = self.run_make(compile_only=True)

            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.SUCCESS and make_results.get('exit_code', -1) == 0:
                logger.info("Makefile successfully generated and compilation succeeded.")
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "")
                status = Status.SUCCESS
                break
            elif status_code == Status.FAILURE:
                logger.info("Make command failed; reprompting LLM with make results.")
                system_prompt, user_prompt = self.prepare_prompt(make_results)
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "compilation_error")
            else:
                logger.error("Make command failed to run.")
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "make_error")
                status = status_code
                break

            attempts += 1  

        self.log_task_result("makefile_debugger", status == Status.SUCCESS, attempts)

        if status != Status.SUCCESS:
            self.restore_backup(tag)
        self.discard_backup(tag)

        return status == Status.SUCCESS

    def _update_files_in_vector_store(self):
        pass

if __name__ == "__main__":

    if len(sys.argv) < 5:
        logger.error("Usage: python makefile_debugger.py <target function> <root dir> <harness path> <file path>")
        sys.exit(1)

    target_function = sys.argv[1]
    root_dir = sys.argv[2]
    harness_path = sys.argv[3]
    file_path = sys.argv[4]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    args = type("Args", (object,), {
        "target_function_name": target_function,
        "root_dir": root_dir,
        "harness_path": harness_path,
        "target_file_path": file_path,
        "metrics_file": None
    })()

    makefile_generator = MakefileDebugger(args, None)
    makefile_generator.generate()

