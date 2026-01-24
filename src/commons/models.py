from abc import ABC, abstractmethod
import os
from pydantic import BaseModel
import pydantic_core
import openai
import random
import time
import traceback
from typing import Any, Callable, Optional, Type
from openai.lib._pydantic import to_strict_json_schema
import litellm

from logger import setup_logger

logger = setup_logger(__name__)
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
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None
    ) -> Any:
        pass

    def _delay_for_retry(self, attempt_count: int) -> None:
        """Sleeps for a while based on the |attempt_count|."""
        # Exponentially increase from 5 to 80 seconds + some random to jitter.
        delay = 5 * 2**attempt_count + random.randint(1, 5)
        logger.warning('Retry in %d seconds...', delay)
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
                logger.warning('LLM API Error when responding (attempt %d): %s',
                                attempt, err)
                
                if "RateLimitError" in str(type(err)):
                    logger.warning("Rate Limit hit. Sleeping for 60 seconds to reset quota...")
                    time.sleep(60)
                    continue
                
                tb = traceback.extract_tb(err.__traceback__)
                if (not self._is_retryable_error(err, api_errs, tb) or
                    attempt == self._max_attempts):
                    logger.warning(
                        'LLM API cannot fix error when responding (attempt %d) %s: %s',
                        attempt, err, traceback.format_exc())
                    raise err
                self._delay_for_retry(attempt_count=attempt)
        return None

class GPT(LLM):

    def __init__(self, name: str, max_input_tokens: int, url: Optional[str] = None):
        super().__init__(name, max_input_tokens)
        openai_api_key = os.getenv("OPENAI_API_KEY", None)
        if not openai_api_key:
            raise EnvironmentError("No OpenAI API key found")
        self.client = openai.OpenAI(api_key=openai_api_key, base_url=url)

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):
        def build_response_format() -> dict:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": output_format.__name__,
                    "schema": to_strict_json_schema(output_format),
                    "strict": True,
                },
            }

        def parse_output(content: Optional[str]) -> Optional[BaseModel]:
            if not content:
                return None
            if hasattr(output_format, "model_validate_json"):
                return output_format.model_validate_json(content)
            return output_format.parse_raw(content)

        # Start with the initial user input
        new_message = {'role': 'user', 'content': input_messages}

        logger.info(f"LLM Prompt:\n{input_messages}")

        if conversation_history is None:
            conversation_history = []

        conversation_history.append(new_message)

        input_list = list(conversation_history)

        function_calls_count = 0
        parsed_output: BaseModel | None = None
        token_usage = {
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0
        }

        while True:
            # Call the model
            try:
                messages = [{"role": "system", "content": system_messages}, *input_list]
                client_response = self.with_retry_on_error(
                    lambda: self.client.chat.completions.create(
                        model=self.name,
                        messages=messages,
                        response_format=build_response_format(),
                        tool_choice="auto",
                        tools=llm_tools,
                        temperature=1.0,
                    ),
                    [openai.RateLimitError, pydantic_core._pydantic_core.ValidationError]
                )
            except openai.BadRequestError as bad_req_err:
                logger.error(f"Bad request error from LLM: {bad_req_err}")
                return None, {}
            except Exception as e:
                logger.error(f"Unexpected error from LLM: {e}")
                return None, {}

            # Update token usage
            if client_response.usage:
                token_usage["input_tokens"] += client_response.usage.prompt_tokens
                cached_tokens = (
                    client_response.usage.prompt_tokens_details.cached_tokens
                    if client_response.usage.prompt_tokens_details
                    else 0
                )
                token_usage["cached_tokens"] += cached_tokens or 0
                token_usage["output_tokens"] += client_response.usage.completion_tokens
                reasoning_tokens = (
                    client_response.usage.completion_tokens_details.reasoning_tokens
                    if client_response.usage.completion_tokens_details
                    else 0
                )
                token_usage["reasoning_tokens"] += reasoning_tokens or 0
                token_usage["total_tokens"] += client_response.usage.total_tokens

            message = client_response.choices[0].message
            tool_calls = message.tool_calls or []

            if not tool_calls:
                # No function calls left → we’re done
                parsed_output = parse_output(message.content)
                break

            # Handle each function call and add results back to input_list
            input_list.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ],
            })
            for tool_call in tool_calls:
                if call_function is None:
                    raise ValueError("call_function must be provided when tools are used.")
                function_result = call_function(
                    tool_call.function.name,
                    tool_call.function.arguments,
                )
                function_calls_count += 1
                input_list.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": function_result,
                })

        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({'role': 'assistant', 'content': str(parsed_output)})

        return parsed_output, {
            "function_call_count": function_calls_count,
            "token_usage": token_usage
        }


class Generable(ABC):
    """Generable interface """

    @abstractmethod
    def generate(self) -> bool:
        """Entry point for all generative agents"""
