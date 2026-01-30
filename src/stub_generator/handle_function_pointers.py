import sys
import os
import json
import re
import uuid
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse, MakefileFields
from commons.utils import Status
from logger import setup_logger
from agent import AIAgent
from stub_generator.find_function_pointers import analyze_file

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
        """Extract a list of values from a multi-line makefile variable."""
        lines = makefile_content.splitlines()
        values = []
        inside_var = False
        
        for line in lines:
            stripped = line.strip()
            
            # Check for start of variable
            if not inside_var:
                # Matches VAR = ... or VAR += ... or VAR ?= ...
                if re.match(rf'^{var_name}\s*[\?\+]?=', stripped):
                    inside_var = True
                    # Extract content after =
                    part = re.split(r'[\?\+]?=', stripped, 1)[1].strip()
                    if part:
                        # Handle backslash at end
                        if part.endswith('\\'):
                            part = part[:-1].strip()
                        values.extend(part.split())
                        # If line didn't end with \, then variable def ends
                        if not stripped.endswith('\\'):
                            inside_var = False
                continue
            
            # Inside variable
            if inside_var:
                part = stripped
                # Check for continuation
                is_continuation = part.endswith('\\')
                if is_continuation:
                    part = part[:-1].strip()
                
                if part:
                    values.extend(part.split())
                
                if not is_continuation:
                    inside_var = False
                    
        return values



    def get_makefile_var(self, makefile_content, var_name):
        """Simple extraction of a variable value from makefile content."""
        # Handles VAR ?= val or VAR = val
        match = re.search(rf'^{var_name}\s*\??=\s*(.*)', makefile_content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None

    def get_h_def_entries(self):
        return self.get_makefile_list_var(self.get_makefile(), "H_DEF")

    def expand_vars(self, flags, root_path):
        """Expand $(ROOT) in flags."""
        return [f.replace("$(ROOT)", root_path).replace("${ROOT}", root_path) for f in flags]

    def get_h_inc_entries(self):
        makefile_content = self.get_makefile()
        # Determine ROOT
        root_val = self.get_makefile_var(makefile_content, "ROOT")
        if not root_val:
            root_val = self.root_dir
        
        if root_val.startswith("."):
            root_val = os.path.normpath(os.path.join(self.harness_dir, root_val))

        # Extract H_INC
        flags = self.get_makefile_list_var(makefile_content, "H_INC")
        return self.expand_vars(flags, root_val)

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
    
    def run_make(self, compile_only: bool = True) -> dict:
        make_cmd = "make compile -j4" if compile_only else "make -j4"
        make_results = self.execute_command(make_cmd, workdir=self.harness_dir, timeout=1500)
        logger.info('Stdout:\n' + make_results.get('stdout', ''))
        logger.info('Stderr:\n' + make_results.get('stderr', ''))
        return make_results

    def generate(self) -> bool:

        h_def_args = self.get_h_def_entries()
        
        logger.info(f"Analyzing file: {self.target_file_path} for entry point: {self.target_function}")
        fp_results = analyze_file(self.target_file_path, self.target_function, h_def_args)
        
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
            make_results = self.run_make(compile_only=False)
            
            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.SUCCESS and make_results.get('exit_code', -1) == 0:
                logger.info("Generated harness builds succeeded.")
                self.log_task_attempt("function_pointer_generation", attempts, llm_data, None)
                agent_result["verification_status"] = True
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
        return agent_result.get("verification_status", False)
        
