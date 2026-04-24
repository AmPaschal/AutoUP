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
import types
from typing import Any, Callable, Optional, Type, Union, get_args, get_origin
from openai.types.responses.parsed_response import ParsedResponse
import litellm
from litellm import ModelResponse

from logger import setup_logger

logger = setup_logger(__name__)


class GLMResponseValidationError(Exception):
    """Raised when a GLM response cannot be normalized into the target schema."""

    def __init__(self, classification: str, detail: str, payload: Any = None):
        super().__init__(detail)
        self.classification = classification
        self.detail = detail
        self.payload = payload


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

class LiteLLM(LLM):
    """LLM implementation using LiteLLM"""

    def __init__(self, name: str, max_input_tokens: int):
        super().__init__(name, max_input_tokens)

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):

        #ENABLE PARAMETER DROPPING
        litellm.drop_params = True

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
                client_response: ModelResponse = self.with_retry_on_error(
                    lambda: litellm.completion(
                        model=self.name,
                        messages=[{"role": "system", "content": system_messages}] + input_list,
                        response_format=output_format,
                        tool_choice="auto",
                        reasoning_effort="low",
                        tools=llm_tools,
                        temperature=1.0,
                    ),
                    [pydantic_core._pydantic_core.ValidationError]
                )
            except Exception as e:
                logger.error(f"Unexpected error from LLM: {e}")
                return None, {}

            # Update token usage
            if client_response.usage:
                token_usage["input_tokens"] += client_response.usage.prompt_tokens
                
                # Safe check for cached tokens (input)
                if hasattr(client_response.usage, "prompt_tokens_details") and client_response.usage.prompt_tokens_details:
                    token_usage["cached_tokens"] += getattr(client_response.usage.prompt_tokens_details, "cached_tokens", 0)

                token_usage["output_tokens"] += client_response.usage.completion_tokens
                
                # Safe check for reasoning tokens (output)
                if hasattr(client_response.usage, "completion_tokens_details") and client_response.usage.completion_tokens_details:
                    token_usage["reasoning_tokens"] += getattr(client_response.usage.completion_tokens_details, "reasoning_tokens", 0)

                token_usage["total_tokens"] += client_response.usage.total_tokens

            # Find function calls
            function_calls = client_response.choices[0].message["tool_calls"] or []
        
            if not function_calls:
                # No function calls left → we’re done
                break
            
            # Append the Assistant's message (containing the tool calls) ONCE
            input_list.append(client_response.choices[0].message)

            # Handle each function call and add results back to input_list
            for item in function_calls:
                print("Functions to call: ", item.id)
                if call_function is None:
                    raise ValueError("call_function must be provided when tools are used.")
                function_result = call_function(item.function.name, item.function.arguments)
                function_calls_count += 1
                
                input_list.append({
                    "role": "tool",
                    "tool_call_id": item.id,
                    "content": function_result,
                })
                print("Tool call id responded: ", item.id)

        print(client_response.choices[0].message.content)
        parsed_output =output_format.model_validate(json.loads(client_response.choices[0].message.content))
        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({'role': 'assistant', 'content': str(parsed_output)})

        return parsed_output, {
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
                        reasoning={"effort": "low"},
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
            "function_call_count": function_calls_count,
            "token_usage": token_usage
        }


