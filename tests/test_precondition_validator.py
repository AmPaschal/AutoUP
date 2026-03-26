import json
import sys
import types
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from commons.utils import Status
from debugger.error_report import CBMCError
from makefile.output_models import (
    PreconditionValidatorResponse,
    ValidationResult,
    ViolationType,
)
from validator.precondition_validator import PreconditionValidator


class DummyLLM:
    def __init__(self, responses, on_call=None):
        self._responses = list(responses)
        self._on_call = on_call

    def chat_llm(self, *args, **kwargs):
        if self._on_call is not None:
            self._on_call()
        if not self._responses:
            return None, {}
        return self._responses.pop(0), {"model_name": "dummy"}


def build_validation_result(violated, violation_type):
    return ValidationResult(
        precondition="__CPROVER_assume(x > 0)",
        parent_function="harness",
        violated=violated,
        violation_type=violation_type,
        reasoning="reasoning",
        detailed_analysis="analysis",
    )


def build_response(result, updated_harness=None):
    return PreconditionValidatorResponse(
        preconditions_analyzed=1,
        validation_result=[result],
        updated_harness=updated_harness,
    )


def build_error():
    error = CBMCError(
        {
            "line": "10",
            "msg": "dereference failure",
            "function": "target",
            "file": "target.c",
        }
    )
    error.error_id = "E1"
    return error


def make_validator(tmp_path, llm):
    validator = object.__new__(PreconditionValidator)
    validator.agent_name = "PreconditionValidator"
    validator.args = None
    validator.root_dir = str(tmp_path)
    validator.harness_dir = str(tmp_path)
    validator.target_function = "target"
    validator.target_file_path = str(tmp_path / "target.c")
    validator.metrics_file = None
    validator.project_container = None
    validator.harness_file_name = "target_harness.c"
    validator.harness_file_path = str(tmp_path / validator.harness_file_name)
    validator.makefile_path = str(tmp_path / "Makefile")
    validator.preconditions_analyzed = 0
    validator.num_tasks = 0
    validator.not_violated = 0
    validator.violated_total = 0
    validator.violation_counts = {violation.value: 0 for violation in ViolationType}
    validator.max_attempts = 3
    validator._current_error = None
    validator._current_coverage = {}
    validator._initial_property_count = -1
    validator._error_covered_initially = False
    validator._validated_harness_baseline = None
    validator.last_validation_response = None
    validator.llm = llm

    Path(validator.harness_file_path).write_text("original harness", encoding="utf-8")
    Path(validator.makefile_path).write_text("all:\n\t@true\n", encoding="utf-8")

    validator.prepare_prompt = lambda error, diff_output, analysis: ("system", "user")
    validator.get_overall_coverage = lambda: {"hit": 10, "percentage": 0.5}
    validator.get_property_count = lambda property_file_path=None: 5
    validator._is_error_covered = lambda error: True
    validator._is_error_solved = lambda error: True

    return validator


