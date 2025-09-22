from abc import ABC, abstractmethod
import os
from pydantic import BaseModel
import tiktoken
import openai
import random
import time
import logging
import traceback
from typing import Any, Callable, Type

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
        call_function: Callable = None
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


class GPT(LLM):

    def __init__(self, name: str, max_input_tokens: int):
        super().__init__(name, max_input_tokens)
        openai_api_key = os.getenv("OPENAI_API_KEY", None)
        if not openai_api_key:
            raise EnvironmentError("No OpenAI API key found")
        self.client = openai.OpenAI(api_key=openai_api_key)

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: BaseModel,
        llm_tools: list = [],
        call_function: Callable = None
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
            input_list += client_response.output

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

        return client_response.output_parsed
