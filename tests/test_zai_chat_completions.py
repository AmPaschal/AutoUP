import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from commons.models import ZAIChatCompletions
from debugger.output_models import ModelOutput
from makefile.output_models import HarnessResponse, PreconditionValidatorResponse


class FakeResponse:
    def __init__(self, content, usage=None, tool_calls=None):
        message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
        self.choices = [SimpleNamespace(message=message)]
        self.usage = usage


def build_zai() -> ZAIChatCompletions:
    zai = object.__new__(ZAIChatCompletions)
    zai.name = "zai/glm-5"
    zai.model_name = "glm-5"
    zai.max_input_tokens = 270000
    zai._max_attempts = 5
    zai._glm_repair_attempts = 2
    zai.client = None
    return zai


class ZAIChatCompletionsTests(unittest.TestCase):
    def test_valid_harness_response_parses_directly(self):
        zai = build_zai()
        metrics = zai._new_glm_response_metrics(HarnessResponse)
        content = json.dumps({
            "analysis": "valid",
            "harness_code": "void harness(void) {}",
        })

        parsed = zai._parse_glm_final_content(content, HarnessResponse, metrics)

        self.assertEqual(parsed.analysis, "valid")
        self.assertEqual(metrics["malformed_response_counts"], {})
        self.assertEqual(metrics["normalization_successes"], 0)

    def test_schema_wrapper_triggers_repair_and_recovers(self):
        zai = build_zai()
        responses = iter([
            FakeResponse(json.dumps({
                "type": "object",
                "title": "HarnessResponse",
                "properties": {
                    "analysis": {"type": "string"},
                    "harness_code": {"type": "string"},
                },
                "required": ["analysis", "harness_code"],
            })),
            FakeResponse(json.dumps({
                "analysis": "fixed",
                "harness_code": "void harness(void) {}",
            })),
        ])

        zai._request_glm_completion = lambda *args, **kwargs: next(responses)

        parsed, llm_data = zai.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertEqual(parsed.analysis, "fixed")
        metrics = llm_data["glm_response_metrics"]
        self.assertEqual(metrics["malformed_response_counts"]["schema_instead_of_instance"], 1)
        self.assertEqual(metrics["repair_successes"], 1)
        self.assertEqual(metrics["repair_attempts"], 1)

    def test_missing_required_modeloutput_field_triggers_repair(self):
        zai = build_zai()
        responses = iter([
            FakeResponse(json.dumps({
                "analysis": "missing field",
                "updated_harness": "void harness(void) {}",
            })),
            FakeResponse(json.dumps({
                "analysis": "fixed",
                "fix_recomendation": "add precondition",
                "updated_harness": "void harness(void) {}",
            })),
        ])

        zai._request_glm_completion = lambda *args, **kwargs: next(responses)

        parsed, llm_data = zai.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=ModelOutput,
        )

        self.assertEqual(parsed.fix_recomendation, "add precondition")
        metrics = llm_data["glm_response_metrics"]
        self.assertEqual(metrics["malformed_response_counts"]["missing_required_field"], 1)
        self.assertEqual(metrics["repair_successes"], 1)

    def test_stringified_validation_result_is_normalized(self):
        zai = build_zai()
        metrics = zai._new_glm_response_metrics(PreconditionValidatorResponse)
        content = json.dumps({
            "preconditions_analyzed": 1,
            "validation_result": [
                json.dumps({
                    "precondition": "x != NULL",
                    "parent_function": "harness",
                    "verdict": "VALID",
                    "untrusted_input_source": "",
                    "reasoning": "safe",
                    "detailed_analysis": "validated",
                })
            ],
        })

        parsed = zai._parse_glm_final_content(content, PreconditionValidatorResponse, metrics)

        self.assertEqual(parsed.validation_result[0].precondition, "x != NULL")
        self.assertEqual(metrics["malformed_response_counts"]["stringified_nested_object"], 1)
        self.assertEqual(metrics["normalization_successes"], 1)

    def test_empty_final_response_returns_failure_with_classification(self):
        zai = build_zai()
        responses = iter([
            FakeResponse(""),
            FakeResponse(""),
            FakeResponse(""),
        ])
        zai._request_glm_completion = lambda *args, **kwargs: next(responses)

        parsed, llm_data = zai.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertIsNone(parsed)
        metrics = llm_data["glm_response_metrics"]
        self.assertEqual(metrics["malformed_response_counts"]["empty_response"], 3)
        self.assertEqual(metrics["repair_attempts"], 2)

    def test_provider_exception_returns_failure_metrics(self):
        zai = build_zai()

        def raise_provider_error(*args, **kwargs):
            raise RuntimeError("provider down")

        zai._request_glm_completion = raise_provider_error

        parsed, llm_data = zai.chat_llm(
            system_messages="system",
            input_messages="user",
            output_format=HarnessResponse,
        )

        self.assertIsNone(parsed)
        metrics = llm_data["glm_response_metrics"]
        self.assertEqual(metrics["provider_error_count"], 1)
        self.assertEqual(metrics["malformed_response_counts"]["provider_error"], 1)


if __name__ == "__main__":
    unittest.main()
