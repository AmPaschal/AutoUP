""" Debugger class"""

# System
from pathlib import Path
import subprocess
import os

# Utils
import json

# AutoUp
from agent import AIAgent
from commons.models import GPT
from debugger.output_models import ModelOutput
from logger import setup_logger


# OLD
from debugger.error_report import ErrorReport, CBMCError
from debugger.parser import extract_errors_and_payload
from debugger.advice import get_advice_for_cluster

logger = setup_logger(__name__)


class ProofDebugger(AIAgent):
    """Agentic Proof Debugger"""

    def __init__(self, harness_path, root_dir, target_function_name, project_container):
        super().__init__(
            agent_name="debugger",
            project_container=project_container,
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        self.harness_path = harness_path
        self.root_dir = root_dir
        self.target_func = f"{target_function_name}_harness.c"
        self.target_file_path = str(
            Path(harness_path) / f"{target_function_name}_harness.c")
        logger.info("self.harness_path %s", self.harness_path)
        logger.info("self.root_dir %s", self.root_dir)
        logger.info("self.target_func %s", self.target_func)
        logger.info("self.target_file_path %s", self.target_file_path)
        self.__max_attempts = 3


        self.__cause_of_failure = None
        self.__previous_response = None

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
                logger.info("Error resolved!")
                return True
        logger.info("Error not resolved...")
        return False

    def __refine_harness_file(self, error):
        system_prompt = self.__get_prompt("general_system")
        user_prompt = self.__compute_user_prompt(error)
        logger.info("System prompt: %s", system_prompt)
        logger.info("User prompt: %s", user_prompt)
        response = self.llm.chat_llm(
            system_messages=system_prompt,
            input_messages=user_prompt,
            output_format=ModelOutput,
            llm_tools=self.get_tools(),
            call_function=self.handle_tool_calls,
            parsed=False
        )
        logger.info("LLM response: \n%s", json.dumps(
            json.loads(response.output_text), indent=4))
        self.__update_harness(json.loads(
            response.output_text)['updated_harness_file_content'])

    def __update_harness(self, harness_content: str):
        logger.info("Updated harness file content: \n%s", harness_content)
        with open(self.target_file_path, "w+", encoding="utf-8") as f:
            f.write(harness_content)

    def __is_error_solved(self, error) -> bool:
        new_error = self.__pop_error()
        return new_error is None or new_error.cluster != error.cluster

    def __compute_user_prompt(self, error: CBMCError):
        if self.__previous_response is None:
            advice = self.__get_advice(error.cluster)
            user_prompt = self.__get_prompt("no_previous_user")
            user_prompt = user_prompt.replace("{message}", error.msg)
            user_prompt = user_prompt.replace("{stack}", '\n'.join(
                [f'in {func}, Line: {line}' for func, line in error.stack]))
            user_prompt = user_prompt.replace(
                "{variables}", json.dumps(error.vars, indent=4))
            user_prompt = user_prompt.replace("{advice}", '\n'.join(
                [f'{i + 1}. {step}' for i, step in enumerate(advice)]))
            user_prompt = user_prompt.replace("{harness}", self.target_func)
            return user_prompt
        if self.__cause_of_failure is None:
            user_prompt = self.__get_prompt("general_error_user")
            user_prompt = user_prompt.replace(
                "{errors}", json.dumps(error.vars, indent=4))
            return user_prompt
        if self.__cause_of_failure["reason"] == "harness_update_error":
            user_prompt = self.__get_prompt("harness_update_error_user")
            user_prompt = user_prompt.replace(
                "{function}", self.__cause_of_failure["error"].func)
            user_prompt = user_prompt.replace(
                "{previous_line}", self.__cause_of_failure["error"].prev_line)
            return user_prompt
        if self.__cause_of_failure["reason"] == "syntax_error":
            user_prompt = self.__get_prompt("syntax_error_user")
            return user_prompt
        if self.__cause_of_failure["reason"] == "coverage_error":
            user_prompt = self.__get_prompt("coverage_error_user")
            return user_prompt
        if self.__cause_of_failure["reason"] == "precondition_error":
            user_prompt = self.__get_prompt("precondition_error_user")
            user_prompt = user_prompt.replace(
                "{new_errors}", self.__cause_of_failure["error"].new_errors)
            return user_prompt
        raise ValueError(
            f"Unknown cause_of_failure reason: {self.__cause_of_failure['reason']}")

    def __execute_make(self) -> bool:
        logger.info("Executing 'make' into '%s'", self.harness_path)
        with subprocess.Popen(
            ["make"],
            cwd=self.harness_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        ) as process:
            _stdout, stderr = process.communicate()
        if process.returncode == 0:
            logger.info("Execution of 'make' completed sucessfully.")
            return True
        logger.warning(
            "Execution of 'make' completed with error code %i.", process.returncode,
        )
        # logger.warning(stderr.decode("utf-8"))
        return False

    def __create_backup(self):
        backup_path = os.path.join(
            self.harness_path, f"{self.target_func}.backup",
        )
        with open(self.target_file_path, "r", encoding="utf-8") as src:
            with open(backup_path, "w", encoding="utf-8") as dst:
                dst.write(src.read())

    def __pop_error(self) -> CBMCError | None:  # TODO: Refactor Error Handling
        error_report = ErrorReport(
            extract_errors_and_payload(self.target_func, self.target_file_path)
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
