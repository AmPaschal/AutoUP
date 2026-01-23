import logging
import os
import json
from agent import AIAgent
from pathlib import Path
from commons.models import Generable
from makefile.output_models import ValidationAssessmentResponse

from commons.utils import Status

logger = logging.getLogger(__name__)


class ViolationReviewer(AIAgent, Generable):
    def __init__(self, args, project_container):
        super().__init__(
            "ViolationReviewer",
            args,
            project_container,
        )

        self.violations_reviewed = 0
        self.violated_buggy_agree = 0
        self.violated_buggy_disagree = 0
        self.threat_scores = {i: 0 for i in range(1, 11)}
        self.violation_assessments = []

    def _extract_violations(self):
        vb_conditions = []

        # I'm misusing harness_dir here
        for dirpath, dirnames, filenames in os.walk(self.harness_dir):
            if "validation_result.json" in filenames:
                path = os.path.join(dirpath, "validation_result.json")
                try:
                    with open(path, "r") as f:
                        content = f.read()
                        try:
                            data = json.loads(content)
                            if isinstance(data, dict):
                                data = [data]
                        except json.JSONDecodeError:
                            # Handle concatenated JSON objects
                            content = content.strip()
                            if content.count("}{") > 0:
                                content = content.replace("}{", "},{")
                            if content.count("}\n{") > 0:
                                content = content.replace("}\n{", "},{")
                            if not content.startswith("["):
                                content = "[" + content + "]"
                            data = json.loads(content)

                        if isinstance(data, list):
                            for entry in data:
                                error_details = entry.get("error_details", {})
                                results = entry.get("validation_result", [])
                                rel_path = os.path.relpath(dirpath, self.root_dir)

                                for item in results:
                                    if item.get("verdict") == "VIOLATED_BUGGY":
                                        precond = item.get("precondition", "").strip()
                                        input_source = item.get("untrusted_input_source", "").strip()
                                        reasoning = item.get("reasoning", "").strip()
                                        analysis = item.get("detailed_analysis", "").strip()

                                        vb_conditions.append({
                                            "precondition": precond,
                                            "harness_location": rel_path,
                                            "error_func": error_details["error_function"],
                                            "source_file": str(Path(self.root_dir) / error_details["error_file"]),
                                            "input_source": input_source,
                                            "reasoning": reasoning,
                                            "analysis": analysis,
                                        })
                except Exception as e:
                    print(f"Error reading {path}: {e}")

        return vb_conditions

    def get_top_threats(self, top_n=-1):
        """
        Returns our violation assessments, ordered from highest to lowest threat
        """
        sorted_violations = sorted(
            self.violation_assessments,
            key=lambda violation: violation["LLM Review"]["Threat Score"],
        )
        return sorted_violations if top_n == -1 else sorted_violations[:top_n]

    def prepare_prompt(self, validation_results):
        with open("prompts/violation_reviewer_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/violation_reviewer_user.prompt", "r") as f:
            user_prompt = f.read()

        user_prompt = user_prompt.replace(
            "{PRECONDITION}", validation_results["precondition"]
        )
        user_prompt = user_prompt.replace("{TARGET}", validation_results["error_func"])
        user_prompt = user_prompt.replace("{SOURCE}", validation_results["source_file"])
        user_prompt = user_prompt.replace(
            "{INPUT_SOURCE}", validation_results["input_source"]
        )
        user_prompt = user_prompt.replace(
            "{REASONING}", validation_results["reasoning"]
        )
        user_prompt = user_prompt.replace("{ANALYSIS}", validation_results["analysis"])

        return system_prompt, user_prompt

    def review_violation(self, error_id, validation_result):
        conversation = []

        system_prompt, user_prompt = self.prepare_prompt(validation_result)

        llm_response, chat_data = self.llm.chat_llm(
            system_prompt,
            user_prompt,
            ValidationAssessmentResponse,
            llm_tools=self.get_tools(),
            call_function=self.handle_tool_calls,
            conversation_history=conversation,
        )

        task_id = f"violation_review_{error_id}"
        error_tag = None

        if not llm_response:
            logger.error("[ERROR] No valid response from LLM")
            error_tag = "no_llm_response"
            self.log_task_attempt(task_id, 1, chat_data, error_tag)
            self.log_task_result(task_id, False, 1)
            return Status.ERROR

        result = llm_response.to_dict()

        self.violations_reviewed += 1
        if result["violation_is_correct"]:
            self.violated_buggy_agree += 1
            self.threat_scores[result["threat_score"]] += 1
        else:
            self.violated_buggy_disagree += 1

        nice_formatted_assessment = {
            "Precondition": validation_result["precondition"],
            "Target Function": validation_result["error_func"],
            "Source File": validation_result["source_file"],
            "Violation Assessment": {
                "Untrusted Input Source": validation_result["input_source"],
                "Reasoning": validation_result["reasoning"],
                "Analysis": validation_result["analysis"],
                "Reviewer Agrees": result["violation_is_correct"],
                "Reviewer Rationle": result["validation_review"],
            },
            "LLM Review": {
                "Call Trace": result["call_trace"],
                "Origin of Variable": result["var_origin"],
                "Threat Assessment": {
                    "Vulnerability Context": result["vuln_context"],
                    "Vulnerability Impact": result["vuln_impact"],
                    "Ease of Exploitation": result["ease_of_exploitation"],
                },
                "Threat Score": result["threat_score"],
            },
        }

        self.violation_assessments.append(nice_formatted_assessment)

    def dump_violation_assessments(self):

        output_format = {
            "Correct Violations": self.violated_buggy_agree,
            "Incorrect Violations": self.violated_buggy_disagree,
            "Threat Scores": self.threat_scores,
            "Sorted Assessments": self.get_top_threats(),
        }

        with open("./violation_assessments.json", "w") as f:
            json.dump(output_format, f, indent=4)

    def generate(self) -> bool:
        # This method is kept for compatibility but validate should be used instead
        violated_buggy_conditions = self._extract_violations()

        for i, violation in enumerate(violated_buggy_conditions):
            self.review_violation(i, violation)

        self.dump_violation_assessments()

        return True
