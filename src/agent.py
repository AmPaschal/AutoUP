import json
import subprocess
from openai import OpenAI
from abc import ABC, abstractmethod
import random
import time
import logging
import traceback
from typing import Any, Callable, Type

import tiktoken

class AIAgent(ABC):
    """
    Shared features for any OpenAI agent that interacts with a vector store
    """

    def __init__(self, agent_name, project_container, test_mode=False, chunking_strategy=None):
        self.agent_name = agent_name
        self.project_container = project_container
        self.store_name = f'{agent_name}-store'
        self.test_mode = test_mode
        self._max_attempts = 5
        self.root_dir = None
        # self.vector_store = self._create_vector_store(chunking_strategy)

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
            logging.info(f"Running command: {cmd}")
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
        logging.info(logging_text)
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
    
    def _create_vector_store(self, chunking_strategy):
        """
        Checks if a vector store already exists
        If it does already exist, it will clear all files inside and return the existing vector store
        """

        for store in self.client.vector_stores.list():
            if store.name == self.store_name:
                print(f"Found existing vector store with ID {store.id}")
                if self.test_mode:
                    print(f"Cleaning up old vector store {store.id} for testing")
                    self.vector_store = store
                    self._cleanup_vector_store()
                else:
                    return store

        print(f"Initializing vector store for {self.store_name}")
        vector_store = self.client.vector_stores.create(
            name=self.store_name,
            chunking_strategy=chunking_strategy # If chunking_strategy is None, it will use OpenAI's default chunking strategy (max_chunk_size_tokens = 800, chunk_overlap_tokens = 400)
        )
        
        return vector_store

    def _upload_vector_store_files(self, file_path):
        """
        Actually upload the relevant files to the vector store
        This should be implemented in the subclass
        """
        pass
    
    def _update_files_in_vector_store(self):
        """
        Update any files that may have changed locally in the vector store
        Not every agent will necessarily need to use this, so some subclasses may just leave this as an empty function
        """
        pass

    def _cleanup_vector_store(self):
        """
        Deletes the vector store and all files associated with the tag name
        Then moves the updated harness into a different file and restores the original harness file from the backup
        """
        if self.vector_store.id not in [store.id for store in self.client.vector_stores.list()]:
            return

        file_ids = self.client.vector_stores.files.list(self.vector_store.id)
        for file in file_ids:
            print(f"Deleting file {file.id} from vector store {self.vector_store.id}")
            self.client.files.delete(file_id=file.id)

        self.client.vector_stores.delete(self.vector_store.id)
        print(f"Deleted vector store {self.vector_store.id} for {self.store_name}")