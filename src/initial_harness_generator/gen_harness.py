

from pathlib import Path
import json
import os
import re
from agent import AIAgent
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse
from logger import setup_logger
from makefile.makefile_debugger import MakefileDebugger

logger = setup_logger(__name__)
class InitialHarnessGenerator(AIAgent, Generable):

    def __init__(self, args, project_container):
        super().__init__(
            "InitialHarnessGenerator",
            args,
            project_container
        )
        self._max_attempts = 5

    def extract_function_code(self, file_path, function_name):
        if not os.path.exists(file_path):
            print(f"[ERROR] File not found: {file_path}")
            return None

        with open(file_path, 'r', encoding="utf-8", errors="ignore") as file:
            lines = file.readlines()

        start_index = None
        brace_count = 0
        inside_function = False
        waiting_for_brace = False
        function_lines = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Detect function start - could be single or multi-line before opening brace
            if not inside_function and function_name in stripped and "(" in stripped:
                # Append first line of function signature
                function_lines.append(line)

                # Check if opening brace is here
                if "{" in stripped:
                    inside_function = True
                    brace_count += stripped.count("{") - stripped.count("}")
                else:
                    waiting_for_brace = True
                continue

            # If we're still collecting function signature until we find "{"
            if waiting_for_brace:
                function_lines.append(line)
                if "{" in stripped:
                    inside_function = True
                    waiting_for_brace = False
                    brace_count += stripped.count("{") - stripped.count("}")
                continue

            # If inside the function body, collect lines and track braces
            if inside_function:
                function_lines.append(line)
                brace_count += stripped.count("{") - stripped.count("}")
                if brace_count == 0:
                    break

        if function_lines and inside_function:
            return "".join(function_lines)
        else:
            print(f"[ERROR] Function '{function_name}' not found in {file_path}")
            return None

    def prepare_prompt(self):
        with open("prompts/harness_generator_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/harness_generator_user.prompt", "r") as f:
            user_prompt = f.read()
        
        target_relative_root = self.get_relative_path(self.root_dir, self.target_file_path)
        include_line = f'#include "{target_relative_root}"'

        user_prompt = user_prompt.replace("{FUNCTION_NAME}", self.target_function)
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)
        user_prompt = user_prompt.replace("{FUNCTION_SOURCE_FILE}", self.target_file_path)
        user_prompt = user_prompt.replace("{INCLUDE_TARGET_FILE}", include_line)
        function_source = self.extract_function_code(self.target_file_path, self.target_function)
        if function_source:
            user_prompt = user_prompt.replace("{FUNCTION_SOURCE}", function_source)
        else:
            raise ValueError(f"Function {self.target_function} not found in {self.target_file_path}")
        return system_prompt, user_prompt

    def get_relative_path(self, base_path, target_path):
        """We want to get the relative path of target, in terms of how many ../ we need to get back to base"""
        base_path = Path(base_path).resolve()
        target_path = Path(target_path).resolve()
        return target_path.relative_to(base_path)
    
    def get_backward_path(self, base_path, target_path):
        """We want to get the relative path of target, in terms of how many ../ we need to get back to base"""
        relative_path = self.get_relative_path(base_path, target_path)

        up_levels = len(relative_path.parts)

        go_back = '/'.join([".."] * up_levels)

        return go_back

    def create_makefile_include(self):
        """Copy makefile.include from docker to harness parent directory"""
        src_path = os.path.join('makefiles', 'Makefile.include')
        dest_path = os.path.join(os.path.dirname(self.harness_dir), 'Makefile.include')
        if os.path.exists(dest_path):
            logger.info(f'Makefile.include already exists at {dest_path}, skipping copy.')
            return
        # Copy inside the container
        copy_cmd = f"cp {src_path} {dest_path}"
        copy_results = self.project_container.execute(copy_cmd, workdir='/')
        if copy_results.get('exit_code', -1) != 0:
            logger.error(f'Failed to copy Makefile.include: {copy_results.get("stderr", "")}')
            return
        logger.info(f'Copied Makefile.include to {dest_path}')

    def setup_initial_makefile(self, initial_configs):

        harness_relative_root = self.get_backward_path(self.root_dir, self.harness_dir)

        with open('src/makefile/Makefile.template', 'r') as file:
            makefile = file.read()

        makefile = makefile.replace('{ROOT}', str(harness_relative_root))
        makefile = makefile.replace('{H_ENTRY}', self.target_function)

        if initial_configs:
            config_string = " ".join(f"-D{cfg}=1" for cfg in initial_configs)
        else:
            config_string = ""

        makefile = makefile.replace('{H_DEF}', config_string)

        return makefile

    def extract_configs_from_sourcefile(self):
        with open(self.target_file_path, 'r', encoding="utf-8", errors="ignore") as file:
            lines = file.readlines()

        if not lines:
            return []

        configs = set()
        pattern = r'^\s*#\s*ifdef\s+([A-Za-z_][A-Za-z0-9_]*)'

        for line in lines:
            match = re.match(pattern, line)
            if match:
                config = match.group(1)
                configs.add(config)

        return list(configs)

    def generate(self) -> bool:

        # First generate initial harnesses
        os.makedirs(self.harness_dir, exist_ok=True)

        system_prompt, user_prompt = self.prepare_prompt()
        tools = self.get_tools()
        attempts = 0

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []   
        harness_generated = False

        while user_prompt and attempts <= self._max_attempts:

            attempts += 1
            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, HarnessResponse, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response:
                self.log_task_attempt("harness_generation", attempts, llm_data, "invalid_response")
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n" 
            else:
                self.log_task_attempt("harness_generation", attempts, llm_data, "")
                self.update_harness(llm_response.harness_code)
                harness_generated = True
                break
        
        self.log_task_result("harness_generation", harness_generated, attempts)

        if not harness_generated:
            logger.error("Failed to generate initial harness within max attempts.")
            return False

        # Then generate initial Makefile

        # Copy makefile.include from docker to harness parent directory
        self.create_makefile_include()

        initial_configs = self.extract_configs_from_sourcefile()

        # We setup the initial Makefile
        makefile = self.setup_initial_makefile(initial_configs)
        self.update_makefile(makefile)   

        # Now, we try to resolve all the make errors
        makefile_debugger = MakefileDebugger(
                                args=self.args,
                                project_container=self.project_container
                            )
        status = makefile_debugger.generate()

        self.save_status('harness')
        return status
        