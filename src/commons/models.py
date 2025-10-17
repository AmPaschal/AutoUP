from abc import ABC, abstractmethod
import os
from pydantic import BaseModel
import tiktoken
import openai
import random
import time
import logging
import traceback
from typing import Any, Callable, Optional, Type

class LLM(ABC):

    name: str
    max_input_tokens: int

    def __init__(self, name: str, max_input_tokens: int):
        self.name = name
        self.max_input_tokens = max_input_tokens
        self._max_attempts = 5

    @abstractmethod
    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: BaseModel,
        llm_tools: list = [],
        call_function: Optional[Callable] = None
    ):
        pass

    def _delay_for_retry(self, attempt_count: int) -> None:
        """Sleeps for a while based on the |attempt_count|."""
        # Exponentially increase from 5 to 80 seconds + some random to jitter.
        delay = 5 * 2**attempt_count + random.randint(1, 5)
        logging.warning('Retry in %d seconds...', delay)
        time.sleep(delay)

    def _is_retryable_error(self, err: Exception,
                            api_errors: list[Type[Exception]],
                            tb: traceback.StackSummary) -> bool:
        """Validates if |err| is worth retrying."""
        if any(isinstance(err, api_error) for api_error in api_errors):
            return True

        # A known case from vertex package, no content due to mismatch roles.
        if (isinstance(err, ValueError) and
            'Content roles do not match' in str(err) and tb[-1].filename.endswith(
                'vertexai/generative_models/_generative_models.py')):
            return True

        # A known case from vertex package, content blocked by safety filters.
        if (isinstance(err, ValueError) and
            'blocked by the safety filters' in str(err) and
            tb[-1].filename.endswith(
                'vertexai/generative_models/_generative_models.py')):
            return True

        return False

    def with_retry_on_error(self, func: Callable,
                            api_errs: list[Type[Exception]]) -> Any:
        """
        Retry when the function returns an expected error with exponential backoff.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                return func()
            except Exception as err:
                logging.warning('LLM API Error when responding (attempt %d): %s',
                                attempt, err)
                tb = traceback.extract_tb(err.__traceback__)
                if (not self._is_retryable_error(err, api_errs, tb) or
                    attempt == self._max_attempts):
                    logging.warning(
                        'LLM API cannot fix error when responding (attempt %d) %s: %s',
                        attempt, err, traceback.format_exc())
                    raise err
                self._delay_for_retry(attempt_count=attempt)
        return None

    def _upload_vector_store_files(self, files_to_upload):
        """
        Actually upload the relevant files to the vector store
        This should be implemented in the subclass
        """
        pass

    def delete_by_file_type(self, file_type: str):
        """
        Delete all vector store files of a given type
        This should be implemented in the subclass
        """
        pass


class GPT(LLM):

    def __init__(self, name: str, max_input_tokens: int, agent_name: str = ''):
        super().__init__(name, max_input_tokens)
        openai_api_key = os.getenv("OPENAI_API_KEY", None)
        if not openai_api_key:
            raise EnvironmentError("No OpenAI API key found")
        self.client = openai.OpenAI(api_key=openai_api_key)
        self.store_name = f'{agent_name}-store'
        self.test_mode = True
        chunking_strategy={
                'type': 'static',
                'static': {
                    'chunk_overlap_tokens': 0, #I believe that having this as a non-zero value can cause hallucinations about file contents
                    'max_chunk_size_tokens': 800 # Unsure if this matters
                } 
            }
        self.vector_store = self._create_vector_store(chunking_strategy)

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: BaseModel,
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        parsed: bool = True
    ):
        # Start with the initial user input
        input_list = [{'role': 'user', 'content': input_messages}]

        while True:
            # Call the model
            client_response = self.with_retry_on_error(
                lambda: self.client.responses.parse(
                    model="gpt-5",
                    instructions=system_messages,
                    input=input_list,
                    text_format=output_format,
                    tool_choice="auto",
                    reasoning={"effort": "low"},
                    tools=llm_tools,
                    temperature=1.0,
                ),
                [openai.RateLimitError]
            )

            # Add model outputs to conversation state
            # This is a workaround for the issue https://github.com/openai/openai-python/issues/2374
            for item in client_response.output:
                if item.type == "function_call":
                    mapping = dict(item)
                    del mapping['parsed_arguments']
                    input_list.append(mapping)
                else:
                    input_list.append(item)

            # Find function calls
            function_calls = [item for item in client_response.output if item.type == "function_call"]

            if not function_calls:  
                # No function calls left → we’re done
                break

            # Handle each function call and add results back to input_list
            for item in function_calls:
                if call_function is None:
                    raise ValueError("call_function must be provided when tools are used.")
                function_result = call_function(item.name, item.arguments)
                input_list.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": function_result,
                })

        return client_response.output_parsed if parsed else client_response

    def get_vector_store_id(self):
        return self.vector_store.id

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

    def upload_vector_store_files(self, files_to_upload):
        """Uploads payload files to the vector store if none exist already."""
        curr_files = self.client.vector_stores.files.list(self.vector_store.id).data
        if curr_files:
            print(f"WARNING: Vector store {self.vector_store.id} already contains payload files")
            return

        for file_path, file_info in files_to_upload:
            with open(file_path, "rb") as f:
                new_file = self.client.vector_stores.files.upload_and_poll(
                    vector_store_id=self.vector_store.id,
                    file=f,
                )
            self.client.vector_stores.files.update(
                vector_store_id=self.vector_store.id,
                file_id=new_file.id,
                attributes={"type": file_info},
            )

    def delete_by_file_type(self, file_type: str):
        """Delete all vector store files of a given type."""
        files = self.client.vector_stores.files.list(self.vector_store.id).data
        for file in files:
            if file.attributes.get("type") == file_type:
                print(f"Deleting {file_type} file {file.id} from vector store {self.vector_store.id}")
                self.client.vector_stores.files.delete(
                    vector_store_id=self.vector_store.id,
                    file_id=file.id,
                )
                self.client.files.delete(file_id=file.id)