class ZAIChatCompletions(LLM):
    """LLM implementation using Z.AI's OpenAI-compatible chat completions API."""

    def __init__(self, name: str, max_input_tokens: int):
        super().__init__(name, max_input_tokens)
        zai_api_key = os.getenv("ZAI_API_KEY", None)
        if not zai_api_key:
            raise EnvironmentError("No Z.AI API key found")

        self.model_name = name.removeprefix("zai/")
        self.base_url = os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4/")
        self.client = openai.OpenAI(api_key=zai_api_key, base_url=self.base_url)
        self._glm_repair_attempts = 2

    def _update_token_usage(self, token_usage: dict, usage: Any) -> None:
        """Safely accumulate token usage from chat completions responses."""
        if not usage:
            return

        token_usage["input_tokens"] += getattr(usage, "prompt_tokens", 0)
        token_usage["output_tokens"] += getattr(usage, "completion_tokens", 0)
        token_usage["total_tokens"] += getattr(usage, "total_tokens", 0)

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details:
            token_usage["cached_tokens"] += getattr(prompt_details, "cached_tokens", 0)

        completion_details = getattr(usage, "completion_tokens_details", None)
        if completion_details:
            token_usage["reasoning_tokens"] += getattr(completion_details, "reasoning_tokens", 0)

    def _make_schema_prompt(self, output_format: Type[BaseModel]) -> str:
        """Build a JSON-only serialization prompt from a Pydantic model."""
        schema = output_format.model_json_schema()
        top_level_keys = list(schema.get("properties", {}).keys())
        example = self._schema_example_value(schema)
        return (
            "Return ONLY a JSON object instance. Do NOT return a JSON Schema.\n"
            "Do not include markdown fences or any extra commentary.\n"
            "Do not return keys like 'properties', 'required', 'type', '$defs', or 'title' "
            "unless they are actual response fields.\n"
            f"The top-level JSON object must contain these fields: {', '.join(top_level_keys)}.\n"
            "For code fields, include the complete file contents as a single JSON string.\n"
            "Use this example JSON shape:\n"
            f"{json.dumps(example, indent=2)}"
        )

    def _schema_example_value(self, schema: dict) -> Any:
        """Generate a simple JSON instance example from a JSON schema."""
        variants = schema.get("anyOf") or schema.get("oneOf")
        if variants:
            non_null_variant = next((item for item in variants if item.get("type") != "null"), variants[0])
            return self._schema_example_value(non_null_variant)

        if "enum" in schema:
            return schema["enum"][0]

        schema_type = schema.get("type")

        if schema_type == "object":
            return {
                key: self._schema_example_value(value)
                for key, value in schema.get("properties", {}).items()
            }
        if schema_type == "array":
            items = schema.get("items", {})
            return [self._schema_example_value(items)] if items else []
        if schema_type == "integer":
            return 0
        if schema_type == "number":
            return 0
        if schema_type == "boolean":
            return False
        if schema_type == "null":
            return None

        return "<string>"

    def _append_assistant_message(self, messages: list[dict], response_message: Any) -> None:
        """Append the assistant response to the local conversation state."""
        assistant_message = {
            "role": "assistant",
            "content": response_message.content or "",
        }

        tool_calls = getattr(response_message, "tool_calls", None)
        if tool_calls:
            assistant_message["tool_calls"] = [
                tool_call.model_dump() if hasattr(tool_call, "model_dump") else dict(tool_call)
                for tool_call in tool_calls
            ]

        messages.append(assistant_message)

    def _build_llm_data(self, function_calls_count: int, token_usage: dict,
                        glm_response_metrics: dict | None = None) -> dict:
        response = {
            "function_call_count": function_calls_count,
            "token_usage": token_usage,
        }
        if glm_response_metrics is not None:
            response["glm_response_metrics"] = glm_response_metrics
        return response

    def _new_glm_response_metrics(self, output_format: Type[BaseModel]) -> dict:
        return {
            "target_schema": output_format.__name__,
            "malformed_response_counts": {},
            "normalization_successes": 0,
            "repair_successes": 0,
            "provider_error_count": 0,
            "repair_attempts": 0,
        }

    def _increment_metric_count(self, counts: dict, key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    def _record_glm_failure(self, metrics: dict, classification: str, detail: str,
                            recovered_via: str | None = None) -> None:
        self._increment_metric_count(metrics["malformed_response_counts"], classification)
        log_message = (
            "GLM final response issue for schema '%s': %s. detail=%s recovered_via=%s"
        )
        logger.warning(
            log_message,
            metrics["target_schema"],
            classification,
            detail,
            recovered_via or "no",
        )

    def _record_glm_provider_error(self, metrics: dict, err: Exception, phase: str) -> None:
        metrics["provider_error_count"] += 1
        self._increment_metric_count(metrics["malformed_response_counts"], "provider_error")
        logger.warning(
            "GLM provider error for schema '%s' during %s: %s",
            metrics["target_schema"],
            phase,
            err,
        )

    def _request_glm_completion(self, system_messages: str, messages: list[dict], *,
                                temperature: float, response_format: dict | None = None,
                                tools: list | None = None, tool_choice: str | None = None) -> Any:
        return self.with_retry_on_error(
            lambda: self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "system", "content": system_messages}] + messages,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                temperature=temperature,
            ),
            [
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
            ],
        )

    def _required_top_level_fields(self, output_format: Type[BaseModel]) -> list[str]:
        schema = output_format.model_json_schema()
        return list(schema.get("required", []))

    def _top_level_fields(self, output_format: Type[BaseModel]) -> list[str]:
        schema = output_format.model_json_schema()
        return list(schema.get("properties", {}).keys())

    def _is_schema_wrapper_payload(self, payload: Any, output_format: Type[BaseModel]) -> bool:
        if not isinstance(payload, dict):
            return False

        expected_fields = set(self._top_level_fields(output_format))
        schema_keys = {"properties", "required", "type", "title", "$defs"}
        payload_keys = set(payload.keys())
        return "properties" in payload and payload_keys.issubset(schema_keys) and not (payload_keys & expected_fields)

    def _missing_required_fields(self, payload: Any, output_format: Type[BaseModel]) -> list[str]:
        if not isinstance(payload, dict):
            return []

        return [
            field for field in self._required_top_level_fields(output_format)
            if field not in payload
        ]

    def _normalize_glm_value(self, value: Any, annotation: Any) -> tuple[Any, bool, bool]:
        if value is None:
            return value, False, False

        origin = get_origin(annotation)
        if origin in (list,):
            if not isinstance(value, list):
                return value, False, False

            args = get_args(annotation)
            item_annotation = args[0] if args else Any
            normalized_items = []
            changed = False
            stringified_object = False
            for item in value:
                normalized_item, item_changed, item_stringified = self._normalize_glm_value(
                    item, item_annotation
                )
                normalized_items.append(normalized_item)
                changed = changed or item_changed
                stringified_object = stringified_object or item_stringified
            return normalized_items, changed, stringified_object

        if origin in (dict,):
            return value, False, False

        if origin in (types.UnionType, Union):
            for candidate in [arg for arg in get_args(annotation) if arg is not type(None)]:
                normalized_value, changed, stringified_object = self._normalize_glm_value(
                    value, candidate
                )
                if changed or stringified_object:
                    return normalized_value, changed, stringified_object
            return value, False, False

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if isinstance(value, str):
                try:
                    decoded_value = json.loads(value)
                except json.JSONDecodeError:
                    return value, False, False
                if not isinstance(decoded_value, dict):
                    return value, False, False
                normalized_value, changed, nested_stringified = self._normalize_glm_model_payload(
                    decoded_value, annotation
                )
                return normalized_value, True, True

            if isinstance(value, dict):
                return self._normalize_glm_model_payload(value, annotation)

        return value, False, False

    def _normalize_glm_model_payload(self, payload: dict, model_cls: Type[BaseModel]) -> tuple[dict, bool, bool]:
        normalized_payload = dict(payload)
        changed = False
        stringified_object = False

        for field_name, field_info in model_cls.model_fields.items():
            if field_name not in normalized_payload:
                continue

            normalized_value, value_changed, value_stringified = self._normalize_glm_value(
                normalized_payload[field_name], field_info.annotation
            )
            if value_changed:
                normalized_payload[field_name] = normalized_value
                changed = True
            stringified_object = stringified_object or value_stringified

        return normalized_payload, changed, stringified_object

    def _validate_glm_payload(self, payload: Any, output_format: Type[BaseModel],
                              metrics: dict) -> BaseModel:
        if self._is_schema_wrapper_payload(payload, output_format):
            detail = "Model returned a JSON Schema wrapper instead of a response instance."
            self._record_glm_failure(metrics, "schema_instead_of_instance", detail)
            raise GLMResponseValidationError("schema_instead_of_instance", detail, payload)

        missing_fields = self._missing_required_fields(payload, output_format)
        if missing_fields:
            detail = f"Missing required top-level fields: {', '.join(missing_fields)}"
            self._record_glm_failure(metrics, "missing_required_field", detail)
            raise GLMResponseValidationError("missing_required_field", detail, payload)

        normalized_payload = payload
        normalization_changed = False
        stringified_nested_object = False
        if isinstance(payload, dict):
            normalized_payload, normalization_changed, stringified_nested_object = (
                self._normalize_glm_model_payload(payload, output_format)
            )

        if stringified_nested_object:
            self._record_glm_failure(
                metrics,
                "stringified_nested_object",
                "Nested JSON objects were wrapped in strings.",
                recovered_via="normalization",
            )

        try:
            parsed_output = output_format.model_validate(normalized_payload)
        except pydantic_core._pydantic_core.ValidationError as e:
            classification = "stringified_nested_object" if stringified_nested_object else "validation_error"
            if classification == "validation_error":
                self._record_glm_failure(metrics, classification, str(e))
            raise GLMResponseValidationError(classification, str(e), normalized_payload) from e

        if normalization_changed:
            metrics["normalization_successes"] += 1
            logger.info(
                "GLM normalization succeeded for schema '%s'.",
                metrics["target_schema"],
            )

        return parsed_output

    def _parse_glm_final_content(self, final_content: str, output_format: Type[BaseModel],
                                 metrics: dict) -> BaseModel:
        if not final_content:
            detail = "Z.AI returned an empty final response."
            self._record_glm_failure(metrics, "empty_response", detail)
            raise GLMResponseValidationError("empty_response", detail)

        try:
            payload = json.loads(final_content)
        except json.JSONDecodeError as e:
            self._record_glm_failure(metrics, "invalid_json", str(e))
            raise GLMResponseValidationError("invalid_json", str(e), final_content) from e

        return self._validate_glm_payload(payload, output_format, metrics)

    def _glm_schema_specific_rules(self, output_format: Type[BaseModel]) -> str:
        schema_name = output_format.__name__
        if schema_name == "HarnessResponse":
            return "Required top-level keys: analysis, harness_code."
        if schema_name == "ModelOutput":
            return (
                "All top-level keys are mandatory. Required top-level keys: "
                "analysis, fix_recomendation, updated_harness."
            )
        if schema_name == "PreconditionValidatorResponse":
            return (
                "Required top-level keys: preconditions_analyzed, validation_result. "
                "validation_result must be a JSON array of objects, not strings."
            )
        return f"Required top-level keys: {', '.join(self._top_level_fields(output_format))}."

    def _build_glm_repair_prompt(self, output_format: Type[BaseModel],
                                 invalid_payload: Any, validation_error: str) -> str:
        top_level_keys = self._top_level_fields(output_format)
        example = self._schema_example_value(output_format.model_json_schema())
        payload_text = json.dumps(invalid_payload, indent=2) if invalid_payload is not None else "<empty response>"
        return (
            "Your previous response did not match the required format.\n"
            f"Target schema: {output_format.__name__}\n"
            f"Expected top-level keys only: {', '.join(top_level_keys)}\n"
            f"{self._glm_schema_specific_rules(output_format)}\n"
            "Return an instance, not a schema.\n"
            "Do not wrap objects in strings.\n"
            "Return ONLY a valid JSON object with no markdown fences and no extra commentary.\n"
            f"Validation error:\n{validation_error}\n"
            f"Previous invalid payload:\n{payload_text}\n"
            f"Minimal valid example:\n{json.dumps(example, indent=2)}"
        )

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):
        new_message = {"role": "user", "content": input_messages}

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
        glm_response_metrics = self._new_glm_response_metrics(output_format)

        if llm_tools:
            while True:
                try:
                    client_response = self._request_glm_completion(
                        system_messages,
                        input_list,
                        tools=llm_tools,
                        tool_choice="auto",
                        temperature=1.0,
                    )
                except openai.BadRequestError as bad_req_err:
                    logger.error(f"Bad request error from LLM: {bad_req_err}")
                    return None, self._build_llm_data(
                        function_calls_count,
                        token_usage,
                        glm_response_metrics,
                    )
                except Exception as e:
                    self._record_glm_provider_error(glm_response_metrics, e, "tool_call")
                    logger.error(f"Unexpected error from LLM: {e}")
                    return None, self._build_llm_data(
                        function_calls_count,
                        token_usage,
                        glm_response_metrics,
                    )

                self._update_token_usage(token_usage, getattr(client_response, "usage", None))

                response_message = client_response.choices[0].message
                tool_calls = getattr(response_message, "tool_calls", None) or []

                self._append_assistant_message(input_list, response_message)

                if not tool_calls:
                    break

                for item in tool_calls:
                    if call_function is None:
                        raise ValueError("call_function must be provided when tools are used.")
                    function_result = call_function(item.function.name, item.function.arguments)
                    function_calls_count += 1
                    input_list.append({
                        "role": "tool",
                        "tool_call_id": item.id,
                        "content": function_result,
                    })

        final_prompt = self._make_schema_prompt(output_format)
        final_messages = input_list + [{"role": "user", "content": final_prompt}]
        repair_messages = list(final_messages)
        final_content = None
        parsed_output = None

        for attempt in range(self._glm_repair_attempts + 1):
            try:
                final_response = self._request_glm_completion(
                    system_messages,
                    repair_messages,
                    response_format={"type": "json_object"},
                    temperature=1.0 if attempt == 0 else 0.0,
                )
            except openai.BadRequestError as bad_req_err:
                logger.error(f"Bad request error from LLM: {bad_req_err}")
                return None, self._build_llm_data(
                    function_calls_count,
                    token_usage,
                    glm_response_metrics,
                )
            except Exception as e:
                self._record_glm_provider_error(glm_response_metrics, e, "final_response")
                logger.error(f"Unexpected error from LLM: {e}")
                return None, self._build_llm_data(
                    function_calls_count,
                    token_usage,
                    glm_response_metrics,
                )

            self._update_token_usage(token_usage, getattr(final_response, "usage", None))
            final_content = final_response.choices[0].message.content

            try:
                parsed_output = self._parse_glm_final_content(
                    final_content,
                    output_format,
                    glm_response_metrics,
                )
                if attempt > 0:
                    glm_response_metrics["repair_successes"] += 1
                    logger.info(
                        "GLM repair succeeded for schema '%s' after %i repair attempt(s).",
                        glm_response_metrics["target_schema"],
                        attempt,
                    )
                break
            except GLMResponseValidationError as e:
                if attempt == self._glm_repair_attempts:
                    logger.error(f"Failed to parse Z.AI JSON response: {e.detail}")
                    return None, self._build_llm_data(
                        function_calls_count,
                        token_usage,
                        glm_response_metrics,
                    )

                glm_response_metrics["repair_attempts"] += 1
                repair_prompt = self._build_glm_repair_prompt(
                    output_format,
                    e.payload,
                    e.detail,
                )
                repair_messages = repair_messages + [
                    {"role": "assistant", "content": final_content or ""},
                    {"role": "user", "content": repair_prompt},
                ]

        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({"role": "assistant", "content": str(parsed_output)})

        return parsed_output, self._build_llm_data(
            function_calls_count,
            token_usage,
            glm_response_metrics,
        )


