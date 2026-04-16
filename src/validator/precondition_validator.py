import json
import logging
import os
import re
import uuid
from typing import Optional

from agent import AIAgent
from commons.models import Generable
from commons.utils import Status
from debugger.error_report import CBMCError
from debugger.parser import get_json_errors
from makefile.output_models import (
    PreconditionValidatorResponse,
    ValidationResult,
    ViolationType,
)

logger = logging.getLogger(__name__)


class PreconditionValidator(AIAgent, Generable):
    def __init__(self, args, project_container):
        super().__init__(
            "PreconditionValidator",
            args,
            project_container,
        )

        self.preconditions_analyzed = 0
        self.num_tasks = 0
        self.not_violated = 0
        self.violated_total = 0
        self.violation_counts = {violation.value: 0 for violation in ViolationType}
        self.max_attempts = 3

        self._current_error: Optional[CBMCError] = None
        self._current_coverage: dict = {}
        self._initial_property_count: int = -1
        self._error_covered_initially: bool = False
        self._validated_harness_baseline: Optional[str] = None
        self.last_validation_response: Optional[PreconditionValidatorResponse] = None
        self._validation_records: list[dict] = []

    def extract_preconditions(self, harness_path):
        """
        Extracts __CPROVER_assume statements from the harness file.
        Returns a list of precondition strings.
        """
        if not os.path.exists(harness_path):
            logger.error(f"[ERROR] Harness file not found: {harness_path}")
            return []

        with open(harness_path, "r", encoding="utf-8") as file:
            content = file.read()

        preconditions = re.findall(r"__CPROVER_assume\((.*?)\);", content, re.DOTALL)
        return [precondition.strip() for precondition in preconditions]

    def get_property_count(self, property_file_path: str = None) -> int:
        if property_file_path is None:
            property_file_path = os.path.join(
                self.harness_dir, "build", "report", "json", "viewer-property.json"
            )

        if not os.path.exists(property_file_path):
            logger.error(f"[ERROR] Property report not found: {property_file_path}")
            return -1

        try:
            with open(property_file_path, "r", encoding="utf-8") as file:
                property_data = json.load(file)
        except Exception as exc:
            logger.error(f"[ERROR] Failed to read property file: {exc}")
            return -1

        properties = property_data.get("viewer-property", {}).get("properties", {})
        return len(properties)

    def _is_error_covered(self, error: CBMCError) -> bool:
        coverage_status = self._get_function_coverage_status(error.file, error.func)
        if not coverage_status:
            logger.error("[ERROR] Function coverage status not found.")
            return False

        result = coverage_status.get(error.line) != "missed"
        if result:
            logger.info("Error '%s' line %s covered", error.error_id, error.line)
        else:
            logger.info("Error '%s' line %s not covered", error.error_id, error.line)
        return result

    def _is_error_solved(self, error: CBMCError) -> bool:
        try:
            current_errors = get_json_errors(self.harness_dir)
        except Exception as exc:
            logger.error(f"[ERROR] Failed to read current JSON errors: {exc}")
            return False

        result = error.error_id not in current_errors
        if result:
            logger.info("Error '%s' solved", error.error_id)
        else:
            logger.info("Error '%s' not solved", error.error_id)
        return result

    def get_validator_tools(self):
        proof_validator_tool = {
            "type": "function",
            "name": "proof_validator",
            "description": (
                "Update the proof harness with the provided content, then run compilation "
                "or full verification to validate the candidate harness. Makefile updates "
                "are not allowed in this validator."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason for running this validation",
                    },
                    "harness_content": {
                        "type": "string",
                        "description": "The complete updated harness file content",
                    },
                    "makefile_content": {
                        "type": ["string", "null"],
                        "description": "Must be null for this validator",
                    },
                    "compile_only": {
                        "type": "boolean",
                        "description": "If true, only compile the candidate harness.",
                    },
                },
                "required": [
                    "reason",
                    "harness_content",
                    "makefile_content",
                    "compile_only",
                ],
                "additionalProperties": False,
            },
        }
        return [*self.get_tools(), proof_validator_tool]

    def handle_proof_validator(
        self,
        harness_content: str,
        makefile_content: Optional[str],
        compile_only: bool,
    ) -> dict:
        result = {
            "compilation": {
                "success": False,
                "exit_code": -1,
                "stderr": "",
            },
            "timed_out": False,
            "error_covered": None,
            "coverage_maintained": None,
            "properties_maintained": None,
            "error_resolved": None,
            "message": "",
        }

        if makefile_content is not None:
            result["compilation"]["stderr"] = (
                "Makefile updates are not allowed in the precondition validator."
            )
            result["message"] = result["compilation"]["stderr"]
            return result

        backup_tag = uuid.uuid4().hex[:8].upper()
        self.create_backup(backup_tag)
        try:
            self.update_harness(harness_content)
            logger.info("Harness updated via proof_validator tool.")

            make_results = self.run_make(compile_only=compile_only)
            status_code = make_results.get("status", Status.ERROR)
            exit_code = make_results.get("exit_code", -1)
            timed_out = status_code == Status.TIMEOUT

            result["compilation"] = {
                "success": exit_code == 0 and status_code == Status.SUCCESS,
                "exit_code": exit_code,
                "stderr": make_results.get("stderr", ""),
            }
            if not result["compilation"]["success"]:
                result["compilation"]["stdout"] = make_results.get("stdout", "")
            result["timed_out"] = timed_out

            if compile_only:
                self.restore_backup(backup_tag)
                result["message"] = (
                    "Compilation succeeded (compile-only mode)."
                    if result["compilation"]["success"]
                    else "Compilation failed. Review the errors above."
                )
                return result

            if timed_out:
                self.restore_backup(backup_tag)
                result["message"] = (
                    "Verification timed out. Refine the harness without changing the Makefile."
                )
                return result

            if not result["compilation"]["success"]:
                self.restore_backup(backup_tag)
                result["message"] = (
                    "Compilation or verification failed. Review the build logs and fix the harness."
                )
                return result

            messages = []
            error = self._current_error

            if error and self._error_covered_initially:
                error_covered = self._is_error_covered(error)
                result["error_covered"] = error_covered
                if not error_covered:
                    messages.append(
                        "ERROR: The harness no longer reaches the line where the error occurred."
                    )

            if self._current_coverage:
                new_coverage = self.get_overall_coverage()
                cov_hit_ok = new_coverage.get("hit", 0.0) >= self._current_coverage.get(
                    "hit", 0.0
                )
                cov_pct_ok = new_coverage.get(
                    "percentage", 0.0
                ) >= self._current_coverage.get("percentage", 0.0)
                result["coverage_maintained"] = cov_hit_ok and cov_pct_ok
                if not result["coverage_maintained"]:
                    messages.append(
                        "ERROR: Overall coverage decreased after applying the harness update."
                    )

            if self._initial_property_count >= 0:
                new_property_count = self.get_property_count()
                result["properties_maintained"] = (
                    new_property_count >= 0
                    and new_property_count >= self._initial_property_count
                )
                if not result["properties_maintained"]:
                    messages.append(
                        "ERROR: Property count decreased after applying the harness update."
                    )

            if error:
                error_resolved = self._is_error_solved(error)
                result["error_resolved"] = error_resolved
                if not error_resolved:
                    messages.append("ERROR: The error is no longer resolved.")

            if messages:
                self.restore_backup(backup_tag)
                result["message"] = " | ".join(messages)
                return result

            self._validated_harness_baseline = harness_content
            result["message"] = (
                "All checks passed. The harness update is valid and the error remains resolved."
            )
            return result
        finally:
            self.discard_backup(backup_tag)

    def handle_tool_calls(self, tool_name, function_args):
        logging_text = f"""
        Function call:
        Name: {tool_name}
        Args: {function_args}
        """
        logger.info(logging_text)

        function_args_parsed = json.loads(function_args)
        if tool_name == "proof_validator":
            harness_content = function_args_parsed.get("harness_content", "")
            makefile_content = function_args_parsed.get("makefile_content", None)
            compile_only = function_args_parsed.get("compile_only", False)
            tool_response = self.handle_proof_validator(
                harness_content, makefile_content, compile_only
            )
            logger.info(f"Function call response:\n {tool_response}")
            return str(tool_response)

        return super().handle_tool_calls(tool_name, function_args)

    def prepare_prompt(self, error: CBMCError, diff_output: str, analysis: str):
        with open("prompts/precondition_validator_system.prompt", "r", encoding="utf-8") as file:
            system_prompt = file.read()

        with open("prompts/precondition_validator_user.prompt", "r", encoding="utf-8") as file:
            user_prompt = file.read()

        user_prompt = user_prompt.replace("{TARGET_FUNCTION}", self.target_function)
        user_prompt = user_prompt.replace("{ORIGINAL_HARNESS}", self.get_harness())
        user_prompt = user_prompt.replace("{ERROR_SUMMARY}", error.msg)
        user_prompt = user_prompt.replace(
            "{ERROR_FILE}", error.file if error.file else "Unknown"
        )
        user_prompt = user_prompt.replace("{ERROR FUNCTION}", error.func)
        user_prompt = user_prompt.replace("{ERROR_LINE}", str(error.line))
        user_prompt = user_prompt.replace("{ERROR_ANALYSIS}", analysis)
        user_prompt = user_prompt.replace("{HARNESS_DIFF}", diff_output)

        return system_prompt, user_prompt

    def save_validation_result(
        self,
        error: CBMCError,
        validation_result: PreconditionValidatorResponse,
    ):
        error_details = {
            "error_id": error.error_id,
            "error_summary": error.msg,
            "error_file": error.file,
            "error_function": error.func,
            "error_line": error.line,
        }

        output = {
            "error_details": error_details,
            **validation_result.to_dict(include_updated_harness=False),
        }

        validation_result_path = os.path.join(self.harness_dir, "validation_result.json")
        with open(validation_result_path, "a", encoding="utf-8") as file:
            file.write(json.dumps(output, indent=2))
            file.write("\n")

    def _build_retry_prompt(self, failure_reason: str) -> str:
        return (
            "The previous response was rejected.\n"
            f"Reason: {failure_reason}\n"
            "Re-analyze the preconditions, use proof_validator when needed, and return "
            "a response that satisfies the schema and workflow requirements."
        )

    def _proof_validation_succeeded(self, validation_result: dict) -> bool:
        if not validation_result.get("compilation", {}).get("success", False):
            return False
        if validation_result.get("timed_out", False):
            return False

        for key in (
            "error_covered",
            "coverage_maintained",
            "properties_maintained",
            "error_resolved",
        ):
            if validation_result.get(key) is False:
                return False
        return True

    def _validate_response_consistency(
        self,
        response: PreconditionValidatorResponse,
        require_applied_harness: bool = False,
    ) -> Optional[str]:
        if not response.validation_result:
            return "The response must include at least one validation_result entry."

        requires_updated_harness = any(
            result.violation_type in (ViolationType.IRRELEVANT, ViolationType.INCOMPLETE)
            for result in response.validation_result
        )
        if requires_updated_harness and not response.updated_harness:
            return (
                "Responses containing 'irrelevant' or 'incomplete' violations must "
                "include updated_harness."
            )

        if (
            require_applied_harness
            and response.updated_harness
            and response.updated_harness != self.get_harness()
        ):
            return (
                "updated_harness must match the final validated harness content kept on disk."
            )

        return None

    def _summarize_validation(
        self, validation_result: PreconditionValidatorResponse
    ) -> dict:
        total_preconditions = len(
            [result for result in validation_result.validation_result if result.precondition]
        )
        not_violated = len(
            [result for result in validation_result.validation_result if not result.violated]
        )
        violated_total = len(
            [result for result in validation_result.validation_result if result.violated]
        )
        violation_counts = {violation.value: 0 for violation in ViolationType}
        for result in validation_result.validation_result:
            if result.violation_type:
                violation_counts[result.violation_type.value] += 1

        return {
            "total_preconditions": total_preconditions,
            "not_violated": not_violated,
            "violated_total": violated_total,
            "violation_counts": violation_counts,
        }

    def _apply_summary_counts(self, summary: dict):
        self.preconditions_analyzed += summary["total_preconditions"]
        self.not_violated += summary["not_violated"]
        self.violated_total += summary["violated_total"]
        for violation_type, count in summary["violation_counts"].items():
            self.violation_counts[violation_type] += count
        self.num_tasks += 1

    def validate(self, error: CBMCError, diff_output: str, analysis: str) -> Status:
        task_id = f"validate_{error.error_id}"
        system_prompt, base_user_prompt = self.prepare_prompt(error, diff_output, analysis)
        conversation = []
        failure_reason = None
        original_harness = self.get_harness()

        self.last_validation_response = None
        self._current_error = error
        self._current_coverage = self.get_overall_coverage()
        self._initial_property_count = self.get_property_count()
        self._error_covered_initially = self._is_error_covered(error)
        self._validated_harness_baseline = original_harness

        for attempt in range(1, self.max_attempts + 1):
            if self.get_harness() != self._validated_harness_baseline:
                self.update_harness(self._validated_harness_baseline)

            user_prompt = (
                base_user_prompt
                if attempt == 1
                else self._build_retry_prompt(failure_reason or "Unknown failure.")
            )

            llm_response, chat_data = self.llm.chat_llm(
                system_prompt,
                user_prompt,
                PreconditionValidatorResponse,
                llm_tools=self.get_validator_tools(),
                call_function=self.handle_tool_calls,
                conversation_history=conversation,
            )

            if not llm_response:
                failure_reason = "The response could not be parsed into the expected schema."
                self.log_task_attempt(task_id, attempt, chat_data, "no_llm_response")
                continue

            response_error = self._validate_response_consistency(llm_response)
            if response_error:
                failure_reason = response_error
                self.log_task_attempt(task_id, attempt, chat_data, "invalid_response")
                continue

            if llm_response.updated_harness:
                proof_result = self.handle_proof_validator(
                    llm_response.updated_harness,
                    None,
                    False,
                )
                if not self._proof_validation_succeeded(proof_result):
                    failure_reason = (
                        "The updated harness in the final response failed validation: "
                        f"{proof_result.get('message', 'unknown proof validation error')}"
                    )
                    self.log_task_attempt(
                        task_id,
                        attempt,
                        chat_data,
                        "updated_harness_validation_failed",
                    )
                    continue

                response_error = self._validate_response_consistency(
                    llm_response,
                    require_applied_harness=True,
                )
                if response_error:
                    failure_reason = response_error
                    self.log_task_attempt(task_id, attempt, chat_data, "invalid_response")
                    continue

            self.last_validation_response = llm_response
            self.save_validation_result(error, llm_response)

            summary = self._summarize_validation(llm_response)
            task_result = {
                **summary,
                "error_id": error.error_id,
                "error_summary": error.msg,
                "error_location": {
                    "file": error.file,
                    "function": error.func,
                    "line": error.line,
                },
            }

            logger.info(f"Precondition Validator Result: {task_result}")
            self.log_task_attempt(task_id, attempt, chat_data, error=None)
            self.log_task_result(task_id, True, attempt, task_result)
            self._apply_summary_counts(summary)

            # Collect exploitable preconditions for the vulnerability report
            exploitable = [
                {
                    "precondition": result.precondition,
                    "reasoning": result.reasoning,
                }
                for result in llm_response.validation_result
                if result.violated
                and result.violation_type == ViolationType.EXPLOITABLE
            ]
            if exploitable:
                self._validation_records.append(
                    {
                        "error_id": error.error_id,
                        "error_type": error.msg,
                        "code_location": {
                            "file": error.file,
                            "function": error.func,
                            "line": error.line,
                        },
                        "violated_preconditions": exploitable,
                    }
                )

            if (
                summary["violation_counts"].get(ViolationType.EXPLOITABLE.value, 0) > 0
            ):
                return Status.FAILURE
            return Status.SUCCESS

        self.update_harness(original_harness)
        self._validated_harness_baseline = original_harness
        logger.error("[ERROR] Precondition validation failed after %s attempts.", self.max_attempts)
        self.log_task_result(
            task_id,
            False,
            self.max_attempts,
            {"reason": failure_reason or "validation_failed"},
        )
        return Status.ERROR

    def _generate_vulnerability_report(self):
        report = {
            "summary": {
                "total_errors_analyzed": self.num_tasks,
                "total_vulnerabilities": len(self._validation_records),
            },
            "vulnerabilities": self._validation_records,
        }

        report_path = os.path.join(self.harness_dir, "vulnerability-report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        logger.info("Vulnerability report written to %s", report_path)

    def complete_validation(self):
        agent_result = {
            "validation_tasks": self.num_tasks,
            "preconditions_analyzed": self.preconditions_analyzed,
            "not_violated": self.not_violated,
            "violated_total": self.violated_total,
            "violation_counts": self.violation_counts,
        }

        self.log_agent_result(agent_result)
        self._generate_vulnerability_report()

    def generate(self) -> bool:
        return True
