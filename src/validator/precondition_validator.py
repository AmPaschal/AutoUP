import logging
import os
import re
import json
from agent import AIAgent
from commons.models import Generable
from makefile.output_models import PreconditionValidatorResponse, ValidationResult, Verdict

logger = logging.getLogger(__name__)

class PreconditionValidator(AIAgent, Generable):
    def __init__(self, args, project_container):
        super().__init__(
            "PreconditionValidator",
            args,
            project_container,
        )

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

    def prepare_prompt(self):
        with open("prompts/precondition_validator_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/precondition_validator_user.prompt", "r") as f:
            user_prompt = f.read()

        user_prompt = user_prompt.replace("{FUNCTION_NAME}", self.target_function)
        user_prompt = user_prompt.replace("{FILE_LOCATION}", self.target_file_path)
        user_prompt = user_prompt.replace("{HARNESS_CODE}", self.get_harness())

        return system_prompt, user_prompt

    def save_validation_result(self, validation_result: PreconditionValidatorResponse):
        validation_result_path = os.path.join(self.harness_dir, "validation_result.json")
        with open(validation_result_path, "w") as f:
            f.write(validation_result.model_dump_json(indent=2))

    def generate(self) -> bool:
        conversation = []
 
        system_prompt, user_prompt = self.prepare_prompt()
        
        llm_response, chat_data = self.llm.chat_llm(
            system_prompt, 
            user_prompt, 
            PreconditionValidatorResponse,
            llm_tools=self.get_tools(),
            call_function=self.handle_tool_calls,
            conversation_history=conversation
        )

        task_id = "PreconditionValidator"
        error_tag = None
        agent_result = {}

        if not llm_response:
            logger.error("[ERROR] No valid response from LLM")
            error_tag = "no_llm_response"
            
        elif not llm_response.validation_result:
            logger.error("[ERROR] No valid verdicts from LLM")
            error_tag = "no_llm_verdicts"
        else:
            # Save validation result
            self.save_validation_result(llm_response)
            valid_preconditions = len([v for v in llm_response.validation_result if v.precondition and v.verdict == Verdict.SATISFIED])
            total_preconditions = len([v for v in llm_response.validation_result if v.precondition])
            valid_function_models = len([v for v in llm_response.validation_result if v.function_model and v.verdict == Verdict.SATISFIED])
            total_function_models = len([v for v in llm_response.validation_result if v.function_model])

            agent_result = {
                "preconditions_validated": f"{valid_preconditions}/{total_preconditions}",
                "function_models_validated": f"{valid_function_models}/{total_function_models}"
            }
            logger.info(f"Precondition Validator Result: {agent_result}")

        self.log_task_attempt(task_id, 1, chat_data, error_tag)
        self.log_task_result(task_id, error_tag == None, 1)
        self.log_agent_result(agent_result)

        return error_tag == None