class MiniMaxChatCompletions(LLM):
    """LLM implementation using MiniMax's OpenAI-compatible chat completions API."""

    def __init__(self, name: str, max_input_tokens: int):
        super().__init__(name, max_input_tokens)
        minimax_api_key = os.getenv("MINIMAX_API_KEY", None)
        if not minimax_api_key:
            raise EnvironmentError("No MiniMax API key found")

        self.model_name = name.removeprefix("minimax/")
        self.base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
        self.client = openai.OpenAI(api_key=minimax_api_key, base_url=self.base_url)
        self._minimax_repair_attempts = 2

    def _build_llm_data(self, function_calls_count: int, token_usage: dict) -> dict:
        return {
            "function_call_count": function_calls_count,
            "token_usage": token_usage,
        }

    def _update_token_usage(self, token_usage: dict, usage: Any) -> None:
        if not usage:
            return

        token_usage["input_tokens"] += getattr(usage, "prompt_tokens", 0)
        token_usage["output_tokens"] += getattr(usage, "completion_tokens", 0)
        token_usage["total_tokens"] += getattr(usage, "total_tokens", 0)

        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details:
            token_usage["cached_tokens"] += getattr(prompt_details, "cached_tokens", 0)

        completion_details = getattr(usage, "completion_tokens_details", None)
        if completion_details:
            token_usage["reasoning_tokens"] += getattr(completion_details, "reasoning_tokens", 0)

    def _request_completion(self, system_messages: str, messages: list[dict], *,
                            temperature: float, response_format: dict | None = None,
                            tools: list | None = None, tool_choice: str | None = None) -> Any:
        return self.with_retry_on_error(
            lambda: self.client.chat.completions.create(**{
                "model": self.model_name,
                "messages": [{"role": "system", "content": system_messages}] + messages,
                "temperature": temperature,
                "extra_body": {"reasoning_split": True},
                **({"tools": tools} if tools is not None else {}),
                **({"tool_choice": tool_choice} if tool_choice is not None else {}),
                **({"response_format": response_format} if response_format is not None else {}),
            }),
            [
                openai.RateLimitError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.InternalServerError,
                pydantic_core._pydantic_core.ValidationError,
            ],
        )

    def _serialize_assistant_message(self, response_message: Any) -> dict:
        if hasattr(response_message, "model_dump"):
            return response_message.model_dump(exclude_none=True)
        if hasattr(response_message, "dict"):
            return response_message.dict(exclude_none=True)
        return {
            key: value
            for key, value in dict(response_message).items()
            if value is not None
        }

    def _top_level_fields(self, output_format: Type[BaseModel]) -> list[str]:
        schema = output_format.model_json_schema()
        return list(schema.get("properties", {}).keys())

    def _schema_example_value(self, schema: dict) -> Any:
        variants = schema.get("anyOf") or schema.get("oneOf")
        if variants:
            non_null_variant = next((item for item in variants if item.get("type") != "null"), variants[0])
            return self._schema_example_value(non_null_variant)

        if "enum" in schema:
            return schema["enum"][0]

        schema_type = schema.get("type")
        if schema_type == "object":
            return {
                key: self._schema_example_value(value)
                for key, value in schema.get("properties", {}).items()
            }
        if schema_type == "array":
            items = schema.get("items", {})
            return [self._schema_example_value(items)] if items else []
        if schema_type == "integer":
            return 0
        if schema_type == "number":
            return 0
        if schema_type == "boolean":
            return False
        if schema_type == "null":
            return None

        return "<string>"

    def _make_schema_prompt(self, output_format: Type[BaseModel]) -> str:
        schema = output_format.model_json_schema()
        example = self._schema_example_value(schema)
        top_level_keys = self._top_level_fields(output_format)
        return (
            "Return ONLY one valid JSON object instance matching the requested output.\n"
            "Do not include markdown fences, prose, comments, or any text before or after the JSON.\n"
            "Do not return a JSON Schema.\n"
            f"The top-level JSON object must contain these fields: {', '.join(top_level_keys)}.\n"
            "For code fields, include the complete file contents as a single JSON string.\n"
            "Use this example JSON shape:\n"
            f"{json.dumps(example, indent=2)}"
        )

    def _build_repair_prompt(self, output_format: Type[BaseModel], invalid_payload: str,
                             validation_error: str) -> str:
        example = self._schema_example_value(output_format.model_json_schema())
        top_level_keys = self._top_level_fields(output_format)
        return (
            "Your previous response was not valid for the required output format.\n"
            f"Expected top-level keys: {', '.join(top_level_keys)}\n"
            "Return ONLY one valid JSON object instance.\n"
            "Do not return a JSON Schema.\n"
            "Do not include markdown fences, prose, or comments.\n"
            f"Validation error:\n{validation_error}\n"
            f"Previous response:\n{invalid_payload}\n"
            f"Minimal valid example:\n{json.dumps(example, indent=2)}"
        )

    def _parse_final_output(self, final_content: Any, output_format: Type[BaseModel]) -> BaseModel:
        if not final_content:
            raise ValueError("MiniMax returned an empty final response.")

        payload = json.loads(final_content)
        return output_format.model_validate(payload)

    def chat_llm(
        self,
        system_messages: str,
        input_messages: str,
        output_format: Type[BaseModel],
        llm_tools: list = [],
        call_function: Optional[Callable] = None,
        conversation_history: Optional[list] = None
    ):
        new_message = {"role": "user", "content": input_messages}

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

        if llm_tools:
            while True:
                try:
                    client_response = self._request_completion(
                        system_messages,
                        input_list,
                        temperature=1.0,
                        tools=llm_tools,
                        tool_choice="auto",
                    )
                except openai.BadRequestError as bad_req_err:
                    logger.error(f"Bad request error from LLM: {bad_req_err}")
                    return None, self._build_llm_data(function_calls_count, token_usage)
                except Exception as e:
                    logger.error(f"Unexpected error from LLM: {e}")
                    return None, self._build_llm_data(function_calls_count, token_usage)

                self._update_token_usage(token_usage, getattr(client_response, "usage", None))

                response_message = client_response.choices[0].message
                tool_calls = getattr(response_message, "tool_calls", None) or []

                input_list.append(self._serialize_assistant_message(response_message))

                if not tool_calls:
                    break

                for item in tool_calls:
                    if call_function is None:
                        raise ValueError("call_function must be provided when tools are used.")
                    function_result = call_function(item.function.name, item.function.arguments)
                    function_calls_count += 1
                    input_list.append({
                        "role": "tool",
                        "tool_call_id": item.id,
                        "content": function_result,
                    })

        final_messages = input_list + [{
            "role": "user",
            "content": self._make_schema_prompt(output_format),
        }]
        repair_messages = list(final_messages)
        parsed_output = None

        for attempt in range(self._minimax_repair_attempts + 1):
            try:
                final_response = self._request_completion(
                    system_messages,
                    repair_messages,
                    temperature=0.1,
                )
            except openai.BadRequestError as bad_req_err:
                logger.error(f"Bad request error from LLM: {bad_req_err}")
                return None, self._build_llm_data(function_calls_count, token_usage)
            except Exception as e:
                logger.error(f"Unexpected error from LLM: {e}")
                return None, self._build_llm_data(function_calls_count, token_usage)

            self._update_token_usage(token_usage, getattr(final_response, "usage", None))

            final_content = final_response.choices[0].message.content
            try:
                parsed_output = self._parse_final_output(final_content, output_format)
                break
            except (ValueError, TypeError, json.JSONDecodeError, pydantic_core._pydantic_core.ValidationError) as e:
                if attempt == self._minimax_repair_attempts:
                    logger.error(f"Failed to parse MiniMax JSON response: {e}")
                    return None, self._build_llm_data(function_calls_count, token_usage)

                repair_messages = repair_messages + [
                    {
                        "role": "assistant",
                        "content": final_content or "",
                    },
                    {
                        "role": "user",
                        "content": self._build_repair_prompt(
                            output_format,
                            final_content or "<empty response>",
                            str(e),
                        ),
                    },
                ]

        parsed_output_dict = parsed_output.model_dump_json(indent=2) if parsed_output else {}
        logger.info(f"LLM Response:\n{parsed_output_dict}")

        conversation_history.append({"role": "assistant", "content": str(parsed_output)})

        return parsed_output, self._build_llm_data(function_calls_count, token_usage)


class Generable(ABC):
    """Generable interface """

    @abstractmethod
    def generate(self) -> bool:
        """Entry point for all generative agents"""
