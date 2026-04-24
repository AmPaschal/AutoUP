import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commons.models import MiniMaxChatCompletions
from makefile.output_models import HarnessResponse


class FakeResponse:
    def __init__(self, content, usage=None, tool_calls=None, reasoning_details=None):
        message = SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
            reasoning_details=reasoning_details,
        )
        message.model_dump = lambda exclude_none=True: {
            key: value
            for key, value in {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls or [],
                "reasoning_details": reasoning_details,
            }.items()
            if not exclude_none or value is not None
        }
        self.choices = [SimpleNamespace(message=message)]
        self.usage = usage


def build_minimax() -> MiniMaxChatCompletions:
    minimax = object.__new__(MiniMaxChatCompletions)
    minimax.name = "minimax/MiniMax-M2.5"
    minimax.model_name = "MiniMax-M2.5"
    minimax.max_input_tokens = 270000
    minimax._max_attempts = 5
    minimax._minimax_repair_attempts = 2
    minimax.client = None
    return minimax


class MiniMaxChatCompletionsTests(unittest.TestCase):
    def test_valid_harness_response_parses_directly(self):
        minimax = build_minimax()
        captured_kwargs = []
        response = FakeResponse(json.dumps({
            "analysis": "valid",
            "harness_code": "void harness(void) {}",
        }))

        def fake_request(*args, **kwargs):
            captured_kwargs.append(kwargs)
            return response

        minimax._request_completion = fake_request

        parsed, llm_data = minimax.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertEqual(parsed.analysis, "valid")
        self.assertEqual(llm_data["function_call_count"], 0)
        self.assertNotIn("response_format", captured_kwargs[0])

    def test_tool_round_trip_preserves_reasoning_details(self):
        minimax = build_minimax()
        captured_messages = []

        tool_call = SimpleNamespace(
            id="tool-1",
            function=SimpleNamespace(name="search_repo", arguments='{"query":"foo"}'),
        )
        responses = iter([
            FakeResponse(
                content="\n",
                tool_calls=[tool_call],
                reasoning_details=[{"type": "reasoning.text", "text": "first"}],
            ),
            FakeResponse(
                content="Tool result incorporated.",
                tool_calls=[],
                reasoning_details=[{"type": "reasoning.text", "text": "second"}],
            ),
            FakeResponse(json.dumps({
                "analysis": "done",
                "harness_code": "void harness(void) {}",
            })),
        ])

        def fake_request(system_messages, messages, **kwargs):
            captured_messages.append(messages)
            return next(responses)

        minimax._request_completion = fake_request

        parsed, llm_data = minimax.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
            llm_tools=[{"type": "function", "function": {"name": "search_repo"}}],
            call_function=lambda name, args: "tool-result",
        )

        self.assertEqual(parsed.analysis, "done")
        self.assertEqual(llm_data["function_call_count"], 1)
        second_turn_messages = captured_messages[1]
        assistant_messages = [m for m in second_turn_messages if m.get("role") == "assistant"]
        tool_turn_messages = [m for m in assistant_messages if m.get("tool_calls")]
        self.assertEqual(len(tool_turn_messages), 1)
        self.assertIn("reasoning_details", tool_turn_messages[0])
        self.assertEqual(tool_turn_messages[0]["reasoning_details"][0]["text"], "first")

    def test_invalid_json_triggers_repair_prompt(self):
        minimax = build_minimax()
        captured_messages = []

        responses = iter([
            FakeResponse("not valid json"),
            FakeResponse(json.dumps({
                "analysis": "repaired",
                "harness_code": "void harness(void) {}",
            })),
        ])

        def fake_request(system_messages, messages, **kwargs):
            captured_messages.append((messages, kwargs))
            return next(responses)

        minimax._request_completion = fake_request

        parsed, _ = minimax.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertEqual(parsed.analysis, "repaired")
        self.assertEqual(len(captured_messages), 2)
        self.assertNotIn("response_format", captured_messages[0][1])
        repair_turn_messages = captured_messages[1][0]
        self.assertEqual(repair_turn_messages[-2]["role"], "assistant")
        self.assertIn("not valid json", repair_turn_messages[-2]["content"])
        self.assertEqual(repair_turn_messages[-1]["role"], "user")
        self.assertIn("Return ONLY one valid JSON object instance.", repair_turn_messages[-1]["content"])

    def test_provider_exception_returns_none_with_stable_llm_data(self):
        minimax = build_minimax()

        def raise_provider_error(*args, **kwargs):
            raise RuntimeError("provider down")

        minimax._request_completion = raise_provider_error

        parsed, llm_data = minimax.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertIsNone(parsed)
        self.assertEqual(llm_data["function_call_count"], 0)
        self.assertIn("token_usage", llm_data)

    def test_model_prefix_strips_correctly(self):
        minimax = build_minimax()
        self.assertEqual(minimax.model_name, "MiniMax-M2.5")


if __name__ == "__main__":
    unittest.main()
