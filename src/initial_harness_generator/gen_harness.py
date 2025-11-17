

from pathlib import Path
import json
import os
from agent import AIAgent
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse
from logger import setup_logger

logger = setup_logger(__name__)
class InitialHarnessGenerator(AIAgent, Generable):

    def __init__(self, root_dir, harness_dir, target_func, target_file_path, metrics_file, project_container):
        super().__init__(
            "InitialHarnessGenerator",
            project_container,
            harness_dir=harness_dir, 
            metrics_file=metrics_file
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        self.root_dir = root_dir
        self.harness_dir = harness_dir
        self.target_func = target_func
        self.target_file_path = target_file_path
        self._max_attempts = 5
        
        harness_dir = Path(harness_dir)
        harness_dir.mkdir(parents=True, exist_ok=True)

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

        user_prompt = user_prompt.replace("{FUNCTION_NAME}", self.target_func)
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)
        user_prompt = user_prompt.replace("{FUNCTION_SOURCE_FILE}", self.target_file_path)
        function_source = self.extract_function_code(self.target_file_path, self.target_func)
        if function_source:
            user_prompt = user_prompt.replace("{FUNCTION_SOURCE}", function_source)
        else:
            raise ValueError(f"Function {self.target_func} not found in {self.target_file_path}")

        return system_prompt, user_prompt
    
    def save_harness(self, harness_code):
        os.makedirs(self.harness_dir, exist_ok=True)
        harness_file_path = os.path.join(self.harness_dir, f'{self.target_func}_harness.c')
        
        with open(harness_file_path, 'w') as f:
            f.write(harness_code)
        
        logger.info(f'Harness saved to {harness_file_path}')

        return harness_file_path

    def generate(self) -> bool:

        # Generate initial harnesses

        system_prompt, user_prompt = self.prepare_prompt()
        tools = self.get_tools()
        attempts = 1

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []   

        while user_prompt and attempts <= self._max_attempts:

            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, HarnessResponse, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response:
                self.log_task_attempt("harness_generation", attempts, llm_data, "invalid_response")
                user_prompt = "The LLM did not return a valid response. Please provide a response using the expected format.\n" 
                attempts += 1
                continue

            self.save_harness(llm_response.harness_code)
            self.log_task_attempt("harness_generation", attempts, llm_data, "")
            self.log_task_result("harness_generation", True, attempts)
            return True

        logger.error("Failed to generate harness after maximum attempts.")
        self.log_task_result("harness_generation", False, attempts)

        return False
        