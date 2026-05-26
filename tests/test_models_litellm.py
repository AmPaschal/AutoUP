import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
litellm_stub.completion = lambda **kwargs: None
sys.modules.setdefault("litellm", litellm_stub)

import commons.models as models

if not hasattr(models.litellm, "completion"):
    models.litellm.completion = lambda **kwargs: None


class DemoResponse(BaseModel):
    answer: str


class FakeToolFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = FakeToolFunction(name, arguments)


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]
        self.usage = None


class LiteLLMModelTests(unittest.TestCase):
    def test_chat_llm_uses_json_fallback_without_response_format(self):
        calls = []

        def fake_completion(**kwargs):
            calls.append(kwargs)
            return FakeResponse({
                "content": "{\"answer\": \"ok\"}",
                "tool_calls": None,
            })

        llm = models.LiteLLM(name="openrouter/demo", max_input_tokens=1024)
        with mock.patch.object(models.litellm, "completion", side_effect=fake_completion):
            parsed_output, metadata = llm.chat_llm("system", "user", DemoResponse)

        self.assertEqual(parsed_output.answer, "ok")
        self.assertEqual(metadata["model_name"], "openrouter/demo")
        self.assertNotIn("response_format", calls[0])
        self.assertNotIn("tools", calls[0])
        self.assertNotIn("tool_choice", calls[0])

    def test_chat_llm_translates_openai_tool_schema_for_litellm(self):
        calls = []
        responses = [
            FakeResponse({
                "content": None,
                "tool_calls": [FakeToolCall("call-1", "run_bash_command", "{\"cmd\": \"pwd\"}")],
            }),
            FakeResponse({
                "content": "{\"answer\": \"done\"}",
                "tool_calls": None,
            }),
        ]

        def fake_completion(**kwargs):
            calls.append(kwargs)
            return responses.pop(0)

        llm = models.LiteLLM(name="openrouter/demo", max_input_tokens=1024)
        tool_definition = {
            "type": "function",
            "name": "run_bash_command",
            "description": "Run a shell command",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string"},
                },
                "required": ["cmd"],
            },
        }

        with mock.patch.object(models.litellm, "completion", side_effect=fake_completion):
            parsed_output, metadata = llm.chat_llm(
                "system",
                "user",
                DemoResponse,
                llm_tools=[tool_definition],
                call_function=lambda name, arguments: "{\"ok\": true}",
            )

        self.assertEqual(parsed_output.answer, "done")
        self.assertEqual(metadata["function_call_count"], 1)
        translated_tool = calls[0]["tools"][0]
        self.assertEqual(translated_tool["type"], "function")
        self.assertEqual(translated_tool["function"]["name"], "run_bash_command")
        self.assertTrue(translated_tool["function"]["strict"])
        self.assertEqual(calls[0]["tool_choice"], "auto")
        self.assertNotIn("response_format", calls[0])

    def test_chat_llm_returns_failure_on_malformed_json(self):
        llm = models.LiteLLM(name="openrouter/demo", max_input_tokens=1024)
        with mock.patch.object(models.litellm, "completion", return_value=FakeResponse({
            "content": "not-json",
            "tool_calls": None,
        })):
            parsed_output, metadata = llm.chat_llm("system", "user", DemoResponse)

        self.assertIsNone(parsed_output)
        self.assertEqual(metadata, {})

    def test_chat_llm_returns_failure_on_malformed_tool_payload(self):
        llm = models.LiteLLM(name="openrouter/demo", max_input_tokens=1024)
        malformed_message = {
            "content": None,
            "tool_calls": [{"function": {"name": "run_bash_command", "arguments": "{}"}}],
        }
        with mock.patch.object(models.litellm, "completion", return_value=FakeResponse(malformed_message)):
            parsed_output, metadata = llm.chat_llm(
                "system",
                "user",
                DemoResponse,
                llm_tools=[{
                    "type": "function",
                    "name": "run_bash_command",
                    "parameters": {"type": "object", "properties": {}},
                }],
                call_function=lambda name, arguments: "{}",
            )

        self.assertIsNone(parsed_output)
        self.assertEqual(metadata, {})


if __name__ == "__main__":
    unittest.main()
