
import sys
import os
import subprocess
import json
import shutil
import time
from typing import Any, Callable, Type
from dotenv import load_dotenv
from agent import AIAgent
from pathlib import Path
from makefile.output_models import MakefileFields
from commons.models import GPT, Generable
from commons.utils import Status
from logger import setup_logger

load_dotenv()
logger = setup_logger(__name__)

class LLMMakefileGenerator(AIAgent, Generable):


    def __init__(self, root_dir, harness_dir, target_func, target_file_path, metrics_file, project_container):
        super().__init__(
            "MakefileGenerator",
            project_container,
            metrics_file=metrics_file
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        
        self.root_dir = root_dir
        self.harness_dir = harness_dir
        self.target_func = target_func
        self.target_file_path = target_file_path
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

    def get_relative_path(self, base_path, target_path):
        """We want to get the relative path of target, in terms of how many ../ we need to get back to base"""
        base_path = Path(base_path).resolve()
        target_path = Path(target_path).resolve()
        return target_path.relative_to(base_path)
    
    def get_backward_path(self, base_path, target_path):
        """We want to get the relative path of target, in terms of how many ../ we need to get back to base"""
        relative_path = self.get_relative_path(base_path, target_path)

        up_levels = len(relative_path.parts)

        go_back = '/'.join([".."] * up_levels)

        return go_back
    
    def setup_initial_makefile(self):

        harness_relative_root = self.get_backward_path(self.root_dir, self.harness_dir)
        target_relative_root = self.get_relative_path(self.root_dir, self.target_file_path)

        with open('src/makefile/Makefile.template', 'r') as file:
            makefile = file.read()

        makefile = makefile.replace('{ROOT}', str(harness_relative_root))
        makefile = makefile.replace('{H_ENTRY}', self.target_func)
        makefile = makefile.replace('{LINK}', f'$(ROOT)/{target_relative_root}')

        return makefile

    def run_make(self, compile_only: bool = True) -> dict:
        make_cmd = "make compile -j4" if compile_only else "make -j4"
        make_results = self.execute_command(make_cmd, workdir=self.harness_dir, timeout=600)
        logger.info('Stdout:\n' + make_results.get('stdout', ''))
        logger.info('Stderr:\n' + make_results.get('stderr', ''))
        return make_results

    def update_makefile(self, makefile_content):
        with open(f'{self.harness_dir}/Makefile', 'w') as file:
            file.write(makefile_content)

    def update_harness(self, harness_code):
        harness_file_path = os.path.join(self.harness_dir, f'{self.target_func}_harness.c')
        
        with open(harness_file_path, 'w') as f:
            f.write(harness_code)

    def prepare_prompt(self, makefile_content, make_results):
        # Create the system prompt
        with open('prompts/gen_makefile_system.prompt', 'r') as file:
            system_prompt = file.read()

        with open('src/makefile/Makefile.example', 'r') as file:
            example_makefile = file.read()

        system_prompt = system_prompt.replace('{SAMPLE_MAKEFILE}', example_makefile)

        # Create the user prompt
        with open('prompts/gen_makefile_user.prompt', 'r') as file:
            user_prompt = file.read()

        user_prompt = user_prompt.replace('{TARGET_FUNC}', self.target_func)
        user_prompt = user_prompt.replace('{MAKEFILE_DIR}', self.harness_dir)
        user_prompt = user_prompt.replace('{PROJECT_DIR}', self.root_dir)
        user_prompt = user_prompt.replace('{MAKEFILE_CONTENT}', makefile_content)
        user_prompt = user_prompt.replace('{MAKE_ERROR}', make_results.get('stderr', ''))   

        return system_prompt, user_prompt

    def create_makefile_include(self):
        """Copy makefile.include from docker to harness parent directory"""
        src_path = os.path.join('makefiles', 'Makefile.include')
        dest_path = os.path.join(os.path.dirname(self.harness_dir), 'Makefile.include')
        if os.path.exists(dest_path):
            logger.info(f'Makefile.include already exists at {dest_path}, skipping copy.')
            return
        # Copy inside the container
        copy_cmd = f"cp {src_path} {dest_path}"
        copy_results = self.project_container.execute(copy_cmd, workdir='/')
        if copy_results.get('exit_code', -1) != 0:
            logger.error(f'Failed to copy Makefile.include: {copy_results.get("stderr", "")}')
            return
        logger.info(f'Copied Makefile.include to {dest_path}')

    def generate(self) -> bool:
        """
        Main function to generate the Makefile using the LLM.
        """
        
        # Copy makefile.include from docker to harness parent directory
        self.create_makefile_include()

        # First, we setup the initial Makefile
        makefile = self.setup_initial_makefile()
        self.update_makefile(makefile)
        
        # Next, we build and see if it succeeds
        make_results = self.run_make()

        attempts = 1

        system_prompt, user_prompt = self.prepare_prompt(makefile, make_results)
        tools = self.get_tools()

        logger.info(f'System Prompt:\n{system_prompt}')

        status = Status.ERROR

        conversation = []
        
        # Finally, we iteratively call the LLM to fix any errors until it succeeds
        while user_prompt and attempts <= self._max_attempts:

            logger.info(f'LLM Prompt:\n{user_prompt}')

            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, MakefileFields, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response:
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n"
                self.log_task_attempt("makefile_generation", attempts, llm_data, "invalid_response")
                continue

            logger.info(f'LLM Response:\n{json.dumps(llm_response.to_dict(), indent=2)}')
            self.update_makefile(llm_response.updated_makefile)
            if llm_response.updated_harness:
                self.update_harness(llm_response.updated_harness)
            make_results = self.run_make()
            

            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.SUCCESS and make_results.get('exit_code', -1) == 0:
                logger.info("Makefile successfully generated and compilation succeeded.")
                self.run_make(compile_only=False)  # Now run full make to generate proofs
                self.print_coverage(Path(self.harness_dir))
                self.log_task_attempt("makefile_generation", attempts, llm_data, "")
                status = Status.SUCCESS
                break
            elif status_code == Status.FAILURE:
                logger.info("Make command failed; reprompting LLM with make results.")

                # It's possible this is a new error, so we clear the conversation history
                # In future, we may want to detect if the previous error was resolved.
                system_prompt, user_prompt = self.prepare_prompt(llm_response.updated_makefile, make_results)
                self.log_task_attempt("makefile_generation", attempts, llm_data, "compilation_error")
            else:
                logger.error("Make command failed to run.")
                self.log_task_attempt("makefile_generation", attempts, llm_data, "make_error")
                status = status_code
                break

            attempts += 1

        self.log_task_result("makefile_generation", status == Status.SUCCESS, attempts)

        return status == Status.SUCCESS

    def _update_files_in_vector_store(self):
        pass

if __name__ == "__main__":

    if len(sys.argv) < 5:
        logger.error("Usage: python gen_makefile.py <target function> <root dir> <harness path> <file path>")
        sys.exit(1)

    target_function = sys.argv[1]
    root_dir = sys.argv[2]
    harness_path = sys.argv[3]
    file_path = sys.argv[4]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    makefile_generator = LLMMakefileGenerator(target_function, root_dir, harness_path, file_path, None, None)
    makefile_generator.generate()

