
import json
import os
import re
import shutil
from time import time
import uuid

from anyio import Path
from agent import AIAgent
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse
from logger import setup_logger
from commons.utils import Status

logger = setup_logger(__name__)

class StubGenerator(AIAgent, Generable):

    def __init__(self, args, project_container):
        super().__init__(
            "StubGenerator",
            args,
            project_container=project_container
        )
        self._max_attempts = 5

    def extract_function_signature(self, file_path: str, func_name: str, start_line: int) -> str:
        """
        Extracts the function signature for `func_name` starting at `start_line`
        from the given C/C++ source file.
        
        - It captures multi-line signatures.
        - Stops reading once it encounters the opening '{' or a semicolon (';').
        
        Returns the full signature as a single string (without the body).
        """

        signature_lines = []
        inside_signature = False

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        # Start reading from the specified line (1-based index)
        for i in range(start_line - 1, len(lines)):
            line = lines[i].strip()

            # Start collecting once the function name appears
            if not inside_signature and re.search(rf'\b{re.escape(func_name)}\b', line):
                inside_signature = True

            if inside_signature:
                signature_lines.append(line)

                # Stop if function definition starts or ends
                if '{' in line or ';' in line:
                    break

        # Join and clean up extra whitespace and line breaks
        signature = ' '.join(signature_lines)
        signature = re.sub(r'\s+', ' ', signature)

        # Optionally remove the opening brace if it's there
        signature = signature.split('{')[0].strip()

        return signature

    def prepare_initial_prompt(self, functions_to_stub):
        with open("prompts/gen_stubs_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/gen_stubs_user.prompt", "r") as f:
            user_prompt = f.read()

        # Prepare list of function signatures to stub
        stubs_list = []

        for func in functions_to_stub:
            stub_info = f"""
            Function Name: {func['name']}
            Signature: {self.extract_function_signature(func['file'], func['name'], func['line'])}
            Source File: {func['file']}
            """
            stubs_list.append(stub_info)

        stubs_text = "\n\n".join(stubs_list)

        # Get the existing harness code
        harness_file_path = os.path.join(self.harness_dir, f'{self.target_function}_harness.c')
        with open(harness_file_path, 'r') as f:
            harness_code = f.read()

        makefile_path = os.path.join(self.harness_dir, 'Makefile')
        with open(makefile_path, 'r') as f:
            makefile_code = f.read()

        user_prompt = user_prompt.replace("{HARNESS_CODE}", harness_code)
        user_prompt = user_prompt.replace("{MAKEFILE_CODE}", makefile_code)
        user_prompt = user_prompt.replace("{STUBS_REQUIRED}", stubs_text)   
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)

        return system_prompt, user_prompt
    
    def save_harness(self, harness_code):
        os.makedirs(self.harness_dir, exist_ok=True)
        harness_file_path = os.path.join(self.harness_dir, f'{self.target_function}_harness.c')
        
        with open(harness_file_path, 'w') as f:
            f.write(harness_code)
        
        logger.info(f'Harness saved to {harness_file_path}')

        return harness_file_path

    def get_reachable_functions(self, reachable_output: str) -> set:
        goto_reachable_lines = reachable_output.splitlines()

        unique_funcs = set()    

        if not goto_reachable_lines:
            logger.error("No reachable functions found in GOTO binary.")
            return unique_funcs

        for line in goto_reachable_lines:
            line = line.strip()
            if "->" in line:
                parts = [p.strip() for p in line.split("->")]
                if len(parts) == 2:
                    caller, callee = parts
                    unique_funcs.add(caller)
                    unique_funcs.add(callee)

        return unique_funcs

    def extract_functions_without_body_and_returning_pointer(self, goto_file: str):
        """
        Extract all functions in a GOTO binary that:
        - do not have a function body (isBodyAvailable == false)
        - have a pointer return type
        Returns a list of dicts: [{ "name": <func_name>, "pointee": <pointee_type> }]
        """

        # 1. Get list of all functions
        goto_functions_result = self.execute_command(
            f"goto-instrument --list-goto-functions {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60
        )
        if goto_functions_result['exit_code'] != 0:
            logger.error("Failed to find functions in GOTO binary.")
            return []
        
        goto_functions = json.loads(goto_functions_result['stdout'])

        if len(goto_functions) != 3 or "functions" not in goto_functions[2]:
            logger.error("Unexpected format of goto functions output.")
            return []
        
        goto_functions_list = goto_functions[2]["functions"]

        logger.info(f"Total functions found in GOTO binary: {len(goto_functions_list)}")

        # 2. Get all functions without bodies
        no_body_funcs = {
            func.get('name', '') for func in goto_functions_list
            if not func.get("isBodyAvailable", True) and not func.get("isInternal", True)
        }

        if not no_body_funcs:
            logger.info("No functions without bodies found.")
            return []

        logger.info(f"Number of functions without bodies: {len(no_body_funcs)}")

        # 3. Get symbol table with type info
        goto_symbols_result = self.execute_command(
            f"goto-instrument --show-symbol-table {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60
        )
        if goto_symbols_result['exit_code'] != 0:
            logger.error("Failed to get symbol table from GOTO binary.")
            return []
        
        goto_symbols = json.loads(goto_symbols_result['stdout'])

        if len(goto_symbols) != 3 or "symbolTable" not in goto_symbols[2]:
            logger.error("Unexpected format of goto symbols output.")
            return []

        goto_symbols_dict = goto_symbols[2]["symbolTable"]

        # 4. Get reachable functions from the GOTO binary
        goto_reachable_result = self.execute_command(
            f"goto-instrument --reachable-call-graph {goto_file}",
            workdir=self.harness_dir,
            timeout=60
        )
        if goto_reachable_result['exit_code'] != 0:
            logger.error("Failed to get reachable call graph functions from GOTO binary.")
            return []

        reachable_functions = self.get_reachable_functions(goto_reachable_result['stdout'])

        # 5. Filter undefined but reachable functions that return a pointer
        result = []
        for func_name in no_body_funcs:
            if func_name not in reachable_functions:
                # Skip functions that are not in the call graph
                continue
            func_symbol = goto_symbols_dict.get(func_name)
            if not func_symbol:
                logger.warning(f"Function symbol not found: {func_name}")
                continue

            func_type = func_symbol.get("type", {})
            ret_type = func_type.get("namedSub", {}).get("return_type", {})

            if ret_type.get("id") == "pointer":
                file_rel_path = func_symbol.get("location", {}).get("namedSub", {}).get("file", {}).get("id", "")
                base_path = func_symbol.get("location", {}).get("namedSub", {}).get("working_directory", {}).get("id", "")
                file_abs_path = os.path.normpath(os.path.join(base_path, file_rel_path))
                signature_line_str = func_symbol.get("location", {}).get("namedSub", {}).get("line", {}).get("id", "")
                line_number = int(signature_line_str) if signature_line_str.isdigit() else 0
                result.append({
                    "name": func_name,
                    "file": file_abs_path,
                    "line": line_number,
                })

        logger.info(f"Number of functions without bodies and returning pointers: {len(result)}")

        return result

    def generate(self) -> bool:

        self.run_make(compile_only=True)

        # 1. Get functions to stub
        goto_file = os.path.join(self.harness_dir, "build", f"{self.target_function}.goto")
        if not os.path.exists(goto_file):
            logger.error(f"GOTO file not found: {goto_file}")
            self.log_agent_result({"stubs_to_generate": None})
            return False
        
        functions_to_stub = self.extract_functions_without_body_and_returning_pointer(goto_file)

        if not functions_to_stub:
            logger.info("No functions found!")
            self.log_agent_result({"stubs_to_generate": 0})
            return True

        system_prompt, user_prompt = self.prepare_initial_prompt(functions_to_stub)
        tools = self.get_tools()
        attempts = 0

        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []

        stubs_to_generate = len(functions_to_stub)
        agent_result = {"stubs_to_generate": stubs_to_generate, "verification_status": False}
        while user_prompt and attempts < self._max_attempts:
            logger.info(f'User Prompt:\n{user_prompt}')

            # First, generate stubs using the LLM
            llm_response, _ = self.llm.chat_llm(system_prompt, 
                                                           user_prompt, 
                                                           HarnessResponse, 
                                                           llm_tools=tools, 
                                                           call_function=self.handle_tool_calls, 
                                                           conversation_history=conversation)

            if not llm_response or not isinstance(llm_response, HarnessResponse):
                user_prompt = "The LLM did not return a valid response. Please try again and provide response in the correct format.\n" 
                attempts += 1
                continue

            logger.info(f'LLM Response:\n{json.dumps(llm_response.to_dict(), indent=2)}')

            self.save_harness(llm_response.harness_code)

            # Now, try to build the harness using make
            make_results = self.run_make(compile_only=False)
            
            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.SUCCESS and make_results.get('exit_code', -1) == 0 and self.validate_verification_report():
                logger.info("Generated harness builds succeeded.")
                agent_result["verification_status"] = True
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

                attempts += 1
            else:
                logger.error("Make command failed to run.")
                break

        if attempts >= self._max_attempts:  
            logger.error("Failed to generate compilable harness after maximum attempts.")

        if agent_result.get("verification_status", False):
            self.discard_backup(tag)
        else:
            self.restore_backup(tag)

        self.log_agent_result(agent_result)
        self.save_status('stubs')
        return True
        
        