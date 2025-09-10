import random
import re
import sys
import os
import subprocess
import json
import shutil
import logging
import time
import tiktoken
import traceback
import openai
from typing import Any, Callable, Type
from dotenv import load_dotenv
from agent import AIAgent
from pathlib import Path
from makefile.output_models import MakefileFields
from commons.models import GPT

load_dotenv()

class LLMMakefileGenerator(AIAgent):

    def __init__(self, root_dir, harness_dir, target_func, target_file_path):
        super().__init__(
            "MakefileGenerator"
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        
        self.root_dir = os.path.abspath(root_dir)
        self.harness_dir = os.path.abspath(harness_dir)
        self.target_func = target_func
        self.target_file_path = os.path.abspath(target_file_path)
        self._max_attempts = 5

    def truncate_result_custom(self, result, cmd: str, max_input_tokens: int, model: str) -> dict:
        """
        Truncates stdout and stderr of a result object to fit within a token limit.
        Rules:
            - If stderr > 50% of max tokens, truncate stderr first.
            - Otherwise, keep stderr in full and truncate stdout.
            - Replace truncated content with '[Truncated to fit context window]'.
        
        Args:
            result: The result object with attributes `returncode`, `stdout`, `stderr`.
            cmd (str): The executed command.
            max_input_tokens (int): Maximum total tokens allowed.
            model (str): Model name for tokenization.
        
        Returns:
            dict: Dictionary with truncated stdout/stderr and command info.
        """
        encoding = tiktoken.get_encoding("cl100k_base")
        
        stdout_tokens = encoding.encode(result.stdout)
        stderr_tokens = encoding.encode(result.stderr)

        total_tokens = len(stdout_tokens) + len(stderr_tokens)

        if total_tokens > 130000:
            max_input_tokens = 180000      
        
        trunc_msg = "[Truncated to fit context window]"
        trunc_msg_tokens = encoding.encode(trunc_msg)
        
        stderr_limit_threshold = max_input_tokens // 2
        
        if len(stderr_tokens) > stderr_limit_threshold:
            # Truncate stderr to 50% of max tokens
            allowed_stderr_tokens = stderr_limit_threshold - len(trunc_msg_tokens)
            truncated_stderr = encoding.decode(stderr_tokens[:allowed_stderr_tokens]) + " " + trunc_msg
            # Truncate stdout to fit remaining tokens
            remaining_tokens = max_input_tokens - len(encoding.encode(truncated_stderr))
            allowed_stdout_tokens = max(0, remaining_tokens - len(trunc_msg_tokens))
            truncated_stdout = encoding.decode(stdout_tokens[:allowed_stdout_tokens])
            if allowed_stdout_tokens < len(stdout_tokens):
                truncated_stdout += " " + trunc_msg
        else:
            # Keep stderr in full, truncate stdout to fit
            remaining_tokens = max_input_tokens - len(stderr_tokens)
            allowed_stdout_tokens = max(0, remaining_tokens - len(trunc_msg_tokens))
            truncated_stdout = encoding.decode(stdout_tokens[:allowed_stdout_tokens])
            truncated_stderr = result.stderr
            if allowed_stdout_tokens < len(stdout_tokens):
                truncated_stdout += " " + trunc_msg
        
        return {
            "cmd": cmd,
            "exit_code": result.returncode,
            "stdout": truncated_stdout,
            "stderr": truncated_stderr
        }


    def run_bash_command(self, cmd):
        """Run a command-line command and return the output."""
        try:
            logging.info(f"Running command: {cmd}")
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, check=True, cwd=self.root_dir
            )
            return self.truncate_result_custom(result, cmd, max_input_tokens=3000, model='gpt-5')
        except subprocess.CalledProcessError as e:
            print(f"Command failed with error:\n{e.stderr}")
            return None

    def run_make(self):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=self.harness_dir, timeout=60
            )
            logging.info('Stdout:\n' + result.stdout)
            logging.info('Stderr:\n' + result.stderr) 
            return {"error": 0, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                print("Make command timed out.")
                return {"error": -1}
            else:
                print(f"An error occurred while running make: {e}")
                return {"error": -2}
            
    def _upload_vector_store_files(self):
        upload_complete = self.client.vector_stores.files.upload_and_poll(
            vector_store_id=self.vector_store.id,
            file=open(self.func_file, "rb"),
            poll_interval_ms=1000,
        )
        return upload_complete

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
    
    def handle_tool_calls(self, function_name, function_args):
        logging_text = f"""
        Function call: 
        Name: {function_name} 
        Args: {function_args}
        """
        logging.info(logging_text)
        # Parse function_args string to dict
        function_args = json.loads(function_args)
        if function_name == "run_bash_command":
            cmd = function_args.get("cmd", "")
            tool_response = self.run_bash_command(cmd)
        else:
            raise ValueError(f"Unknown function call: {function_name}")
        
        logging.info(f"Function call response: {tool_response}")
        return str(tool_response)

    def get_tools(self):
        return [
            {
                "type": "function",
                "name": "run_bash_command",
                "description": "Run a command-line command to search the repo for relevant information, and return the output",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "The reason for running the command"
                        },
                        "cmd": {
                            "type": "string",
                            "description": "A bash command-line command to run"
                        }
                    },
                    "required": ["reason", "cmd"],
                    "additionalProperties": False
                }
            }
        ]
    
    def generate_makefile(self):
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

            if make_results.get('error', 0) == 0:
                if make_results.get('exit_code', -1) == 0:
                    print("Makefile successfully generated and build succeeded.")
                    break

                system_prompt, user_prompt = self.prepare_prompt(llm_response.updated_makefile, make_results)
            else:
                print("Make command failed to run.")
                break
                

        # For testing purposes, we restore the original Makefile
        if backup_path:
            self._restore_makefile(backup_path)

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
            print(f"Backup file {backup_path} does not exist. Cannot restore harness.")
            return
        
        makefile_path = os.path.join(self.harness_dir, 'Makefile')

        # If Makefile was generated, back it up with the timestamp
        if os.path.exists(makefile_path):
            timestamp = int(time.time())
            generated_backup_path = os.path.join(self.harness_dir, f'Makefile.{timestamp}.backup')
            shutil.copy(makefile_path, generated_backup_path)
            print(f"Backed up generated Makefile to {generated_backup_path}")

        shutil.copy(backup_path, makefile_path)
        print(f"Restored harness from {backup_path} to {self.harness_dir}")
        os.remove(backup_path)

    def _update_files_in_vector_store(self):
        pass

if __name__ == "__main__":

    if len(sys.argv) < 3:
        print("Usage: python gen_makefile.py <target function> <file path>")

    target_function = sys.argv[1]
    file_path = sys.argv[2]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    makefile_generator = LLMMakefileGenerator(target_function, os.getcwd(), file_path, openai_api_key)
    makefile_generator.generate_makefile()

