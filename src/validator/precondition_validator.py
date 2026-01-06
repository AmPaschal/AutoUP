import logging
import os
import re
import json
from agent import AIAgent
from commons.models import Generable
from makefile.output_models import PreconditionValidatorResponse, ValidationResult, Verdict

from commons.utils import Status
from debugger.error_report import CBMCError

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
        self.valid = 0
        self.violated_buggy = 0
        self.violated_not_buggy = 0

    def extract_preconditions(self, harness_path):
        """
        Extracts __CPROVER_assume statements from the harness file.
        Returns a list of precondition strings.
        """
        if not os.path.exists(harness_path):
            logger.error(f"[ERROR] Harness file not found: {harness_path}")
            return []

        with open(harness_path, "r") as f:
            content = f.read()

        # Regex to find __CPROVER_assume(...)
        # This is a simple regex and might not handle nested parentheses correctly for complex expressions
        # But for a first pass it should be sufficient for standard assumes
        preconditions = re.findall(r'__CPROVER_assume\((.*?)\);', content, re.DOTALL)
        return [p.strip() for p in preconditions]

    def prepare_prompt(self, error: CBMCError, diff_output: str, analysis: str):
        with open("prompts/precondition_validator_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/precondition_validator_user.prompt", "r") as f:
            user_prompt = f.read()

        user_prompt = user_prompt.replace("{ORIGINAL_HARNESS}", self.get_harness())
        user_prompt = user_prompt.replace("{ERROR_SUMMARY}", error.msg)
        user_prompt = user_prompt.replace("{ERROR_FILE}", error.file if error.file else "Unknown")
        user_prompt = user_prompt.replace("{ERROR FUNCTION}", error.func)
        user_prompt = user_prompt.replace("{ERROR_LINE}", str(error.line))
        user_prompt = user_prompt.replace("{ERROR_ANALYSIS}", analysis)
        user_prompt = user_prompt.replace("{HARNESS_DIFF}", diff_output)

        return system_prompt, user_prompt

    def save_validation_result(self, error: CBMCError, validation_result: PreconditionValidatorResponse):
        error_details = {
            "error_id": error.error_id,
            "error_summary": error.msg,
            "error_file": error.file,
            "error_function": error.func,
            "error_line": error.line,
        }

        # Dump validation result to dict
        result_dict = validation_result.to_dict()

        # Build a new dictionary with error_details at the top
        output = {
            "error_details": error_details,
            **result_dict    
        }

        validation_result_path = os.path.join(self.harness_dir, "validation_result.json")
        with open(validation_result_path, "a") as f:
            f.write(json.dumps(output, indent=2))
            f.write("\n")

    def validate(self, error: CBMCError, diff_output: str, analysis: str) -> Status:
        conversation = []
 
        system_prompt, user_prompt = self.prepare_prompt(error, diff_output, analysis)
        
        llm_response, chat_data = self.llm.chat_llm(
            system_prompt, 
            user_prompt, 
            PreconditionValidatorResponse,
            llm_tools=self.get_tools(),
            call_function=self.handle_tool_calls,
            conversation_history=conversation
        )

        task_id = f"validate_{error.error_id}"
        error_tag = None
        agent_result = {}

        if not llm_response:
            logger.error("[ERROR] No valid response from LLM")
            error_tag = "no_llm_response"
            self.log_task_attempt(task_id, 1, chat_data, error_tag)
            self.log_task_result(task_id, False, 1)
            return Status.ERROR
            
        elif not llm_response.validation_result:
            logger.error("[ERROR] No valid verdicts from LLM")
            error_tag = "no_llm_verdicts"
            self.log_task_attempt(task_id, 1, chat_data, error_tag)
            self.log_task_result(task_id, False, 1)
            return Status.ERROR
        else:
            # Save validation result
            self.save_validation_result(error, llm_response)
            valid = len([v for v in llm_response.validation_result if v.precondition and v.verdict == Verdict.VALID])
            violated_buggy = len([v for v in llm_response.validation_result if v.precondition and v.verdict == Verdict.VIOLATED_BUGGY])
            violated_non_buggy = len([v for v in llm_response.validation_result if v.precondition and v.verdict == Verdict.VIOLATED_NOT_BUGGY])
            total_preconditions = len([v for v in llm_response.validation_result if v.precondition])
            
            task_result = {
                "total_preconditions": total_preconditions,
                "valid_preconditions": valid,
                "violated_buggy": violated_buggy,
                "violated_not_buggy": violated_non_buggy,
                "error_id": error.error_id,
                "error_summary": error.msg,
                "error_location": {
                    "file": error.file,
                    "function": error.func,
                    "line": error.line,
                }
            }
            logger.info(f"Precondition Validator Result: {agent_result}")

            self.log_task_attempt(task_id, 1, chat_data, error_tag)
            self.log_task_result(task_id, True, 1, task_result)

            self.preconditions_analyzed += total_preconditions
            self.valid += valid
            self.violated_buggy += violated_buggy
            self.violated_not_buggy += violated_non_buggy
            self.num_tasks += 1

            # Check if all verdicts are VALID
            all_satisfied = all(v.verdict == Verdict.VALID for v in llm_response.validation_result)
            if all_satisfied:
                return Status.SUCCESS
            else:
                return Status.FAILURE
            
    def complete_validation(self):

        agent_result = {
            "validation_tasks": self.num_tasks,
            "preconditions_analyzed": self.preconditions_analyzed,
            "valid_preconditions": self.valid,
            "violated_buggy": self.violated_buggy,
            "violated_not_buggy": self.violated_not_buggy
        }

        self.log_agent_result(agent_result)

    def generate(self) -> bool:
        # This method is kept for compatibility but validate should be used instead
        return True
