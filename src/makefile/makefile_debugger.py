
import sys
import os
import subprocess
import json
import shutil
import time
from typing import Any, Callable, Type
import uuid
from dotenv import load_dotenv
from agent import AIAgent
from pathlib import Path
from makefile.output_models import MakefileFields
from commons.models import GPT, Generable
from commons.utils import Status
from logger import setup_logger

load_dotenv()
logger = setup_logger(__name__)

class MakefileDebugger(AIAgent, Generable):


    def __init__(self, args, project_container):
        super().__init__(
            "MakefileGenerator",
            args,
            project_container
        )
        
        self._max_attempts = 30

    def get_coverage_dict(self, json_path: str) -> dict:
        with open(json_path, "r") as f:
            data = json.load(f)
        # Navigate to the overall_coverage section
        return data.get("viewer-coverage", {}).get("overall_coverage", {})

    def get_reachable_functions(self, json_path: str) -> dict:
        with open(json_path, "r") as f:
            data = json.load(f)
        reachable = data.get("viewer-reachable", {}).get("reachable", {})
        num_files = len(reachable)
        num_functions = sum(len(funcs) for funcs in reachable.values())
        return {"num_files": num_files, "num_functions": num_functions}

    def print_coverage(self, proof_dir: Path):
        print(f"Report for {proof_dir}:")
        report_path = os.path.join(proof_dir, "build/report/json")
        coverage_report = os.path.join(report_path, "viewer-coverage.json")
        if os.path.exists(coverage_report):
            coverage_dict = self.get_coverage_dict(coverage_report)
            print(f"Coverage:\n{coverage_dict}")
        reachability_report = os.path.join(report_path, "viewer-reachable.json")
        if os.path.exists(reachability_report):
            reachable_dict = self.get_reachable_functions(reachability_report)
            print(f"Reachable functions:\n{reachable_dict}")

    
    def validate_linked_target(self) -> bool:

        goto_file = os.path.join(self.harness_dir, "build", f"{self.target_function}.goto")
        if not os.path.exists(goto_file):
            logger.error(f"GOTO file not found: {goto_file}")
            return False

        goto_symbols_result = self.execute_command(
            f"goto-instrument --show-symbol-table {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60,
        )
        if goto_symbols_result["exit_code"] != 0:
            logger.error("Failed to get symbol table from GOTO binary.")
            return False

        try:
            goto_symbols = json.loads(goto_symbols_result["stdout"])
        except Exception as e:
            logger.error(f"Failed to parse goto-instrument JSON output: {e}")
            return False

        if len(goto_symbols) != 3 or "symbolTable" not in goto_symbols[2]:
            logger.error("Unexpected format of goto symbols output.")
            return False

        goto_symbols_dict = goto_symbols[2].get("symbolTable", {})
        if not goto_symbols_dict or self.target_function not in goto_symbols_dict:
            logger.error(f"Target function {self.target_function} not found in GOTO binary.")
            return False

        target_function_location = (
            goto_symbols_dict.get(self.target_function, {})
            .get("location", {})
            .get("namedSub", {})
        )

        file_rel = target_function_location.get("file", {}).get("id")
        wd = target_function_location.get("working_directory", {}).get("id")

        if not file_rel or not wd:
            logger.error(
                f"Missing location info for {self.target_function}: "
                f"file={file_rel!r}, working_directory={wd!r}"
            )
            return False

        # file_rel is relative to wd (and may contain ../../..), so normalize to an absolute path
        referenced_full_path = os.path.realpath(os.path.abspath(os.path.join(wd, file_rel)))
        expected_full_path = os.path.realpath(os.path.abspath(self.target_file_path))

        if referenced_full_path != expected_full_path:
            logger.error(
                "Linked target file mismatch.\n"
                f"  expected:   {expected_full_path}\n"
                f"  referenced: {referenced_full_path}\n"
                f"  (wd={wd}, rel={file_rel})"
            )
            return False

        return True
    
    def validate_called_target(self) -> bool:
        """
        Validates that the harness calls the target function by checking the
        reachable call graph output contains: 'harness -> <target_function>'.
        """
        goto_path = os.path.join("build", f"{self.target_function}.goto")

        callgraph_result = self.execute_command(
            f"goto-instrument --reachable-call-graph {goto_path}",
            workdir=self.harness_dir,
            timeout=60,
        )
        if callgraph_result["exit_code"] != 0:
            logger.error(
                "Failed to compute reachable call graph.\n"
                f"cmd: goto-instrument --reachable-call-graph {goto_path}\n"
                f"stderr: {callgraph_result.get('stderr', '')}"
            )
            return False

        stdout = callgraph_result.get("stdout", "")
        needle = f"harness -> {self.target_function}"

        for line in stdout.splitlines():
            if line.strip() == needle:
                return True

        logger.error(
            f"Call graph does not contain expected edge '{needle}'.\n"
        )
        return False



    def prepare_prompt(self, make_results):
        # Create the system prompt
        with open('prompts/gen_makefile_system.prompt', 'r') as file:
            system_prompt = file.read()

        with open('src/makefile/Makefile.example', 'r') as file:
            example_makefile = file.read()

        system_prompt = system_prompt.replace('{SAMPLE_MAKEFILE}', example_makefile)

        # Create the user prompt
        with open('prompts/gen_makefile_user.prompt', 'r') as file:
            user_prompt = file.read()

        makefile_content = self.get_makefile()
        harness_content = self.get_harness()

        user_prompt = user_prompt.replace('{TARGET_FUNC}', self.target_function)
        user_prompt = user_prompt.replace('{MAKEFILE_DIR}', self.harness_dir)
        user_prompt = user_prompt.replace('{PROJECT_DIR}', self.root_dir)
        user_prompt = user_prompt.replace('{MAKEFILE_CONTENT}', makefile_content)
        user_prompt = user_prompt.replace('{HARNESS_CONTENT}', harness_content)
        user_prompt = user_prompt.replace('{MAKE_ERROR}', make_results.get('stderr', ''))   

        return system_prompt, user_prompt

    def generate(self) -> bool:
        """
        Main function to generate the Makefile using the LLM.
        """

        # Next, we build and see if it succeeds
        make_results = self.run_make(compile_only=True)

        attempts = 1

        system_prompt, user_prompt = self.prepare_prompt(make_results)
        tools = self.get_tools()

        logger.info(f'System Prompt:\n{system_prompt}')

        status = Status.ERROR

        conversation = []

        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)
        
        # Finally, we iteratively call the LLM to fix any errors until it succeeds
        while user_prompt and attempts <= self._max_attempts:

            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, MakefileFields, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response or not isinstance(llm_response, MakefileFields):
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n"
                self.log_task_attempt("makefile_generation", attempts, llm_data, "invalid_response")
                continue

            if not llm_response.updated_makefile and not llm_response.updated_harness:
                logger.error("The LLM gave up and decided it cannot resolve this error.")
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "no_modifications")
                status = Status.ERROR
                break

            if llm_response.updated_makefile:
                self.update_makefile(llm_response.updated_makefile)
            if llm_response.updated_harness:
                self.update_harness(llm_response.updated_harness)

            make_results = self.run_make(compile_only=True)

            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.FAILURE or make_results.get('exit_code', -1) != 0:
                logger.info("Make command failed; reprompting LLM with make results.")
                system_prompt, user_prompt = self.prepare_prompt(make_results)
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "compilation_error")
                continue
            elif status_code == Status.ERROR or status_code == Status.TIMEOUT:
                logger.error("An error or timeout occurred when running make.")
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "make_error")
                status = status_code
                break

            logger.info("Makefile successfully generated and compilation succeeded.")

            if not self.validate_linked_target() or not self.validate_called_target():
                logger.error("The target function is not linked in the compiled binary.")
                user_prompt = f"""
                The generated harness does not call the function {self.target_function} in the file {self.target_file_path}. 
                Please, update the harness to ensure the correct function is called.\n
                """
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "target_not_linked")
                continue

            if status_code == Status.SUCCESS:
                
                self.log_task_attempt("makefile_debugger", attempts, llm_data, "")
                status = Status.SUCCESS
                break
            

            attempts += 1  

        self.log_task_result("makefile_debugger", status == Status.SUCCESS, attempts)

        if status != Status.SUCCESS:
            self.restore_backup(tag)
        self.discard_backup(tag)

        return status == Status.SUCCESS

    def _update_files_in_vector_store(self):
        pass

if __name__ == "__main__":

    if len(sys.argv) < 5:
        logger.error("Usage: python makefile_debugger.py <target function> <root dir> <harness path> <file path>")
        sys.exit(1)

    target_function = sys.argv[1]
    root_dir = sys.argv[2]
    harness_path = sys.argv[3]
    file_path = sys.argv[4]
    openai_api_key = os.getenv("OPENAI_API_KEY", None)
    if not openai_api_key:
        raise EnvironmentError("No OpenAI API key found")

    args = type("Args", (object,), {
        "target_function_name": target_function,
        "root_dir": root_dir,
        "harness_path": harness_path,
        "target_file_path": file_path,
        "metrics_file": None
    })()

    makefile_generator = MakefileDebugger(args, None)
    makefile_generator.generate()