class PreconditionValidatorTests(unittest.TestCase):
    def test_validation_result_requires_null_violation_type_for_non_violated(self):
        with self.assertRaises(ValidationError):
            build_validation_result(False, ViolationType.EXPLOITABLE)

    def test_validation_result_accepts_all_supported_violation_types(self):
        for violation_type in ViolationType:
            with self.subTest(violation_type=violation_type.value):
                result = build_validation_result(True, violation_type)
                self.assertEqual(result.violation_type, violation_type)

    def test_validation_result_requires_violation_type_when_violated(self):
        with self.assertRaises(ValidationError):
            build_validation_result(True, None)

    def test_save_validation_result_excludes_updated_harness(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        validator = make_validator(tmp_path, DummyLLM([]))
        response = build_response(
            build_validation_result(True, ViolationType.INCOMPLETE),
            updated_harness="refined harness",
        )

        validator.save_validation_result(build_error(), response)

        persisted = (tmp_path / "validation_result.json").read_text(encoding="utf-8")
        self.assertNotIn("updated_harness", persisted)
        self.assertIn("incomplete", persisted)

    def test_validate_returns_failure_for_exploitable_violation(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        validator = make_validator(
            tmp_path,
            DummyLLM(
                [build_response(build_validation_result(True, ViolationType.EXPLOITABLE))]
            ),
        )

        status = validator.validate(build_error(), "diff", "analysis")

        self.assertEqual(status, Status.FAILURE)
        persisted = json.loads(
            (tmp_path / "validation_result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            persisted["validation_result"][0]["violation_type"], "exploitable"
        )
        self.assertNotIn("updated_harness", persisted)

    def test_validate_returns_success_for_non_exploitable_violation(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        validator = make_validator(
            tmp_path,
            DummyLLM(
                [build_response(build_validation_result(True, ViolationType.ANGELIC_ASSUMPTION))]
            ),
        )

        status = validator.validate(build_error(), "diff", "analysis")

        self.assertEqual(status, Status.SUCCESS)

    def test_irrelevant_requires_updated_harness(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        response = build_response(build_validation_result(True, ViolationType.IRRELEVANT))
        validator = make_validator(tmp_path, DummyLLM([response, response, response]))

        status = validator.validate(build_error(), "diff", "analysis")

        self.assertEqual(status, Status.ERROR)
        self.assertEqual(validator.get_harness(), "original harness")

    def test_handle_proof_validator_rejects_makefile_updates(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        validator = make_validator(tmp_path, DummyLLM([]))
        validator._validated_harness_baseline = validator.get_harness()

        result = validator.handle_proof_validator(
            harness_content="candidate",
            makefile_content="new makefile",
            compile_only=False,
        )

        self.assertFalse(result["compilation"]["success"])
        self.assertIn("Makefile updates are not allowed", result["message"])
        self.assertEqual(validator.get_harness(), "original harness")

    def test_validate_retries_and_restores_failed_candidate_then_keeps_successful_one(self):
        tmp_path = Path(self._testMethodName)
        tmp_path.mkdir(exist_ok=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_path, ignore_errors=True))

        first_response = build_response(
            ValidationResult(
                precondition="__CPROVER_assume(old > 0)",
                parent_function="harness",
                violated=True,
                violation_type=ViolationType.INCOMPLETE,
                reasoning="refine it",
                detailed_analysis="first attempt",
            ),
            updated_harness="candidate-1",
        )
        second_response = build_response(
            ValidationResult(
                precondition="__CPROVER_assume(refined > 0)",
                parent_function="harness",
                violated=True,
                violation_type=ViolationType.INCOMPLETE,
                reasoning="refined and validated",
                detailed_analysis="second attempt",
            ),
            updated_harness="candidate-2",
        )

        observed_harnesses = []
        validator = None
        llm = DummyLLM(
            [first_response, second_response],
            on_call=lambda: observed_harnesses.append(validator.get_harness()),
        )
        validator = make_validator(tmp_path, llm)
        validator.run_make = lambda compile_only=False: {
            "status": Status.SUCCESS,
            "exit_code": 0,
            "stderr": "",
            "stdout": "",
        }
        validator._is_error_solved = (
            lambda error: validator.get_harness() == "candidate-2"
        )
        validator._validated_harness_baseline = validator.get_harness()

        status = validator.validate(build_error(), "diff", "analysis")

        self.assertEqual(status, Status.SUCCESS)
        self.assertEqual(observed_harnesses, ["original harness", "original harness"])
        self.assertEqual(validator.get_harness(), "candidate-2")
        self.assertEqual(
            validator.last_validation_response.updated_harness, "candidate-2"
        )

        persisted = json.loads(
            (tmp_path / "validation_result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            persisted["validation_result"][0]["precondition"],
            "__CPROVER_assume(refined > 0)",
        )
        self.assertNotIn("updated_harness", persisted)


if __name__ == "__main__":
    unittest.main()
