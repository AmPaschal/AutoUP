"""
Makefile Generator Agent

This agent generates a working Makefile for CBMC proof harnesses by giving the
LLM the Makefile template upfront and a `makefile_validator` tool to iteratively
update and test the Makefile until compilation succeeds.
"""

import json
import os
import uuid
from typing import Optional

from agent import AIAgent
from commons.models import Generable
from makefile.output_models import MakefileFields
from commons.utils import Status
from logger import setup_logger

logger = setup_logger(__name__)


class MakefileGenerator(AIAgent, Generable):

    def __init__(self, args, project_container):
        super().__init__(
            "MakefileGenerator",
            args,
            project_container
        )
        self._max_attempts = 10

    def prepare_prompt(self):
        """Prepare system and user prompts for the LLM."""

        with open('prompts/makefile_generator_system.prompt', 'r') as f:
            system_prompt = f.read()

        with open('src/makefile/Makefile.example', 'r') as f:
            example_makefile = f.read()

        system_prompt = system_prompt.replace('{SAMPLE_MAKEFILE}', example_makefile)

        with open('prompts/makefile_generator_user.prompt', 'r') as f:
            user_prompt = f.read()

        makefile_content = self.get_makefile()
        harness_content = self.get_harness()

        user_prompt = user_prompt.replace('{TARGET_FUNC}', self.target_function)
        user_prompt = user_prompt.replace('{HARNESS_DIR}', self.harness_dir)
        user_prompt = user_prompt.replace('{PROJECT_DIR}', self.root_dir)
        user_prompt = user_prompt.replace('{MAKEFILE_CONTENT}', makefile_content)
        user_prompt = user_prompt.replace('{HARNESS_CONTENT}', harness_content)

        return system_prompt, user_prompt

    def get_makefile_tools(self):
        """Return the standard tools plus the makefile_validator tool."""
        makefile_validator_tool = {
            "type": "function",
            "name": "makefile_validator",
            "description": (
                "Update the Makefile with the provided content and run 'make compile' "
                "to test if the harness compiles successfully. Returns the compilation "
                "result including exit_code, stdout, and stderr."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason for updating the Makefile"
                    },
                    "makefile_content": {
                        "type": "string",
                        "description": "The complete updated Makefile content"
                    }
                },
                "required": ["reason", "makefile_content"],
                "additionalProperties": False
            }
        }

        return [*self.get_tools(), makefile_validator_tool]

    def handle_makefile_validator(self, makefile_content: str) -> dict:
        """
        Write the provided Makefile content to disk and run make compile.
        Returns the make results as a dict.
        """
        self.update_makefile(makefile_content)
        logger.info("Makefile updated via makefile_validator tool.")

        make_results = self.run_make(compile_only=True)
        return {
            "exit_code": make_results.get("exit_code", -1),
            "stdout": make_results.get("stdout", ""),
            "stderr": make_results.get("stderr", ""),
            "status": str(make_results.get("status", Status.ERROR))
        }

    def handle_tool_calls(self, tool_name, function_args):
        """Handle tool calls, including the new makefile_validator tool."""
        logging_text = f"""
        Function call: 
        Name: {tool_name} 
        Args: {function_args}
        """
        logger.info(logging_text)

        function_args = json.loads(function_args)

        if tool_name == "makefile_validator":
            makefile_content = function_args.get("makefile_content", "")
            tool_response = self.handle_makefile_validator(makefile_content)
        elif tool_name == "run_bash_command":
            cmd = function_args.get("cmd", "")
            tool_response = self.run_bash_command(cmd)
        elif tool_name == "run_cscope_command":
            command = function_args.get("command", "")
            tool_response = self.run_bash_command(command)
        else:
            raise ValueError(f"Unknown function call: {tool_name}")

        logger.info(f"Function call response:\n {tool_response}")
        return str(tool_response)

    def generate(self) -> bool:
        """
        Main entry point for the Makefile generator agent.

        Flow:
        1. Prepare prompt with Makefile template and harness
        2. Call chat_llm with tools (bash, cscope, makefile_validator)
           - The LLM explores the codebase and iteratively tests Makefiles
        3. Parse final structured response
        4. Run final validation (compile + target linked + target called)
        5. Return success/failure
        """
        attempts = 0

        system_prompt, user_prompt = self.prepare_prompt()
        tools = self.get_makefile_tools()

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []
        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)

        status = Status.ERROR

        while user_prompt and attempts < self._max_attempts:
            attempts += 1

            llm_response, llm_data = self.llm.chat_llm(
                system_prompt,
                user_prompt,
                MakefileFields,
                llm_tools=tools,
                call_function=self.handle_tool_calls,
                conversation_history=conversation
            )

            if not llm_response or not isinstance(llm_response, MakefileFields):
                self.log_task_attempt("makefile_generation", attempts, llm_data, "invalid_response")
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n"
                continue

            if not llm_response.updated_makefile and not llm_response.updated_harness:
                logger.error("The LLM gave up and decided it cannot resolve this error.")
                self.log_task_attempt("makefile_generation", attempts, llm_data, "no_modifications")
                status = Status.ERROR
                break

            # Apply the final response
            if llm_response.updated_makefile:
                self.update_makefile(llm_response.updated_makefile)
            if llm_response.updated_harness:
                self.update_harness(llm_response.updated_harness)

            # Final compilation check
            make_results = self.run_make(compile_only=True)
            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.FAILURE or make_results.get('exit_code', -1) != 0:
                logger.info("Final compilation failed; reprompting LLM.")
                user_prompt = (
                    "Your final Makefile still does not compile successfully.\n"
                    f"Exit Code: {make_results.get('exit_code', -1)}\n"
                    f"Stderr:\n{make_results.get('stderr', '')}\n"
                    "Please analyze the errors and provide an updated Makefile.\n"
                )
                self.log_task_attempt("makefile_generation", attempts, llm_data, "compilation_error")
                continue
            elif status_code in [Status.ERROR, Status.TIMEOUT]:
                logger.error("An error or timeout occurred when running make.")
                self.log_task_attempt("makefile_generation", attempts, llm_data, "make_error")
                status = status_code
                break

            logger.info("Makefile compilation succeeded. Running validation checks...")

            # Validate that the target function is correctly linked and called
            if not self.validate_linked_target() or not self.validate_called_target():
                logger.error("The target function is not linked or called in the compiled binary.")
                user_prompt = (
                    f"The generated harness does not call the function {self.target_function} "
                    f"in the file {self.target_file_path}.\n"
                    "Please update the harness or Makefile to ensure the correct function is called.\n"
                )
                self.log_task_attempt("makefile_generation", attempts, llm_data, "target_not_linked")
                continue

            if status_code == Status.SUCCESS:
                logger.info("Makefile successfully generated and compilation succeeded.")
                self.log_task_attempt("makefile_generation", attempts, llm_data, "")
                status = Status.SUCCESS
                break

            attempts += 1

        self.log_task_result("makefile_generation", status == Status.SUCCESS, attempts)

        if status != Status.SUCCESS:
            self.restore_backup(tag)
        self.discard_backup(tag)

        return status == Status.SUCCESS

    def _update_files_in_vector_store(self):
        pass
