""" Debugger class"""

# System
from pathlib import Path
import logging
import subprocess
import re

# Utils
import json

# AutoUp
from src.debugger.new.agent import Agent

# OLD
from src.debugger.error_report import ErrorReport, CBMCError
from src.debugger.error_report import ErrorReport
from src.debugger.parser import extract_errors_and_payload
from src.debugger.advice import get_advice_for_cluster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ProofDebugger():
    """Agentic Proof Debugger"""

    def __init__(self, harness_dir, target_func, target_file_path):
        self.llm = Agent()
        self.harness_dir = str(Path(harness_dir).resolve())
        self.target_func = target_func
        self.target_file_path = str(Path(target_file_path).resolve())
        self.__max_attempts = 3

    def generate(self) -> bool:
        """Iterates over errors"""
        make_success = self.__execute_make()
        if not make_success:
            logger.error("Initial proof does not build successfully.")
            return False
        error = self.__pop_error()
        while error is not None:
            logger.info("Target Error: %s", error)
            result = self.generate_single_fix(error)
            if not result:
                return False
            error = self.__pop_error()
        return True

    def generate_single_fix(self, error: CBMCError) -> bool:
        """Generate the fix of a given error"""
        for attempt in range(1, self.__max_attempts + 1):
            logger.info("Attempt: %i", attempt)
            logger.info("Cluster: %s", error.cluster)
            logger.info("Error id: %s", error.error_id)
            self.__refine_harness_file(error)
            self.__execute_make()
            if self.__is_error_solved(error):
                return True
        return False

    def __refine_harness_file(self, error) -> str:
        self.llm.create_chat()
        return ""

    def __update_harness(self, preconditions):
        pass



    def __is_error_solved(self, error) -> bool:
        new_error = self.__pop_error()
        return new_error.cluster != error.cluster
    
    def __compute_user_prompt(self, error: CBMCError, cause_of_failure, previous_response):
        if previous_response:
            advice = self.__get_advice(error.cluster)
            user_prompt = self.__get_prompt("no_previous_user")
            user_prompt = user_prompt.replace("{message}", error.msg)
            user_prompt = user_prompt.replace("{stack}", '\n'.join(
                [f'in {func}, Line: {line}' for func, line in error.stack]))
            user_prompt = user_prompt.replace(
                "{variables}", json.dumps(error.vars, indent=4))
            user_prompt = user_prompt.replace("{advice}", '\n'.join(
                [f'{i + 1}. {step}' for i, step in enumerate(advice)]))
            return user_prompt
        if cause_of_failure is None:
            user_prompt = self.__get_prompt("general_error_user")
            user_prompt = user_prompt.replace(
                "{errors}", json.dumps(error.vars, indent=4))
            return user_prompt
        if cause_of_failure["reason"] == "harness_update_error":
            user_prompt = self.__get_prompt("harness_update_error_user")
            user_prompt = user_prompt.replace(
                "{function}", cause_of_failure["error"].func)
            user_prompt = user_prompt.replace(
                "{previous_line}", cause_of_failure["error"].prev_line)
            return user_prompt
        if cause_of_failure["reason"] == "syntax_error":
            user_prompt = self.__get_prompt("syntax_error_user")
            return user_prompt
        if cause_of_failure["reason"] == "coverage_error":
            user_prompt = self.__get_prompt("coverage_error_user")
            return user_prompt
        if cause_of_failure["reason"] == "precondition_error":
            user_prompt = self.__get_prompt("precondition_error_user")
            user_prompt = user_prompt.replace(
                "{new_errors}", cause_of_failure["error"].new_errors)
            return user_prompt
        raise ValueError(
            f"Unknown cause_of_failure reason: {cause_of_failure['reason']}")

    def __execute_make(self) -> bool:
        logger.info("Executing 'make' into '%s'", self.harness_dir)
        with subprocess.Popen(
            ["make"],
            cwd=self.harness_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        ) as process:
            _stdout, stderr = process.communicate()
        if process.returncode == 0:
            logger.info("Execution of 'make' completed sucessfully.")
            return True
        logger.warning(
            "Execution of 'make' completed with error code %i.", process.returncode)
        # logger.warning(stderr.decode("utf-8"))
        return False

    def __pop_error(self) -> CBMCError:  # TODO: Refactor Error Handling
        error_report = ErrorReport(
            extract_errors_and_payload(self.target_func, self.harness_dir)
        )
        error = error_report.get_next_error()
        error[2].cluster = error[0]
        error[2].id_error = error[1]
        return error[2]

    def __get_prompt(self, prompt_name: str) -> str:
        with open(f"prompts/debugger/{prompt_name}.prompt", encoding="utf-8") as f:
            return f.read()

    def __get_advice(self, cluster: str):
        return get_advice_for_cluster(cluster, self.target_func)
