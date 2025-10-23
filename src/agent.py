import json
import subprocess
from openai import OpenAI
from abc import ABC, abstractmethod
import random
import time
import traceback
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

    def __init__(self, agent_name, project_container):
        self.agent_name = agent_name
        self.project_container: ProjectContainer = project_container
        self._max_attempts = 5

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

    def handle_tool_calls(self, function_name, function_args):
        logging_text = f"""
        Function call: 
        Name: {function_name} 
        Args: {function_args}
        """
        logger.info(logging_text)
        # Parse function_args string to dict
        function_args = json.loads(function_args)
        if function_name == "run_bash_command":
            cmd = function_args.get("cmd", "")
            tool_response = self.run_bash_command(cmd)
        elif function_name == "run_cscope_command":
            command = function_args.get("command", "")
            tool_response = self.run_bash_command(command)
        else:
            raise ValueError(f"Unknown function call: {function_name}")
        
        logger.info(f"Function call response: {tool_response}")
        return str(tool_response)

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
