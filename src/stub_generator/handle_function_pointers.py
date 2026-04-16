import sys
import os
import json
import shlex
import uuid
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse, MakefileFields
from commons.utils import Status
from logger import setup_logger
from agent import AIAgent
from stub_generator.makefile_helpers import (
    get_makefile_list_var,
    get_makefile_var,
    get_h_def_entries,
    get_h_inc_entries,
)
from stub_generator.source_locations import (
    is_builtin_source_location,
    resolve_source_path,
)

logger = setup_logger(__name__)

class FunctionPointerHandler(AIAgent, Generable):
    def __init__(self, args, project_container):
        super().__init__(
            "FunctionPointerHandler",
            args,
            project_container=project_container
        )
        self._max_attempts = 3

    def get_makefile_list_var(self, makefile_content, var_name):
        return get_makefile_list_var(makefile_content, var_name)

    def get_makefile_var(self, makefile_content, var_name):
        return get_makefile_var(makefile_content, var_name)

    def get_h_def_entries(self):
        return get_h_def_entries(self.get_makefile())

    def get_h_inc_entries(self):
        return get_h_inc_entries(
            self.get_makefile(),
            self.harness_dir,
            default_root=self.root_dir,
        )

    def prepare_initial_prompt(self, function_pointers):
        with open("prompts/replace_function_pointers_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/replace_function_pointers_user.prompt", "r") as f:
            user_prompt = f.read()
            
        

        # Get the existing harness code
        harness_file_path = os.path.join(self.harness_dir, f'{self.target_function}_harness.c')
        with open(harness_file_path, 'r') as f:
            harness_code = f.read()

        makefile_path = os.path.join(self.harness_dir, 'Makefile')
        with open(makefile_path, 'r') as f:
            makefile_code = f.read()

        user_prompt = user_prompt.replace("{HARNESS_CODE}", harness_code)
        user_prompt = user_prompt.replace("{MAKEFILE_CODE}", makefile_code)
        user_prompt = user_prompt.replace("{STUBS_REQUIRED}", json.dumps(function_pointers, indent=2))
        user_prompt = user_prompt.replace("{HARNESS_DIR}", self.harness_dir)   
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)

        return system_prompt, user_prompt

    def _sanitize_function_pointers(self, function_pointers):
        sanitized = []
        for fp in function_pointers:
            fp_copy = dict(fp)
            file_path = fp_copy.get("file", "")

            if is_builtin_source_location(file_path):
                logger.info(
                    "Skipping function-pointer candidate from builtin pseudo-file '%s'.",
                    file_path,
                )
                continue

            resolved_path = resolve_source_path(file_path)
            if file_path and resolved_path and not os.path.isfile(resolved_path):
                logger.warning(
                    "Function-pointer candidate file not found; clearing file hint: %s",
                    resolved_path,
                )
                fp_copy["file"] = ""
            elif resolved_path:
                fp_copy["file"] = resolved_path

            sanitized.append(fp_copy)

        return sanitized

    def generate(self, verify_after_generation: bool = True) -> bool:
        logger.info(f"Analyzing file: {self.target_file_path} for entry point: {self.target_function}")
        makefile_content = self.get_makefile()
        if get_makefile_list_var(makefile_content, "LINK"):
            logger.info("Containerized multi-file analysis enabled for LINK entries.")
        else:
            logger.info("Containerized single-file analysis enabled.")

        analyzer_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "find_function_pointers.py")
        )
        makefile_path = os.path.join(self.harness_dir, "Makefile")
        analyzer_cmd = " ".join(
            [
                f"AUTOUP_ROOT_DIR={shlex.quote(self.root_dir)}",
                "python3",
                shlex.quote(analyzer_path),
                shlex.quote(self.target_file_path),
                shlex.quote(self.target_function),
                shlex.quote(makefile_path),
            ]
        )

        analysis_result = self.project_container.execute(
            analyzer_cmd,
            workdir=self.harness_dir,
            timeout=60,
        )
        if analysis_result.get("exit_code", 1) != 0:
            logger.error(
                "Function pointer analysis failed with exit code %s. stderr: %s",
                analysis_result.get("exit_code"),
                analysis_result.get("stderr", "").strip(),
            )
            return False

        try:
            fp_results = json.loads(analysis_result.get("stdout", ""))
        except json.JSONDecodeError as exc:
            logger.error(
                "Function pointer analysis returned invalid JSON: %s. stdout: %s",
                exc,
                analysis_result.get("stdout", "").strip(),
            )
            return False

        fp_results = self._sanitize_function_pointers(fp_results)
        
        if not fp_results:
            logger.info("No function pointers found.")
            return True # Nothing to do
            
        logger.info(f"Found {len(fp_results)} function pointer calls.")

        system_prompt, user_prompt = self.prepare_initial_prompt(fp_results)
        tools = self.get_tools()
        attempts = 0

        

        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []
        status = Status.ERROR

        stubs_to_generate = len(fp_results)
        agent_result = {
            "fp_stubs_to_generate": stubs_to_generate, 
            "generation_succeeded": False,
            "verification_status": False,
            }
        while user_prompt and attempts < self._max_attempts:
            logger.info(f'User Prompt:\n{user_prompt}')

            # First, generate stubs using the LLM
            llm_response, llm_data = self.llm.chat_llm(system_prompt, 
                                                           user_prompt, 
                                                           MakefileFields, 
                                                           llm_tools=tools, 
                                                           call_function=self.handle_tool_calls, 
                                                           conversation_history=conversation)

            if not llm_response or not isinstance(llm_response, MakefileFields):
                user_prompt = "The LLM did not return a valid response. Please try again and provide response in the correct format.\n" 
                attempts += 1
                continue

            logger.info(f'LLM Response:\n{json.dumps(llm_response.to_dict(), indent=2)}')

            if not llm_response.updated_makefile and not llm_response.updated_harness:
                logger.error("The LLM gave up and decided it cannot resolve this error.")
                self.log_task_attempt("makefile_debugger", attempts, llm_response, "no_modifications")
                status = Status.ERROR
                break

            if llm_response.updated_makefile:
                self.update_makefile(llm_response.updated_makefile)
            if llm_response.updated_harness:
                self.update_harness(llm_response.updated_harness)

            # Now, try to build the harness using make
            make_results = self.run_make(
                compile_only=not verify_after_generation,
            )
            
            status_code = make_results.get('status', Status.ERROR)

            if (
                status_code == Status.SUCCESS
                and make_results.get('exit_code', -1) == 0
                and (
                    not verify_after_generation
                    or self.validate_verification_report()
                )
            ):
                logger.info("Generated harness builds succeeded.")
                self.log_task_attempt("function_pointer_generation", attempts, llm_data, None)
                agent_result["generation_succeeded"] = True
                agent_result["verification_status"] = verify_after_generation
                status = Status.SUCCESS
                break    
            elif status_code == Status.FAILURE:
                logger.info("Make command failed; reprompting LLM with make results.")

                user_prompt = f"""
                The previously generated harness did not compile successfully. 
                Here are the results from the make command:

                Exit Code: {make_results.get('exit_code', -1)}
                Stdout: {make_results.get('stdout', '')}
                Stderr: {make_results.get('stderr', '')}

                Please analyze the errors and generate an updated harness that addresses these issues.
                """

                self.log_task_attempt("function_pointer_generation", attempts, llm_data, "compilation_failed")
                attempts += 1
            else:
                self.log_task_attempt("function_pointer_generation", attempts, llm_data, "make_error")
                logger.error("Make command failed to run.")
                break

        if attempts >= self._max_attempts:
            logger.error("Failed to generate compilable harness after maximum attempts.")

        if status == Status.SUCCESS:
            self.discard_backup(tag)
            self.save_status('fp')
        else:
            self.restore_backup(tag)

        self.log_agent_result(agent_result)
        return agent_result.get("generation_succeeded", False)
        
