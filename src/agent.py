import json
import os
import time
import subprocess
from abc import ABC
from typing import Any, Callable, Type

import tiktoken

from commons.docker_tool import ProjectContainer
from logger import setup_logger
from commons.utils import Status

logger = setup_logger(__name__)

class AIAgent(ABC):
    """
    Shared features for any OpenAI agent that interacts with a vector store
    """

    def __init__(self, agent_name, project_container: ProjectContainer, harness_dir=None, metrics_file: str=""):
        self.agent_name = agent_name
        self.harness_dir = harness_dir
        self.project_container: ProjectContainer = project_container
        self._max_attempts = 5
        self.metrics_file = metrics_file

    def truncate_result_custom(self, result: dict, cmd: str, max_input_tokens: int, model: str) -> dict:
        """
        Truncates stdout and stderr of a result object to fit within a token limit.
        Rules:
            - If stderr > 50% of max tokens, truncate stderr first.
            - Otherwise, keep stderr in full and truncate stdout.
            - Replace truncated content with '[Truncated to fit context window]'.
        
        Args:
            result: The result object with attributes `exit_code`, `stdout`, `stderr`.
            cmd (str): The executed command.
            max_input_tokens (int): Maximum total tokens allowed.
            model (str): Model name for tokenization.
        
        Returns:
            dict: Dictionary with truncated stdout/stderr and command info.
        """
        encoding = tiktoken.get_encoding("cl100k_base")
        
        stdout_tokens = encoding.encode(result["stdout"])
        stderr_tokens = encoding.encode(result["stderr"])  
        
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
            truncated_stderr = result["stderr"]
            if allowed_stdout_tokens < len(stdout_tokens):
                truncated_stdout += " " + trunc_msg
        
        return {
            "cmd": cmd,
            "exit_code": result["exit_code"],
            "stdout": truncated_stdout,
            "stderr": truncated_stderr
        }


    def run_bash_command(self, cmd):
        """Run a command-line command and return the output."""
        try:
            logger.info(f"Running command: {cmd}")
            result = self.project_container.execute(cmd)
            return self.truncate_result_custom(result, cmd, max_input_tokens=10000, model='gpt-5')
        except subprocess.CalledProcessError as e:
            print(f"Command failed with error:\n{e.stderr}")
            return None
        
    def handle_condition_retrieval_tool(self, function_name, line_number):

        tool_response = {
            "success": False,
            "source_location": {
                "function": function_name,
                "line": line_number
            },
            "error": "",
            "results": ""
        }

        assert self.harness_dir is not None, "harness_dir must be set to use coverage debugger tools."

        # First, check if the coverage-mcdc.json file exists
        coverage_file_path = os.path.join(self.harness_dir, "build", "reports", "coverage-mcdc.json")
        if not os.path.exists(coverage_file_path):
            error_message = f"MC/DC Coverage file not found: {coverage_file_path}"
            tool_response["error"] = error_message
            logger.error(error_message)

            return tool_response

        try:
            with open(coverage_file_path, "r") as f:
                coverage_data = json.load(f)

        except Exception as e:
            error_message = f"Error reading MC/DC Coverage file: {e}"
            tool_response["error"] = error_message
            logger.error(error_message)
            return tool_response

        goals = []

        for item in coverage_data:
            if "goals" in item:
                goals = item["goals"]
                break

        if not goals:
            error_message = f"No condition coverage result found in MC/DC coverage data {coverage_file_path}."
            logger.error(error_message)
            tool_response["error"] = error_message
            return tool_response
        
        function_line_goals = [
            goal for goal in goals
            if goal.get("description", "").startswith("condition") and 
                goal.get("sourceLocation", {}).get("function") == function_name and 
                goal.get("sourceLocation", {}).get("line") == str(line_number)
        ]

        if not function_line_goals:
            error_message = f"No condition coverage goals found for line {line_number} in function '{function_name}'."
            logger.error(error_message)
            tool_response["error"] = error_message
            return tool_response

        tool_response["success"] = True
        tool_response["results"] = function_line_goals
        return tool_response

    def handle_tool_calls(self, tool_name, function_args):
        logging_text = f"""
        Function call: 
        Name: {tool_name} 
        Args: {function_args}
        """
        logger.info(logging_text)
        # Parse function_args string to dict
        function_args = json.loads(function_args)
        if tool_name == "run_bash_command":
            cmd = function_args.get("cmd", "")
            tool_response = self.run_bash_command(cmd)
        elif tool_name == "run_cscope_command":
            command = function_args.get("command", "")
            tool_response = self.run_bash_command(command)
        elif tool_name == "get_condition_satisfiability":
            function_name = function_args.get("function_name", "")
            line_number = function_args.get("line_number", -1)
            tool_response = self.handle_condition_retrieval_tool(function_name, line_number)
        else:
            raise ValueError(f"Unknown function call: {tool_name}")
        
        logger.info(f"Function call response:\n {tool_response}")
        return str(tool_response)

    def log_task_attempt(self, task_id, attempt_number, llm_data, error):
        if not self.metrics_file:
            return
        
        log_entry = {
            "type": "task_attempt",
            "agent_name": self.agent_name,
            "task_id": task_id,
            "attempt_number": attempt_number,
            "llm_data": llm_data,
            "error": error,
            "timestamp": time.time()
        }

        with open(self.metrics_file, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")

    def log_task_result(self, task_id, success: bool, total_attempts: int):
        if not self.metrics_file:
            return
        
        log_entry = {
            "type": "task_result",
            "agent_name": self.agent_name,
            "task_id": task_id,
            "success": success,
            "total_attempts": total_attempts,
            "timestamp": time.time()
        }

        with open(self.metrics_file, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")


    def execute_command(self, cmd: str, workdir: str, timeout: int) -> dict:
        try:
            result = self.project_container.execute(cmd, workdir=workdir, timeout=timeout)
            
            if result.get('exit_code', -1) == 124:
                logger.error(f"Command '{cmd}' timed out.")
                result['stdout'] += "[TIMEOUT]"
                result['status'] = Status.TIMEOUT
            elif result.get('exit_code', -1) == 0:
                logger.info(f"Command '{cmd}' completed successfully.")
                result['status'] = Status.SUCCESS
            else:
                logger.error(f"Command '{cmd}' failed.")
                result['status'] = Status.FAILURE
            return result
        except Exception as e:
            logger.error(f"An error occurred while running command '{cmd}': {e}")
            return {"status": Status.ERROR}

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
            },
            {
                "type": "function",
                "name": "run_cscope_command",
                "description": "Run a cscope command to search for type and function definitions, cross-references, and file paths.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "The reason for running the command"
                        },
                        "command": {
                            "type": "string",
                            "description": "A cscope command to run"
                        }
                    },
                    "required": ["reason", "command"],
                    "additionalProperties": False
                }
            }
        ]

    def get_coverage_tools(self):
        coverage_tools = [
            {
                "type": "function",
                "name": "get_condition_satisfiability",
                "description": "Retrieve the status and satisfiability of conditions present in a specific IF statement.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "The reason for executing this tool"
                        },
                        "function_name": {
                            "type": "string",
                            "description": "The name of the function containing the condition"
                        },
                        "line_number": {
                            "type": "integer",
                            "description": "The line number containing the condition in the source code"
                        }
                    },
                    "required": ["reason", "function_name", "line_number"],
                    "additionalProperties": False
                }
            }
        ]

        return [*self.get_tools(), *coverage_tools]
