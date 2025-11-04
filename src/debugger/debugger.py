""" Debugger class"""

# System
from pathlib import Path
import subprocess
import os

# Utils
from datetime import datetime
import json

# AutoUp
from agent import AIAgent
from commons.models import GPT, Generable
from debugger.output_models import ModelOutput
from logger import setup_logger
from commons.utils import Status


# OLD
from debugger.error_report import ErrorReport, CBMCError
from debugger.parser import extract_errors_and_payload, get_json_errors
from debugger.advice import get_advice_for_cluster

logger = setup_logger(__name__)


class ProofDebugger(AIAgent, Generable):
    """Agentic Proof Debugger"""

    def __init__(self, harness_path, root_dir, target_function_name, target_file_path, project_container, metrics_file):
        super().__init__(
            agent_name="debugger",
            project_container=project_container,
            metrics_file=metrics_file
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        self.harness_path = harness_path
        self.root_dir = root_dir
        self.target_func = f"{target_function_name}_harness.c"
        self.harness_file_path = os.path.join(harness_path, self.target_func)
        self.target_file_path = target_file_path
        logger.info("self.harness_path %s", self.harness_path)
        logger.info("self.root_dir %s", self.root_dir)
        logger.info("self.target_func %s", self.target_func)
        logger.info("self.target_file_path %s", self.target_file_path)
        self.__max_attempts = 3

    def generate(self) -> bool:
        """Iterates over errors"""
        make_success = self.__execute_make()
        if not make_success:
            logger.error("Initial proof does not build successfully.")
            return False
        self.__create_backup()
        error = self.__pop_error()
        while error is not None:
            logger.info("Target Error: %s", error)
            result = self.generate_single_fix(error)
            if not result:
                #TODO: ADD SKIP ERRORS
                continue
            error = self.__pop_error()
        return True

    def generate_single_fix(self, error: CBMCError) -> bool:
        """Generate the fix of a given error"""
        cause_of_failure = None
        for attempt in range(1, self.__max_attempts + 1):
            logger.info("Attempt: %i", attempt)
            logger.info("Cluster: %s", error.cluster)
            logger.info("Error id: %s", error.error_id)
            history = self.__refine_harness_file(error, cause_of_failure)
            make_success = self.__execute_make()
            if not make_success:
                self.log_task_attempt(error.error_id, attempt, history, error="make_failed")
                cause_of_failure = {"reason": "make_failed"}
                continue
            error_report = ErrorReport(
                extract_errors_and_payload(self.target_func, self.harness_file_path),
                get_json_errors(self.harness_file_path)
            )
            if not self.__is_error_covered(error, error_report):
                self.log_task_attempt(error.error_id, attempt, history, error="error_not_covered")
                cause_of_failure = {"reason": "error_not_covered"}
                continue
            if not self.__is_error_solved(error, error_report):
                self.log_task_attempt(error.error_id, attempt, history, error="error_not_fixed")
                cause_of_failure = {"reason": "error_not_fixed"}
                continue
            if self.__is_error_covered(error, error_report) and self.__is_error_solved(error, error_report):
                logger.info("Error resolved!")
                self.log_task_attempt(error.error_id, attempt, history, error=None)
                self.log_task_result(error.error_id, True, attempt)
                return True
        self.log_task_result(error.error_id, False, self.__max_attempts)
        logger.info("Error not resolved...")
        return False

    def __refine_harness_file(self, error, cause_of_failure):
        system_prompt = self.__get_prompt("general_system")
        user_prompt = self.__compute_user_prompt(error, cause_of_failure)
        logger.info("System prompt: %s", system_prompt)
        logger.info("User prompt: %s", user_prompt)
        output, history = self.llm.chat_llm(
            system_messages=system_prompt,
            input_messages=user_prompt,
            output_format=ModelOutput,
            llm_tools=self.get_tools(),
            call_function=self.handle_tool_calls,
        )
        logger.info("LLM response: \n%s", output.updated_harness_file_content)
        self.__update_harness(output.updated_harness_file_content)
        return history

    def __update_harness(self, harness_content: str):
        with open(self.harness_file_path, "w+", encoding="utf-8") as f:
            f.write(harness_content)

    def __is_error_covered(self, error, error_report) -> bool:
        result = error.error_id in (error_report.json_true_errors | error_report.json_false_errors)
        if result:
            logger.info("Error '%s' covered", error.error_id)
        else:
            logger.info("Error '%s' not covered", error.error_id)
        return result
 
    def __is_error_solved(self, error, error_report) -> bool:
        result = error.error_id in error_report.json_true_errors
        if result:
            logger.info("Error '%s' solved", error.error_id)
        else:
            logger.info("Error '%s' not solved", error.error_id)
        return result

    def __compute_user_prompt(self, error: CBMCError, cause_of_failure):
        if cause_of_failure is None:
            logger.info("cause_of_failure is None")
            advice = self.__get_advice(error.cluster)
            user_prompt = self.__get_prompt("no_previous_user")
            user_prompt = user_prompt.replace("{message}", error.msg)
            user_prompt = user_prompt.replace("{target_file_path}", self.target_file_path)
            user_prompt = user_prompt.replace("{harness_file_path}", self.harness_file_path) 
            user_prompt = user_prompt.replace("{stack}", '\n'.join(
                [f'in {func}, Line: {line}' for func, line in error.stack]))
            user_prompt = user_prompt.replace(
                "{variables}", json.dumps(error.vars, indent=4))
            user_prompt = user_prompt.replace("{advice}", '\n'.join(
                [f'{i + 1}. {step}' for i, step in enumerate(advice)]))
            user_prompt = user_prompt.replace("{harness}", self.target_func)
            return user_prompt
        if cause_of_failure["reason"] == "make_failed":
            logger.info("Reason: make_failed")
            user_prompt = self.__get_prompt("make_failed_user")
            return user_prompt
        if cause_of_failure["reason"] == "error_not_covered":
            logger.info("Reason: error_not_covered")
            user_prompt = self.__get_prompt("error_not_covered_user")
            return user_prompt
        if cause_of_failure["reason"] == "error_not_fixed":
            logger.info("Reason: error_not_fixed")
            user_prompt = self.__get_prompt("error_not_fixed_user")
            user_prompt = user_prompt.replace(
                "{errors}", json.dumps(error.vars, indent=4),
            )
            return user_prompt
        raise ValueError(
            f"Unknown cause_of_failure reason: {cause_of_failure['reason']}",
        )

    def __execute_make(self) -> bool:
        logger.info("Executing 'make' into '%s'", self.harness_path)
        result = self.execute_command("make -j4", workdir=self.harness_path, timeout=600)
        return result["status"] == Status.SUCCESS

    def __create_backup(self):
        backup_path = os.path.join(
            self.harness_path, f"{self.target_func}.backup",
        )
        with open(self.harness_file_path, "r", encoding="utf-8") as src:
            with open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        logger.info("Backup created sucessfully.")

    def __pop_error(self) -> CBMCError | None:  # TODO: Refactor Error Handling
        error_report = ErrorReport(
            extract_errors_and_payload(self.target_func, self.harness_file_path),
            get_json_errors(self.harness_file_path)
        )
        logger.info("Unresolved Errors: %i", len(error_report.unresolved_errs))
        error = error_report.get_next_error()
        if error[2] is None:
            return None
        error[2].cluster = "" if error[0] is None else error[0]
        error[2].error_id = "" if error[1] is None else error[1]
        return error[2]

    def __get_prompt(self, prompt_name: str) -> str:
        with open(f"prompts/debugger/{prompt_name}.prompt", encoding="utf-8") as f:
            return "".join(line for line in f if not line.lstrip().startswith("#"))

    def __get_advice(self, cluster: str):
        return get_advice_for_cluster(cluster, self.target_func)
