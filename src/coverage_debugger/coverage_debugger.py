

import logging
import shutil
import subprocess
import json
import os
from agent import AIAgent
from commons.models import GPT
from makefile.output_models import CoverageDebuggerResponse
from commons.utils import Status


class CoverageDebugger(AIAgent):

    def __init__(self, root_dir, harness_dir, target_func, target_file_path, project_container):
        super().__init__(
            "CoverageDebugger",
            project_container
        )
        self.llm = GPT(name='gpt-5', max_input_tokens=270000)
        self.root_dir = root_dir
        self.harness_dir = harness_dir
        self.target_func = target_func
        self.target_file_path = target_file_path
        self._max_attempts = 3

    def _get_function_coverage_status(self, file_path, function_name):
        coverage_report_path = os.path.join(self.harness_dir, "build/report/json/viewer-coverage.json")
        if not os.path.exists(coverage_report_path):
            logging.error(f"[ERROR] Coverage report not found: {coverage_report_path}")
            return None

        with open(coverage_report_path, "r") as f:
            coverage_data = json.load(f)

        viewer_coverage = coverage_data.get("viewer-coverage", {})
        function_coverage = (
            viewer_coverage.get("function_coverage", {}).get(file_path, {}).get(function_name, {})
        )

        if not function_coverage:
            logging.error(f"[ERROR] Function '{function_name}' not found in coverage report for file '{file_path}'.")
            return None

        return function_coverage

    def _get_next_uncovered_function(self, skip=0):
        coverage_report_path = os.path.join(self.harness_dir, "build/report/json/viewer-coverage.json")
        if not os.path.exists(coverage_report_path):
            logging.error(f"[ERROR] Coverage report not found: {coverage_report_path}")
            return None, None

        with open(coverage_report_path, "r") as f:
            coverage_data = json.load(f)

        viewer_coverage = coverage_data.get("viewer-coverage", {})
        function_coverage = (
            viewer_coverage.get("function_coverage", {})
        )
        detailed_coverage = viewer_coverage.get("coverage", {})

        if not function_coverage or not detailed_coverage:
            logging.error("[ERROR] No function coverage found in report.")
            return None, None

        harness_func = None
        target_func_entry = None
        partial_funcs = []

        # Single pass through all function entries
        for file_path, funcs_in_file in function_coverage.items():
            for func_name, stats in funcs_in_file.items():
                pct = stats.get("percentage", 0.0)
                hit = stats.get("hit", 0)
                total = stats.get("total", 0)
                missed = max(total - hit, 0)

                if total == 0 or pct >= 1.0:
                    continue  # fully covered or invalid

                entry = {
                    "file": file_path,
                    "function": func_name,
                    "percentage": pct,
                    "hit": hit,
                    "total": total,
                    "missed": missed
                }

                # Priority grouping
                if harness_func is None and "harness" in func_name.lower():
                    harness_func = entry
                elif (
                    target_func_entry is None
                    and self.target_file_path.endswith(file_path or "")
                    and func_name == self.target_func
                ):
                    target_func_entry = entry
                else:
                    partial_funcs.append(entry)

        # Sort uncovered functions by missed lines (descending)
        partial_funcs.sort(key=lambda f: f["missed"], reverse=True)

        # Collect uncovered functions in priority order
        uncovered = []
        if harness_func:
            uncovered.append(harness_func)
        if target_func_entry:
            uncovered.append(target_func_entry)
        uncovered.extend(partial_funcs)

        if skip < len(uncovered):
            next_func = uncovered[skip]
            coverage_data = detailed_coverage.get(next_func["file"], {}).get(next_func["function"], {})
            logging.info(f"[INFO] Next uncovered function: {next_func['function']} in {next_func['file']} with {next_func['missed']} missed lines.")
            return next_func, coverage_data

        logging.info("[INFO] No uncovered functions remaining.")
        return None, None

    def run_make(self):
        try:
            result = subprocess.run(
                "make", shell=True, capture_output=True, text=True, cwd=self.harness_dir, timeout=150
            )
            logging.info('Stdout:\n' + result.stdout)
            logging.info('Stderr:\n' + result.stderr) 
            return {"status": Status.SUCCESS, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        except Exception as e:
            if isinstance(e, subprocess.TimeoutExpired):
                logging.error("Make command timed out.")
                if e.stdout:
                    logging.info('Partial stdout:\n' + str(e.stdout))
                if e.stderr:
                    logging.info('Partial stderr:\n' + str(e.stderr))
                return {"status": Status.TIMEOUT}
            else:
                logging.error(f"An error occurred while running make: {e}")
                return {"status": Status.FAILURE}

    def extract_function_cli_awk(self, file_path, line_coverage):
        """
        Extract function lines using awk, preserving original line numbers.
        
        Args:
            file_path (str): Path to the C source file.
            line_coverage (dict): Dictionary of line numbers (str or int) from CBMC coverage.
            
        Returns:
            list of str: Lines of the function with original line numbers prepended.
        """
        if not line_coverage:
            return "[Error Getting Source]"

        # Determine line range
        lines = [int(ln) for ln in line_coverage.keys()]
        start_line = min(lines) - 5 if min(lines) > 5 else 1  # Include some context before
        end_line = max(lines) + 5  # Include some context after

        # Build the cli command
        cmd = f"nl -ba {file_path} | sed -n '{start_line},{end_line}p'"
        
        try:
            result = self.project_container.execute(cmd)
            return result['stdout']
        except subprocess.CalledProcessError as e:
            logging.error(f"[ERROR] CLI command failed: {e}")
            return "[Error Getting Source]"


    def prepare_prompt(self, function_data, coverage_data):
        with open("prompts/coverage_debugger_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/coverage_debugger_user.prompt", "r") as f:
            user_prompt = f.read()

            # We get an uncovered code block to fix
        target_block_line = self.get_uncovered_code_block(coverage_data)

        user_prompt = user_prompt.replace("{FUNCTION_DATA}", json.dumps(function_data))
        user_prompt = user_prompt.replace("{COVERAGE_DATA}", json.dumps(coverage_data))
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)
        user_prompt = user_prompt.replace("{HARNESS_DIR}", self.harness_dir)
        user_prompt = user_prompt.replace("{TARGET_BLOCK_LINE}", str(target_block_line) if target_block_line else "N/A")

        function_source = self.extract_function_cli_awk(function_data["file"], coverage_data)
        user_prompt = user_prompt.replace("{FUNCTION_SOURCE}", function_source)

        return system_prompt, user_prompt

    def update_proof(self, updated_harness, updated_makefile):
        if updated_harness:
            harness_path = os.path.join(self.harness_dir, f"{self.target_func}_harness.c")
            backup_path = harness_path + ".bak"

            # Backup original harness if it exists
            if os.path.exists(harness_path):
                shutil.copy2(harness_path, backup_path)
                logging.info(f"Original harness backed up at {backup_path}")

            # Write updated harness
            with open(harness_path, "w") as f:
                f.write(updated_harness)
            logging.info(f"Harness updated at {harness_path}")

        if updated_makefile:
            makefile_path = os.path.join(self.harness_dir, "Makefile")
            backup_path = makefile_path + ".bak"

            # Backup original Makefile if it exists
            if os.path.exists(makefile_path):
                shutil.copy2(makefile_path, backup_path)
                logging.info(f"Original Makefile backed up at {backup_path}")

            # Write updated Makefile
            with open(makefile_path, "w") as f:
                f.write(updated_makefile)
            logging.info(f"Makefile updated at {makefile_path}")

    def reverse_proof_update(self):
        harness_path = os.path.join(self.harness_dir, f"{self.target_func}_harness.c")
        harness_backup = harness_path + ".bak"
        if os.path.exists(harness_backup):
            shutil.move(harness_backup, harness_path)
            logging.info(f"Harness reverted to original from {harness_backup}")

        makefile_path = os.path.join(self.harness_dir, "Makefile")
        makefile_backup = makefile_path + ".bak"
        if os.path.exists(makefile_backup):
            shutil.move(makefile_backup, makefile_path)
            logging.info(f"Makefile reverted to original from {makefile_backup}")

    def get_uncovered_code_block(self, coverage_data: dict[str, str]):

        current_start_line = None
        last_status = None
        current_missed_line_count = 0
        max_missed_line_count = 0
        start_line_of_max_block = ""
        last_line = list(coverage_data.keys())[-1]

        for line, status in coverage_data.items():
            if last_status != "missed" and status == "missed":
                current_start_line = line
            if status == "missed":
                current_missed_line_count += 1
            if status != "missed" or line == last_line:
                if current_missed_line_count > max_missed_line_count:
                    max_missed_line_count = current_missed_line_count
                    start_line_of_max_block = current_start_line
                current_missed_line_count = 0

            last_status = status

        return start_line_of_max_block

    def debug_coverage(self):

        # First, get the next uncovered function from the coverage report
        next_function, coverage_data = self._get_next_uncovered_function()
        if not next_function or not coverage_data:
            logging.info("[INFO] No uncovered functions found.")
            return 0  # All functions are covered

        # Create first LLM prompt
        system_prompt, default_user_prompt = self.prepare_prompt(next_function, coverage_data)
        logging.info(f'System Prompt:\n{system_prompt}')

        user_prompt = default_user_prompt

        attempts = 0    

        skip_count = 0

        # Start the debugging loop
        while next_function:

            if attempts >= self._max_attempts:
                logging.info(f"[INFO] Maximum attempts reached for function '{next_function['function']}'. Moving to next function.")
                skip_count += 1
                next_function, coverage_data = self._get_next_uncovered_function(skip=skip_count)
                if not next_function:
                    logging.info("[INFO] No more uncovered functions found.")
                    break
                # Prepare new prompt for the next function
                system_prompt, default_user_prompt = self.prepare_prompt(next_function, coverage_data)
                user_prompt = default_user_prompt

            attempts += 1

            logging.info(f'LLM Prompt:\n{user_prompt}')

            # Call LLM to fix coverage gap
            llm_response = self.llm.chat_llm(system_prompt, user_prompt, CoverageDebuggerResponse, llm_tools=self.get_tools(), call_function=self.handle_tool_calls)

            if not llm_response:
                user_prompt = "The LLM did not return a valid response. Please try again.\n" + default_user_prompt
                continue

            logging.info(f'LLM Response:\n{json.dumps(llm_response.to_dict(), indent=2)}')
            
            # Update harness and/or makefile with LLM suggestions
            self.update_proof(llm_response.updated_harness, llm_response.updated_makefile)

            # Rerun make and get updated coverage report
            make_results = self.run_make()

            # Reprompt in cases where make returns error
            if make_results.get("status", Status.ERROR) != Status.SUCCESS:
                logging.error("Make command failed to run.")
                self.reverse_proof_update()
                break

            if make_results.get("exit_code", -1) != 0:
                self.reverse_proof_update()
                user_prompt = "The provided proof harness or Makefile failed to build successfully.\n"
                user_prompt += "Here are the results from the last make command:\n"
                user_prompt += f"Exit Code: {make_results.get('exit_code', -1)}\n"
                user_prompt += f"Stdout:\n{make_results.get('stdout', '')}\n"
                user_prompt += f"Stderr:\n{make_results.get('stderr', '')}\n"
                user_prompt += "Please provide updated harness code or Makefile to fix the issue.\n"
                user_prompt += "Current User Prompt:\n" + default_user_prompt
                continue

            coverage_status = self._get_function_coverage_status(next_function["file"], next_function["function"])

            if not coverage_status:
                logging.error("[ERROR] Could not retrieve function coverage status after make.")
                self.reverse_proof_update()
                break

            # Handle case where coverage did not improve
            if coverage_status.get("percentage", 0.0) <= next_function.get("percentage", 0.0):
                logging.info(f"[INFO] Coverage for function '{next_function['function']}' did not improve or decreased.")
                user_prompt = "The coverage for the target function did not improve or decreased after the last changes.\n"
                user_prompt += "Here are the updated coverage details:\n"

                user_prompt += json.dumps(coverage_status, indent=2) + "\n"
                user_prompt += "Please analyze the situation and provide updated harness code or Makefile to improve coverage.\n"
                self.reverse_proof_update()
                continue

            # Handle case where coverage is complete
            if coverage_status.get("percentage", 0.0) >= 1.0:
                logging.info(f"[INFO] Function '{next_function['function']}' is now fully covered.")

            if coverage_status.get("percentage", 0.0) > next_function.get("percentage", 0.0):
                # Move to next uncovered function
                next_function, coverage_data = self._get_next_uncovered_function()
                if not next_function:
                    logging.info("[INFO] No more uncovered functions found.")
                    break
                # Prepare new prompt for the next function
                system_prompt, default_user_prompt = self.prepare_prompt(next_function, coverage_data)
                user_prompt = default_user_prompt
                attempts = 0  # Reset attempts for new function
                continue

            

