

import json
import os
import uuid
from typing import Optional
from enum import Enum
import logging
import shutil
import subprocess

from agent import AIAgent
from commons.models import Generable
from commons.utils import Status
from makefile.output_models import CoverageDebuggerResponse

from logger import setup_logger

logger = setup_logger(__name__)

class AgentAction(Enum):
    RETRY_BLOCK = 0      # ask LLM again
    SKIP_BLOCK = 1       # do not modify this block
    NEXT_BLOCK = 2      # success → move to next uncovered block
    TERMINATE = 3        # fatal error

class CoverageDebugger(AIAgent, Generable):

    def __init__(self, args, project_container):
        super().__init__(
            "CoverageDebugger",
            args,
            project_container,
        )
        self._max_attempts = 3
        
        # Instance attributes for proof_validator tool context
        self._current_function_entry: Optional[dict] = None
        self._target_block_line: Optional[str] = None
        self._current_coverage: dict = {}




    def _get_next_uncovered_function(self, functions_lines_to_skip: dict[str, set]):
        coverage_report_path = os.path.join(self.harness_dir, "build/report/json/viewer-coverage.json")
        if not os.path.exists(coverage_report_path):
            logger.error(f"[ERROR] Coverage report not found: {coverage_report_path}")
            return None, None, None

        with open(coverage_report_path, "r") as f:
            coverage_data = json.load(f)

        viewer_coverage = coverage_data.get("viewer-coverage", {})
        function_coverage = viewer_coverage.get("function_coverage", {})
        detailed_coverage = viewer_coverage.get("coverage", {})

        if not function_coverage or not detailed_coverage:
            logger.error("[ERROR] No function coverage found in report.")
            return None, None, None

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

                if hit == 0 or pct >= 1.0:
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
                    and func_name == self.target_function
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

        # Iterate through uncovered functions to find the next valid one
        for func_entry in uncovered:
            coverage_info = detailed_coverage.get(func_entry["file"], {}).get(func_entry["function"], {})
            logger.info(
                f"[INFO] Next uncovered function: {func_entry['function']} "
                f"in {func_entry['file']} with {func_entry['missed']} missed lines."
            )
            # Get the start line of the most uncovered code block
            start_line = self.get_uncovered_code_block(coverage_info, functions_lines_to_skip.get(func_entry["function"], set()))
            if start_line is not None:  
                return func_entry, coverage_info, start_line

        logger.info("[INFO] No uncovered functions remaining.")
        return None, None, None

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
            logger.error(f"[ERROR] CLI command failed: {e}")
            return "[Error Getting Source]"


    def prepare_prompt(self, function_data, coverage_data, target_block_line):
        with open("prompts/coverage_debugger_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/coverage_debugger_user.prompt", "r") as f:
            user_prompt = f.read()

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
            harness_path = os.path.join(self.harness_dir, f"{self.target_function}_harness.c")
            
            # Write updated harness
            with open(harness_path, "w") as f:
                f.write(updated_harness)
            logger.info(f"Harness updated at {harness_path}")

        if updated_makefile:
            makefile_path = os.path.join(self.harness_dir, "Makefile")
            
            # Write updated Makefile
            with open(makefile_path, "w") as f:
                f.write(updated_makefile)
            logger.info(f"Makefile updated at {makefile_path}")

    def reverse_proof_update(self):
        harness_path = os.path.join(self.harness_dir, f"{self.target_function}_harness.c")
        harness_backup = harness_path + ".bak"
        if os.path.exists(harness_backup):
            shutil.move(harness_backup, harness_path)
            logger.info(f"Harness reverted to original from {harness_backup}")

        makefile_path = os.path.join(self.harness_dir, "Makefile")
        makefile_backup = makefile_path + ".bak"
        if os.path.exists(makefile_backup):
            shutil.move(makefile_backup, makefile_path)
            logger.info(f"Makefile reverted to original from {makefile_backup}")

    def remove_proof_backups(self):
        harness_backup = os.path.join(self.harness_dir, f"{self.target_function}_harness.c.bak")
        if os.path.exists(harness_backup):
            os.remove(harness_backup)
            logger.info(f"Removed harness backup at {harness_backup}")

        makefile_backup = os.path.join(self.harness_dir, "Makefile.bak")
        if os.path.exists(makefile_backup):
            os.remove(makefile_backup)
            logger.info(f"Removed Makefile backup at {makefile_backup}")

    def get_uncovered_code_block(self, coverage_data: dict[str, str], skipped_blocks: set[str]):
        current_start_line = None
        last_status = None
        current_missed_line_count = 0

        # Track all missed blocks
        missed_blocks = []
        last_line = list(coverage_data.keys())[-1]

        for line, status in coverage_data.items():
            if last_status != "missed" and status == "missed":
                current_start_line = line
                current_missed_line_count = 0

            if status == "missed":
                current_missed_line_count += 1

            if status != "missed" or line == last_line:
                if current_start_line is not None and current_missed_line_count > 0:
                    # Add missed block (start, count)
                    missed_blocks.append((current_start_line, current_missed_line_count))
                current_missed_line_count = 0
                current_start_line = None

            last_status = status

        # Sort blocks by size (descending)
        missed_blocks.sort(key=lambda x: x[1], reverse=True)

        # Pick the largest missed block not containing a skipped block
        for start_line, _ in missed_blocks:
            if start_line not in skipped_blocks:
                return start_line

        # If all blocks are skipped, return None
        return None

    def compute_coverage_improvement(self, initial_coverage, final_coverage):
        if not initial_coverage or not final_coverage:
            return None

        initial_percentage = initial_coverage.get("percentage", 0.0)
        final_percentage = final_coverage.get("percentage", 0.0)

        percentage_increase = final_percentage - initial_percentage

        return percentage_increase

    def validate_llm_response(self, llm_response: Optional[CoverageDebuggerResponse], function_entry: dict, target_block_line: str, attempts: int, current_coverage: dict):

        # CASE 1 — LLM returned no valid response
        if not llm_response:
            # An error occurred. We will skip this block 
            logger.error("[ERROR] No valid response from LLM.")
            return (AgentAction.SKIP_BLOCK, None, current_coverage, "no_llm_response")

        # CASE 2 — LLM proposed no modifications
        if not llm_response.proposed_modifications and not llm_response.updated_harness and not llm_response.updated_makefile:
            logger.info(
                f"No proposed modifications provided by LLM. Marking as skipped."
            )
            return (AgentAction.SKIP_BLOCK, None, current_coverage, "no_modifications")

        # Attempt to apply fix
        self.update_proof(llm_response.updated_harness, llm_response.updated_makefile)

        make_results = self.run_make()

        # CASE 3 — Make failed entirely
        if make_results.get("status", Status.ERROR) in [Status.ERROR]:
            logger.error("Make command failed to run.")
            return (AgentAction.SKIP_BLOCK, None, current_coverage, "make_invocation_failed")

        if make_results.get("status", Status.ERROR) == Status.TIMEOUT:
            logger.error("Make command timed out.")
            return (AgentAction.SKIP_BLOCK, None, current_coverage, "make_timeout")

        # CASE 4 — Build failed (exit code != 0)
        if make_results.get("status", Status.ERROR) == Status.FAILURE:
            logger.error("[ERROR] Build failed after applying LLM proposed modifications.")
            user_prompt = (
                "The provided proof harness or Makefile failed to build successfully.\n"
                f"Exit Code: {make_results.get('exit_code', -1)}\n"
                f"Stdout:\n{make_results.get('stdout', '')}\n"
                f"Stderr:\n{make_results.get('stderr', '')}\n"
                "Please provide updated harness code or Makefile to fix the issue.\n"
            )
            return (AgentAction.RETRY_BLOCK, user_prompt, current_coverage, "build_failed")

        # CASE 4b — Build succeeded but CBMC reported invalid --unwindset loop IDs
        invalid_loop_ids = self.get_invalid_unwindset_loop_ids()
        if invalid_loop_ids:
            logger.warning(
                f"[WARN] Invalid --unwindset loop IDs detected: {invalid_loop_ids}. Prompting LLM to fix."
            )
            loop_json_path = self.get_loop_json_path()
            user_prompt = (
                "The updated Makefile contains one or more invalid loop IDs in the --unwindset flag.\n"
                "CBMC reported the following loop IDs as non-existent:\n"
                + "\n".join(f"  - {lid}" for lid in invalid_loop_ids)
                + "\n\n"
                "These loop IDs do not correspond to any loop in the CBMC goto program, "
                "so the unwind limits have no effect.\n\n"
                "To find the correct loop IDs, read the file:\n"
                f"  {loop_json_path}\n\n"
                "It contains a 'loops' dictionary whose keys are the valid loop IDs "
                "(e.g., 'strcpy.0', not 'strcpy.unwind.0').\n"
                "Please update the Makefile to use only valid loop IDs in --unwindset."
            )
            return (AgentAction.RETRY_BLOCK, user_prompt, current_coverage, "invalid_unwindset_loop_ids")


        coverage_status = self._get_function_coverage_status(function_entry["file"], function_entry["function"])

        # CASE 5 — Target function unreachable now
        if not coverage_status:
            logger.error("[ERROR] Function coverage status not found.")
            user_prompt = (
                "The target function is no longer reached by the updated harness and was reverted.\n"
                "Please fix so target function is reached.\n"
            )
            return (AgentAction.RETRY_BLOCK, user_prompt, current_coverage, "function_unreachable")

        # ✅ CASE — Success: block covered!
        if coverage_status.get(target_block_line) != "missed":
            # First, we validate the fix by checking that the overall coverage also increased
            new_coverage = self.get_overall_coverage()
            if (new_coverage.get("hit", 0.0) <= current_coverage.get("hit", 0.0) or 
                new_coverage.get("total", 0.0) < current_coverage.get("total", 0.0)):
                logger.info(
                    "[INFO] Target block covered but overall reachable code or coverage decreased."
                )
                user_prompt = (
                    "The proposed modification covered the target block but decreased the overall reachable and hit code.\n"
                    f"initial coverage: {json.dumps(current_coverage, indent=2)}\n"
                    f"new coverage: {json.dumps(new_coverage, indent=2)}\n"
                    "Your changes have been reverted." 
                    "Investigate and determine why the change led to decreased coverage.\n"
                    "If it cannot be avoided, do not propose any modification."
                )
                return (AgentAction.RETRY_BLOCK, user_prompt, current_coverage, "overall_coverage_decreased")

            # Else, the fix is valid and should be accepted
            logger.info(f"[INFO] Target block on line {target_block_line} successfully covered.")
            return (AgentAction.NEXT_BLOCK, None, new_coverage, None)

        # CASE 6 — Max attempts exhausted
        if attempts >= self._max_attempts:
            logger.error(f"[INFO] Maximum attempts reached for '{function_entry['function']}'.")
            return (AgentAction.SKIP_BLOCK, None, current_coverage, "max_attempts_reached")

        # CASE 7 — Coverage did not improve
        logger.info(
            f"[INFO] The target block on line {target_block_line} is still not covered. "
            "Reverting changes."
        )
        user_prompt = (
            f"The target block on line {target_block_line} is still not covered.\n"
            "Here is the current coverage status of the function:\n"
            f"{json.dumps(coverage_status, indent=2)}\n"
            "Your proposed changes have been reverted. Please update harness or Makefile to cover the target block line.\n"
        )
        return (AgentAction.RETRY_BLOCK, user_prompt, current_coverage, "block_not_covered")



    def get_coverage_tools(self):
        """Return standard coverage tools plus the proof_validator tool."""
        
        # Get the standard coverage tools from the base class
        base_coverage_tools = super().get_coverage_tools()
        
        # Define the proof_validator tool
        proof_validator_tool = {
            "type": "function",
            "name": "proof_validator",
            "description": (
                "Update the proof harness (and optionally the Makefile) with the provided content, "
                "then run verification to test if the fix resolves the coverage gap. "
                "When compile_only is true, only compilation is checked (quick syntax validation). "
                "When compile_only is false, full verification is run and the result includes "
                "coverage block reachability and overall coverage checks."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "The reason for running this validation"
                    },
                    "harness_content": {
                        "type": "string",
                        "description": "The complete updated harness file content"
                    },
                    "makefile_content": {
                        "type": ["string", "null"],
                        "description": "The complete updated Makefile content, or null if no Makefile changes are needed"
                    },
                    "compile_only": {
                        "type": "boolean",
                        "description": "If true, only run compilation to check syntax validity. If false, run full verification."
                    }
                },
                "required": ["reason", "harness_content", "makefile_content", "compile_only"],
                "additionalProperties": False
            }
        }
        
        # Return both sets of tools
        return [*base_coverage_tools, proof_validator_tool]

    def handle_proof_validator(self, harness_content: str, makefile_content: Optional[str], compile_only: bool) -> dict:
        """
        Write harness (and optionally Makefile) to disk, run make, and return
        structured validation results.
        """
        self.update_proof(harness_content, makefile_content)
        
        logger.info("Harness%s updated via proof_validator tool.", " and Makefile" if makefile_content else "")

        make_results = self.run_make(compile_only=compile_only)

        status_code = make_results.get("status", Status.ERROR)
        exit_code = make_results.get("exit_code", -1)
        timed_out = status_code == Status.TIMEOUT

        compilation_info = {
            "success": exit_code == 0 and status_code == Status.SUCCESS,
            "exit_code": exit_code,
            "stderr": make_results.get("stderr", ""),
        }

        # Include stdout only if compilation failed
        if not compilation_info["success"]:
            compilation_info["stdout"] = make_results.get("stdout", "")

        result = {
            "compilation": compilation_info,
            "timed_out": timed_out,
            "error_covered": None,
            "coverage_maintained": None,
            "properties_maintained": None, # Coverage doesn't check properties but keep structure consistent
            "error_resolved": None,
            "message": "",
        }

        # If compile-only mode, or compilation failed, or timed out, return early
        if compile_only:
            if compilation_info["success"]:
                result["message"] = "Compilation succeeded (compile-only mode)."
            else:
                result["message"] = "Compilation failed. Review the errors above."
            return result

        if timed_out:
            result["message"] = (
                "Verification timed out. If the timeout cannot be avoided by updating the "
                "harness (e.g., the target function has inherently long verification time), "
                "you may give up and return an analysis explaining why."
            )
            return result

        if not compilation_info["success"]:
            result["message"] = (
                "Compilation/verification failed. Review the errors and verification logs (build/reports/cbmc.xml, build/reports/coverage.xml) to understand the reason."
                "If the error cannot be resolved, you may give up and return an analysis explaining why."
            )
            return result

        # Full verification passed — run validation checks
        messages = []

        # Check loop IDs if verification passed compilation
        invalid_loop_ids = self.get_invalid_unwindset_loop_ids()
        if invalid_loop_ids:
             messages.append(
                f"The updated Makefile contains one or more invalid loop IDs in the --unwindset flag: {invalid_loop_ids}. "
                "These loop IDs do not correspond to any loop in the CBMC goto program, "
                "so the unwind limits have no effect. "
                f"To find the correct loop IDs, read the viewer-loop.json file."
                "Please update the Makefile to use only valid loop IDs in --unwindset."
             )
             result["message"] = " | ".join(messages)
             return result

        # Check block coverage
        if self._current_function_entry and self._target_block_line:
            coverage_status = self._get_function_coverage_status(self._current_function_entry["file"], self._current_function_entry["function"])
            
            if not coverage_status:
                messages.append("ERROR: The target function is no longer reached by the updated harness.")
                result['error_resolved'] = False
            else:
                block_covered = coverage_status.get(self._target_block_line) != "missed"
                result["error_resolved"] = block_covered
                if not block_covered:
                    messages.append(
                        f"ERROR: The target block on line {self._target_block_line} is still not covered. "
                    )

        # Check overall coverage regression
        if self._current_coverage:
            new_coverage = self.get_overall_coverage()
            cov_hit_ok = new_coverage.get("hit", 0.0) >= self._current_coverage.get("hit", 0.0)
            cov_total_ok = new_coverage.get("total", 0.0) >= self._current_coverage.get("total", 0.0)
            result["coverage_maintained"] = cov_hit_ok and cov_total_ok
            if not result["coverage_maintained"]:
                messages.append(
                    f"ERROR: Overall coverage decreased. "
                    f"Previous: {self._current_coverage}, Current: {new_coverage}. "
                    "Your fix likely added constraints that prevent previously covered lines from being reached."
                )

        if not messages:
            result["message"] = "All checks passed. The coverage gap is resolved and all validation criteria are met."
        else:
            result["message"] = " | ".join(messages)

        return result

    def handle_tool_calls(self, tool_name, function_args):
        """Handle tool calls, delegating to parent for standard tools, and handling proof_validator locally."""
        if tool_name == "proof_validator":
            logging_text = f"""
            Function call: 
            Name: {tool_name} 
            Args: {function_args}
            """
            logger.info(logging_text)   
            
            function_args_parsed = json.loads(function_args)
            harness_content = function_args_parsed.get("harness_content", "")
            makefile_content = function_args_parsed.get("makefile_content", None)
            compile_only = function_args_parsed.get("compile_only", False)
            
            tool_response = self.handle_proof_validator(harness_content, makefile_content, compile_only)
            logger.info(f"Function call response:\\n {tool_response}")
            return str(tool_response)
        
        # Fall back to parent handler for run_bash_command, etc.
        return super().handle_tool_calls(tool_name, function_args)

    def generate(self) -> bool:

        functions_to_skip = {}

        make_results = self.run_make()

        if (make_results.get("status", Status.ERROR) != Status.SUCCESS or 
            make_results.get("exit_code", -1) != 0 or not self.validate_verification_report()):
            logger.error("Make command failed to run.")
            self.log_agent_result({"initial_coverage": None, "final_coverage": None})
            return False

        # Get and log initial coverage
        initial_coverage = self.get_overall_coverage()
        if initial_coverage:
            logger.info(f"[INFO] Initial Overall Coverage: {json.dumps(initial_coverage, indent=2)}")

        current_coverage = initial_coverage

        # First, get the next uncovered function from the coverage report
        next_function, coverage_data, target_block_line = self._get_next_uncovered_function(functions_to_skip)
        if not next_function or not coverage_data or not target_block_line:
            logger.info("[INFO] No uncovered functions found.")
            self.log_agent_result({"initial_coverage": initial_coverage, "final_coverage": initial_coverage})
            return True

        # Create first LLM prompt
        system_prompt, user_prompt = self.prepare_prompt(next_function, coverage_data, target_block_line)
        logger.info(f'System Prompt:\\n{system_prompt}')

        attempts = 0    
        get_next_block = False
        conversation = []
        tag = ""

        # Start the debugging loop
        while user_prompt:
            attempts += 1

            if attempts == 1:
                tag = uuid.uuid4().hex[:4].upper()
                self.create_backup(tag)
            
            task_id = f"cov-{next_function['function']}-{target_block_line}"
            logger.info(f"[INFO] Processing task '{task_id}', attempt {attempts}.")

            # Update context for the proof validator
            self._current_function_entry = next_function
            self._target_block_line = target_block_line
            self._current_coverage = current_coverage

            llm_response, chat_data = self.llm.chat_llm(
                system_prompt, user_prompt, CoverageDebuggerResponse,
                llm_tools=self.get_coverage_tools(),
                call_function=self.handle_tool_calls,
                conversation_history=conversation
            )

            # Cast the response to the expected type
            from typing import cast
            typed_response = cast(Optional[CoverageDebuggerResponse], llm_response)

            llm_result, user_prompt, current_coverage, error_tag = self.validate_llm_response(
                                                                        typed_response, 
                                                                        next_function, 
                                                                        target_block_line, 
                                                                        attempts, 
                                                                        current_coverage)
            self.log_task_attempt(task_id, attempts, chat_data, error=error_tag)

            if llm_result == AgentAction.RETRY_BLOCK and attempts < self._max_attempts: 
                self.restore_backup(tag)
                prompt_prefix = f"""
                Your previous attempt to fix the coverage issue did not succeed.
                You should first analyze the error message below and determine why it was not accepted.
                In your analysis, explain why the proposed modification failed and how your current proposal is different and will address the issue.
                \\n\\n
                """
                if user_prompt:
                    user_prompt = prompt_prefix + user_prompt
            elif llm_result == AgentAction.SKIP_BLOCK or attempts >= self._max_attempts:
                self.restore_backup(tag)
                self.discard_backup(tag)
                functions_to_skip.setdefault(next_function['function'], set()).add(target_block_line)
                self.log_task_result(task_id, False, attempts)
                get_next_block = True
            elif llm_result == AgentAction.NEXT_BLOCK:
                self.discard_backup(tag)
                get_next_block = True
                functions_to_skip.setdefault(next_function['function'], set()).add(target_block_line)
                self.log_task_result(task_id, True, attempts)
            elif llm_result == AgentAction.TERMINATE:
                self.restore_backup(tag)
                self.discard_backup(tag)
                self.log_task_result(task_id, False, attempts)
                break

            if get_next_block:
                overall_coverage = self.get_overall_coverage()
                if overall_coverage:
                    logger.info(f"[INFO] Overall Coverage: {json.dumps(overall_coverage, indent=2)}")
                next_function, coverage_data, target_block_line = self._get_next_uncovered_function(functions_to_skip)
                if not next_function or not coverage_data or not target_block_line:
                    logger.info("[INFO] No more uncovered functions found.")
                    break
                get_next_block = False
                attempts = 0
                conversation = []
                system_prompt, user_prompt = self.prepare_prompt(next_function, coverage_data, target_block_line)

        # Final coverage report
        final_coverage = self.get_overall_coverage()
        if final_coverage:
            logger.info(f"[INFO] Final Overall Coverage: {json.dumps(final_coverage, indent=2)}")

        # Compute coverage improvement
        logger.info(
            "[INFO] Coverage changed from {:.2f}% to {:.2f}%.".format(
                initial_coverage.get("percentage", 0.0) * 100,
                final_coverage.get("percentage", 0.0) * 100
            )
        )
        self.log_agent_result({"initial_coverage": initial_coverage, "final_coverage": final_coverage})
        self.save_status('coverage')
        
        # Reset tracker attributes
        self._current_function_entry = None
        self._target_block_line = None
        self._current_coverage = {}
        
        return True

