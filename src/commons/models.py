from abc import ABC, abstractmethod
import os
from pydantic import BaseModel
import pydantic_core
import tiktoken
import json
import openai
import random
import time
import traceback
from typing import Any, Callable, Optional, Type
from openai.types.responses.parsed_response import ParsedResponse
import litellm
from litellm import ModelResponse

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
                tb = traceback.extract_tb(err.__traceback__)
                if (not self._is_retryable_error(err, api_errs, tb) or
                    attempt == self._max_attempts):
                    logger.warning(
                        'LLM API cannot fix error when responding (attempt %d) %s: %s',
                        attempt, err, traceback.format_exc())
                    raise err
                self._delay_for_retry(attempt_count=attempt)
        return None

class LiteLLM(LLM):
    """LLM implementation using LiteLLM"""

    def __init__(self, name: str, max_input_tokens: int):
        super().__init__(name, max_input_tokens)

    @staticmethod
    def _convert_openai_tools_to_litellm_tools(openai_tools: list[dict]) -> list[dict]:
        converted_tools: list[dict] = []
        for tool in openai_tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                raise ValueError(f"Unsupported LiteLLM tool definition: {tool}")

            if "function" in tool:
                function_payload = tool["function"]
                if not isinstance(function_payload, dict):
                    raise ValueError(f"Malformed LiteLLM tool definition: {tool}")
                normalized_payload = dict(function_payload)
                if "strict" not in normalized_payload and "strict" in tool:
                    normalized_payload["strict"] = tool["strict"]
            else:
                name = tool.get("name")
                parameters = tool.get("parameters")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError(f"LiteLLM function tools must define a name: {tool}")
                if not isinstance(parameters, dict):
                    raise ValueError(f"LiteLLM function tools must define JSON-schema parameters: {tool}")
                normalized_payload = {
                    "name": name,
                    "parameters": parameters,
                }
                if "description" in tool:
                    normalized_payload["description"] = tool["description"]
                if "strict" in tool:
                    normalized_payload["strict"] = tool["strict"]

            converted_tools.append({
                "type": "function",
                "function": normalized_payload,
            })
        return converted_tools

    @staticmethod
    def _build_json_only_system_prompt(system_messages: str, output_format: Type[BaseModel]) -> str:
        json_schema = json.dumps(output_format.model_json_schema(), indent=2)
        return (
            f"{system_messages}\n\n"
            "Return only valid JSON matching this schema.\n"
            "Do not include markdown fences, commentary, or explanatory prose.\n"
            f"{json_schema}"
        )

    @staticmethod
    def _serialize_message(message: Any) -> dict:
        if hasattr(message, "model_dump"):
            dumped = message.model_dump(exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        if isinstance(message, dict):
            return dict(message)
        serialized = {
            "role": getattr(message, "role", "assistant"),
        }
        content = getattr(message, "content", None)
        if content is not None:
            serialized["content"] = content
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is not None:
            serialized["tool_calls"] = tool_calls
        return serialized

    @staticmethod
    def _extract_tool_calls(message: Any) -> list[Any]:
        if isinstance(message, dict):
            return list(message.get("tool_calls") or [])
        return list(getattr(message, "tool_calls", None) or [])

    @staticmethod
    def _extract_message_content(message: Any) -> Optional[str]:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)

        if isinstance(content, list):
            text_chunks = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_chunks.append(str(item.get("text", "")))
                elif hasattr(item, "text"):
                    text_chunks.append(str(item.text))
            return "".join(text_chunks) if text_chunks else None
        if content is None:
            return None
        return str(content)

    @staticmethod
    def _extract_tool_call_details(tool_call: Any) -> tuple[str, str, str]:
        call_id = getattr(tool_call, "id", None)
        if call_id is None and isinstance(tool_call, dict):
            call_id = tool_call.get("id")

        function_payload = getattr(tool_call, "function", None)
        if function_payload is None and isinstance(tool_call, dict):
            function_payload = tool_call.get("function")
        if function_payload is None:
            raise ValueError(f"Malformed LiteLLM tool call payload: {tool_call}")

        if isinstance(function_payload, dict):
            function_name = function_payload.get("name")
            function_arguments = function_payload.get("arguments")
        else:
            function_name = getattr(function_payload, "name", None)
            function_arguments = getattr(function_payload, "arguments", None)

        if not isinstance(call_id, str) or not call_id:
            raise ValueError(f"LiteLLM tool call is missing an id: {tool_call}")
        if not isinstance(function_name, str) or not function_name:
            raise ValueError(f"LiteLLM tool call is missing a function name: {tool_call}")
        if not isinstance(function_arguments, str):
            raise ValueError(f"LiteLLM tool call is missing JSON arguments: {tool_call}")
        return call_id, function_name, function_arguments

    @staticmethod
    def _strip_markdown_code_fences(content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            stripped = stripped[3:-3].strip()
            if stripped.startswith("json"):
                stripped = stripped[4:].strip()
        return stripped

    @staticmethod
    def _parse_json_content(content: str, output_format: Type[BaseModel]) -> BaseModel:
        json_text = LiteLLM._strip_markdown_code_fences(content)
        parsed = json.loads(json_text)
        return output_format.model_validate(parsed)

    @staticmethod
    def _update_litellm_usage(token_usage: dict[str, int], client_response: ModelResponse) -> None:
        if not client_response.usage:
            return
        token_usage["input_tokens"] += client_response.usage.prompt_tokens
        prompt_details = getattr(client_response.usage, "prompt_tokens_details", None)
        token_usage["cached_tokens"] += getattr(prompt_details, "cached_tokens", 0) or 0
        token_usage["output_tokens"] += client_response.usage.completion_tokens
        completion_details = getattr(client_response.usage, "completion_tokens_details", None)
        token_usage["reasoning_tokens"] += getattr(completion_details, "reasoning_tokens", 0) or 0
        token_usage["total_tokens"] += client_response.usage.total_tokens

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):
        # Start with the initial user input
        new_message = {'role': 'user', 'content': input_messages}

        logger.info(f"LLM Prompt:\n{input_messages}")

        if conversation_history is None:
            conversation_history = []

        conversation_history.append(new_message)

        input_list = list(conversation_history)

        function_calls_count = 0
        token_usage = {
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0
        }
        translated_tools = self._convert_openai_tools_to_litellm_tools(llm_tools) if llm_tools else []
        lite_system_prompt = self._build_json_only_system_prompt(system_messages, output_format)

        while True:
            # Call the model
            try:
                completion_kwargs: dict[str, Any] = {
                    "model": self.name,
                    "messages": [{"role": "system", "content": lite_system_prompt}] + input_list,
                    "reasoning": "low",
                    "temperature": 1.0,
                }
                if translated_tools:
                    completion_kwargs["tools"] = translated_tools
                    completion_kwargs["tool_choice"] = "auto"
                client_response: ModelResponse = self.with_retry_on_error(
                    lambda: litellm.completion(**completion_kwargs),
                    [pydantic_core._pydantic_core.ValidationError]
                )
            except Exception as e:
                logger.error(f"Unexpected error from LLM: {e}")
                return None, {}

            # Update token usage
            self._update_litellm_usage(token_usage, client_response)

            # Find function calls
            assistant_message = client_response.choices[0].message
            function_calls = self._extract_tool_calls(assistant_message)
        
            if not function_calls:
                # No function calls left → we’re done
                break

            # Handle each function call and add results back to input_list
            input_list.append(self._serialize_message(assistant_message))
            for item in function_calls:
                if call_function is None:
                    raise ValueError("call_function must be provided when tools are used.")
                try:
                    tool_call_id, function_name, function_arguments = self._extract_tool_call_details(item)
                except Exception as e:
                    logger.error(f"Malformed LiteLLM tool call payload: {e}")
                    return None, {}
                function_result = call_function(function_name, function_arguments)
                function_calls_count += 1
                input_list.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": function_result,
                })
        final_content = self._extract_message_content(client_response.choices[0].message)
        if not final_content:
            logger.error("LiteLLM returned no final JSON content.")
            return None, {}
        try:
            parsed_output = self._parse_json_content(final_content, output_format)
        except Exception as e:
            logger.error(f"LiteLLM returned invalid JSON content: {e}")
            return None, {}
        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({'role': 'assistant', 'content': str(parsed_output)})

        return parsed_output, {
            "model_name": self.name,
            "function_call_count": function_calls_count,
            "token_usage": token_usage
        }

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
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):
        # Start with the initial user input
        new_message = {'role': 'user', 'content': input_messages}

        logger.info(f"LLM Prompt:\n{input_messages}")

        if conversation_history is None:
            conversation_history = []

        conversation_history.append(new_message)

        input_list = list(conversation_history) 

        function_calls_count = 0
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
                client_response: ParsedResponse = self.with_retry_on_error(
                    lambda: self.client.responses.parse(
                        model=self.name,
                        instructions=system_messages,
                        input=input_list,
                        text_format=output_format,
                        tool_choice="auto",
                        reasoning={"effort": "medium"},
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
                token_usage["input_tokens"] += client_response.usage.input_tokens
                token_usage["cached_tokens"] += client_response.usage.input_tokens_details.cached_tokens
                token_usage["output_tokens"] += client_response.usage.output_tokens
                token_usage["reasoning_tokens"] += client_response.usage.output_tokens_details.reasoning_tokens
                token_usage["total_tokens"] += client_response.usage.total_tokens

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
                function_calls_count += 1
                input_list.append({
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": function_result,
                })

        parsed_output: BaseModel|None = client_response.output_parsed
        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({'role': 'assistant', 'content': str(parsed_output)})

        return parsed_output, {
            "model_name": self.name,
            "function_call_count": function_calls_count,
            "token_usage": token_usage
        }


class Generable(ABC):
    """Generable interface """

    @abstractmethod
    def generate(self) -> bool:
        """Entry point for all generative agents"""
