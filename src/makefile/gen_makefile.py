
import sys
import os
import subprocess
import json
import shutil
import logging
import time
from typing import Any, Callable, Type
from dotenv import load_dotenv
from agent import AIAgent
from pathlib import Path
from makefile.output_models import MakefileFields
from commons.models import GPT
from commons.utils import Status

load_dotenv()

class LLMMakefileGenerator(AIAgent):

    def __init__(self, root_dir, harness_dir, target_func, target_file_path, project_container):
        super().__init__(
            "MakefileGenerator",
            project_container
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        
        self.root_dir = os.path.abspath(root_dir)
        self.harness_dir = os.path.abspath(harness_dir)
        self.target_func = target_func
        self.target_file_path = os.path.abspath(target_file_path)
        self._max_attempts = 10

    def run_make(self):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=self.harness_dir, timeout=150
            )
            logging.info('Stdout:\n' + result.stdout)
            logging.info('Stderr:\n' + result.stderr) 
            return {"error": Status.SUCCESS, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                logging.error("Make command timed out.")
                if e.stdout:
                    logging.info('Partial stdout:\n' + str(e.stdout))
                if e.stderr:
                    logging.info('Partial stderr:\n' + str(e.stderr))
                return {"error": Status.TIMEOUT}
            else:
                logging.error(f"An error occurred while running make: {e}")
                return {"error": Status.FAILURE}

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

    def update_makefile(self, makefile_content):
        with open(f'{self.harness_dir}/Makefile', 'w') as file:
            file.write(makefile_content)

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

    def generate_makefile(self) -> Status:
        """
        Main function to generate the Makefile using the LLM.
        """
        
        # For testing purposes, backup existing Makefile
        backup_path = self._backup_makefile()

        # First, we setup the initial Makefile
        makefile = self.setup_initial_makefile()
        self.update_makefile(makefile)
        
        # Next, we build and see if it succeeds
        make_results = self.run_make()

        attempts = 0

        system_prompt, user_prompt = self.prepare_prompt(makefile, make_results)
        tools = self.get_tools()

        logging.info(f'System Prompt:\n{system_prompt}')

        status = Status.ERROR
        
        # Finally, we iteratively call the LLM to fix any errors until it succeeds
        while user_prompt and attempts < self._max_attempts:

            logging.info(f'LLM Prompt:\n{user_prompt}')
            
            llm_response = self.llm.chat_llm(system_prompt, user_prompt, MakefileFields, llm_tools=tools, call_function=self.handle_tool_calls)
            if not llm_response:
                user_prompt += "\nThe LLM did not return a valid response. Please try again."
                continue

            logging.info(f'LLM Response:\n{json.dumps(llm_response.to_dict(), indent=2)}')
            self.update_makefile(llm_response.updated_makefile)
            make_results = self.run_make()
            attempts += 1

            error_code = make_results.get('error', Status.ERROR)

            if error_code == Status.SUCCESS:
                if make_results.get('exit_code', -1) == 0:
                    logging.info("Makefile successfully generated and build succeeded.")
                    status = Status.SUCCESS
                    break

                system_prompt, user_prompt = self.prepare_prompt(llm_response.updated_makefile, make_results)
            else:
                logging.error("Make command failed to run.")
                status = error_code
                break
                

        # For testing purposes, we restore the original Makefile
        if backup_path:
            self._restore_makefile(backup_path)

        return status

    def _backup_makefile(self, backup_suffix='temp'):
        """
        Create an unmodified copy of the harness file that we can restore,
        but only if the Makefile exists.
        """
        if not os.path.exists(os.path.join(self.harness_dir, 'Makefile')):
            return None
        backup_path = os.path.join(self.harness_dir, 'Makefile.backup')
        shutil.copy(os.path.join(self.harness_dir, 'Makefile'), backup_path)
        return backup_path

    def _restore_makefile(self, backup_path):
        if not os.path.exists(backup_path):
            logging.info(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return
        
        makefile_path = os.path.join(self.harness_dir, 'Makefile')

        # If Makefile was generated, back it up with the timestamp
        if os.path.exists(makefile_path):
            timestamp = int(time.time())
            generated_backup_path = os.path.join(self.harness_dir, f'Makefile.{timestamp}.backup')
            shutil.copy(makefile_path, generated_backup_path)
            logging.info(f"Backed up generated Makefile to {generated_backup_path}")

        shutil.copy(backup_path, makefile_path)
        logging.info(f"Restored harness from {backup_path} to {self.harness_dir}")
        os.remove(backup_path)

    def _update_files_in_vector_store(self):
        pass

if __name__ == "__main__":

    if len(sys.argv) < 5:
        logging.error("Usage: python gen_makefile.py <target function> <root dir> <harness path> <file path>")
        sys.exit(1)

    target_function = sys.argv[1]
    root_dir = sys.argv[2]
    harness_path = sys.argv[3]
    file_path = sys.argv[4]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    makefile_generator = LLMMakefileGenerator(target_function, root_dir, harness_path, file_path, None)
    makefile_generator.generate_makefile()

