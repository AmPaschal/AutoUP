"""
Vuln Aware Refiner Agent

This agent analyzes loop unwinding failures from CBMC reports and uses an LLM
to determine appropriate loop unwinding limits based on vulnerability patterns.
"""

import logging
import json
import os
import re
import shutil
import uuid
from enum import Enum

from agent import AIAgent
from commons.models import Generable
from makefile.output_models import VulnAwareRefinerResponse
from commons.utils import Status

logger = logging.getLogger(__name__)


class AgentAction(Enum):
    RETRY = 0       # Ask LLM again
    SKIP = 1        # Skip processing
    SUCCESS = 2     # Successfully updated
    TERMINATE = 3   # Fatal error


class VulnAwareRefiner(AIAgent, Generable):
    """
    Agent that refines loop unwinding limits based on vulnerability analysis.
    
    Workflow:
    1. Parse viewer-result.json for unwind failures
    2. Get loop details from viewer-loop.json
    3. Extract source code for each failed loop
    4. Use LLM to analyze loops for iteration-dependent memory operations
    5. LLM returns updated Makefile with appropriate --unwindset flags
    6. Validate coverage doesn't decrease
    """

    def __init__(self, args, project_container):
        super().__init__(
            "VulnAwareRefiner",
            args,
            project_container,
        )
        self._max_attempts = 3

    def get_overall_coverage(self) -> dict:
        """Get the overall coverage from the coverage report."""
        coverage_report_path = os.path.join(
            self.harness_dir, "build/report/json/viewer-coverage.json"
        )
        if not os.path.exists(coverage_report_path):
            logger.error(f"[ERROR] Coverage report not found: {coverage_report_path}")
            return {}

        with open(coverage_report_path, "r") as f:
            coverage_data = json.load(f)

        viewer_coverage = coverage_data.get("viewer-coverage", {})
        overall_coverage = viewer_coverage.get("overall_coverage", {})

        return overall_coverage

    def get_loops_with_unwind_failures(self) -> list[str]:
        """
        Parse viewer-result.json to find loop unwind failures.
        
        Returns:
            List of unwind failure identifiers (e.g., "le_ecred_conn_req.unwind.9")
        """
        result_path = os.path.join(
            self.harness_dir, "build/report/json/viewer-result.json"
        )
        
        if not os.path.exists(result_path):
            logger.error(f"[ERROR] Result file not found: {result_path}")
            return []
        
        with open(result_path, "r") as f:
            result_data = json.load(f)
        
        viewer_result = result_data.get("viewer-result", {})
        results = viewer_result.get("results", {})
        false_results = results.get("false", [])
        
        # Filter for unwind failures (pattern: <function>.unwind.<N>)
        unwind_pattern = re.compile(r'^(.+)\.unwind\.(\d+)$')
        unwind_failures = [r for r in false_results if unwind_pattern.match(r)]
        
        logger.info(f"[INFO] Found {len(unwind_failures)} loop unwind failures")
        return unwind_failures

    def get_loop_details(self, unwind_failures: list[str]) -> dict:
        """
        Get loop details from viewer-loop.json for the given unwind failures.
        
        Note: Loop IDs in viewer-loop.json do NOT contain "unwind".
        Mapping: <function>.unwind.<N> -> <function>.<N>
        
        Args:
            unwind_failures: List of unwind failure identifiers
            
        Returns:
            Dict mapping unwind failure ID to loop details (file, function, line)
        """
        loop_path = os.path.join(
            self.harness_dir, "build/report/json/viewer-loop.json"
        )
        
        if not os.path.exists(loop_path):
            logger.error(f"[ERROR] Loop file not found: {loop_path}")
            return {}
        
        with open(loop_path, "r") as f:
            loop_data = json.load(f)
        
        viewer_loop = loop_data.get("viewer-loop", {})
        loops = viewer_loop.get("loops", {})
        
        loop_details = {}
        
        for failure_id in unwind_failures:
            # Convert unwind failure ID to loop ID
            # e.g., "le_ecred_conn_req.unwind.9" -> "le_ecred_conn_req.9"
            loop_id = failure_id.replace(".unwind.", ".")
            
            if loop_id in loops:
                loop_details[failure_id] = {
                    "loop_id": loop_id,
                    **loops[loop_id]
                }
                logger.info(
                    f"[INFO] Loop {loop_id}: {loops[loop_id]['file']}:"
                    f"{loops[loop_id]['line']} in {loops[loop_id]['function']}"
                )
            else:
                logger.warning(f"[WARNING] Loop details not found for: {loop_id}")
        
        return loop_details

    def extract_loop_source_code(self, file_path: str, line_number: int, context_lines: int = 30) -> str:
        """
        Extract source code around the loop.
        
        Args:
            file_path: Path to the source file (relative to project root)
            line_number: Line number of the loop
            context_lines: Number of lines to include before and after
            
        Returns:
            Source code with line numbers
        """
        # Build the full path
        full_path = os.path.join(self.root_dir, file_path)
        
        if not os.path.exists(full_path):
            logger.error(f"[ERROR] Source file not found: {full_path}")
            return f"[Error: File not found: {full_path}]"
        
        start_line = max(1, line_number - context_lines)
        end_line = line_number + context_lines
        
        # Use nl and sed to extract lines with line numbers
        cmd = f"nl -ba {full_path} | sed -n '{start_line},{end_line}p'"
        
        try:
            result = self.project_container.execute(cmd)
            return result.get('stdout', '[Error executing command]')
        except Exception as e:
            logger.error(f"[ERROR] Failed to extract source: {e}")
            return f"[Error extracting source: {e}]"

    def prepare_prompt(self, loop_details: dict) -> tuple[str, str]:
        """
        Prepare the LLM prompt for vulnerability analysis.
        
        Args:
            loop_details: Dict mapping failure IDs to loop details
            
        Returns:
            Tuple of (system_prompt, user_prompt)
        """
        with open("prompts/vuln_aware_refiner_system.prompt", "r") as f:
            system_prompt = f.read()

        with open("prompts/vuln_aware_refiner_user.prompt", "r") as f:
            user_prompt = f.read()

        # Build loop information string
        loops_info = []
        loop_sources = []
        
        for failure_id, details in loop_details.items():
            loop_info = {
                "failure_id": failure_id,
                "loop_id": details.get("loop_id"),
                "file": details.get("file"),
                "function": details.get("function"),
                "line": details.get("line")
            }
            loops_info.append(loop_info)
            
            # Extract source code for this loop
            source = self.extract_loop_source_code(
                details.get("file"),
                details.get("line")
            )
            loop_sources.append({
                "loop_id": details.get("loop_id"),
                "file": details.get("file"),
                "line": details.get("line"),
                "source": source
            })

        # Get current Makefile and harness
        current_makefile = self.get_makefile()
        current_harness = self.get_harness()

        # Replace placeholders
        user_prompt = user_prompt.replace("{LOOPS_WITH_FAILURES}", json.dumps(loops_info, indent=2))
        user_prompt = user_prompt.replace("{LOOP_SOURCES}", json.dumps(loop_sources, indent=2))
        user_prompt = user_prompt.replace("{CURRENT_MAKEFILE}", current_makefile)
        user_prompt = user_prompt.replace("{CURRENT_HARNESS}", current_harness)
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)
        user_prompt = user_prompt.replace("{HARNESS_DIR}", self.harness_dir)

        return system_prompt, user_prompt

    def run_make(self) -> dict:
        """Run make command and return results."""
        logger.info("[INFO] Running make command...")
        make_results = self.execute_command("make -j4", workdir=self.harness_dir, timeout=1500)
        logger.info('Stdout:\n' + make_results.get('stdout', ''))
        logger.info('Stderr:\n' + make_results.get('stderr', ''))
        return make_results

    def validate_llm_response(
        self,
        llm_response: VulnAwareRefinerResponse | None,
        attempts: int,
        initial_coverage: dict
    ) -> tuple[AgentAction, str | None, dict, str | None]:
        """
        Validate the LLM response and apply changes.
        
        Returns:
            Tuple of (action, retry_prompt, current_coverage, error_tag)
        """
        # CASE 1 — LLM returned no valid response
        if not llm_response:
            logger.error("[ERROR] No valid response from LLM.")
            return (AgentAction.SKIP, None, initial_coverage, "no_llm_response")

        # CASE 2 — LLM provided no Makefile update
        if not llm_response.updated_makefile:
            logger.info("[INFO] No Makefile update provided by LLM.")
            return (AgentAction.SKIP, None, initial_coverage, "no_makefile_update")

        # Apply the Makefile update
        self.update_makefile(llm_response.updated_makefile)
        logger.info("[INFO] Makefile updated with new unwindset values.")

        # Run make to verify the changes
        make_results = self.run_make()

        # CASE 3 — Make failed entirely
        if make_results.get("status", Status.ERROR) == Status.ERROR:
            logger.error("[ERROR] Make command failed to run.")
            return (AgentAction.SKIP, None, initial_coverage, "make_invocation_failed")

        if make_results.get("status", Status.ERROR) == Status.TIMEOUT:
            logger.error("[ERROR] Make command timed out.")
            return (AgentAction.SKIP, None, initial_coverage, "make_timeout")

        # CASE 4 — Build failed
        if make_results.get("status", Status.ERROR) == Status.FAILURE:
            logger.error("[ERROR] Build failed after applying LLM changes.")
            user_prompt = (
                "The updated Makefile failed to build successfully.\n"
                f"Exit Code: {make_results.get('exit_code', -1)}\n"
                f"Stdout:\n{make_results.get('stdout', '')}\n"
                f"Stderr:\n{make_results.get('stderr', '')}\n"
                "Please provide a corrected Makefile.\n"
            )
            return (AgentAction.RETRY, user_prompt, initial_coverage, "build_failed")

        # Check coverage
        new_coverage = self.get_overall_coverage()
        
        # CASE 5 — Coverage decreased
        if new_coverage.get("hit", 0) < initial_coverage.get("hit", 0):
            logger.warning("[WARNING] Coverage decreased after Makefile update.")
            user_prompt = (
                "The updated Makefile caused coverage to decrease.\n"
                f"Initial coverage: {initial_coverage.get('hit', 0)} lines\n"
                f"New coverage: {new_coverage.get('hit', 0)} lines\n"
                "Please provide a Makefile that maintains or improves coverage.\n"
            )
            return (AgentAction.RETRY, user_prompt, initial_coverage, "coverage_decreased")

        # CASE 6 — Max attempts reached
        if attempts >= self._max_attempts:
            logger.error(f"[ERROR] Maximum attempts ({self._max_attempts}) reached.")
            return (AgentAction.SKIP, None, new_coverage, "max_attempts_reached")

        # SUCCESS
        logger.info("[INFO] Makefile updated successfully. Coverage maintained.")
        return (AgentAction.SUCCESS, None, new_coverage, None)

    def generate(self) -> bool:
        """
        Main entry point for the agent.
        
        Returns:
            True if successful, False otherwise
        """
        # Run initial make to get baseline coverage
        make_results = self.run_make()

        make_status = make_results.get('status', Status.ERROR)

        if make_status == Status.ERROR or make_status == Status.FAILURE:
            logger.error("Initial make command failed; cannot proceed with function pointer stub generation.")
            self.log_agent_result({"stubs_to_generate": None})
            return False
        elif make_status == Status.SUCCESS:
            success_status = True
        else:
            success_status = False


        if make_results.get("status", Status.ERROR) != Status.SUCCESS:
            logger.error("[ERROR] Initial make command failed.")
            self.log_agent_result({"initial_coverage": None, "final_coverage": None})
            return False

        initial_coverage = self.get_overall_coverage()
        logger.info(f"[INFO] Initial coverage: {json.dumps(initial_coverage, indent=2)}")

        # Get loops with unwind failures
        unwind_failures = self.get_loops_with_unwind_failures()
        
        if not unwind_failures:
            logger.info("[INFO] No loop unwind failures found.")
            self.log_agent_result({
                "initial_coverage": initial_coverage,
                "final_coverage": initial_coverage,
                "loops_analyzed": 0
            })
            return True

        # Get details for each failed loop
        loop_details = self.get_loop_details(unwind_failures)
        
        if not loop_details:
            logger.warning("[WARNING] Could not get details for any loops.")
            self.log_agent_result({
                "initial_coverage": initial_coverage,
                "final_coverage": initial_coverage,
                "loops_analyzed": 0
            })
            return True

        # Prepare prompt and call LLM
        system_prompt, user_prompt = self.prepare_prompt(loop_details)
        logger.info(f"[INFO] System prompt:\n{system_prompt}")

        attempts = 0
        tag = uuid.uuid4().hex[:4].upper()
        self.create_backup(tag)
        
        conversation = []
        current_coverage = initial_coverage
        task_id = f"vuln-refine-{len(loop_details)}-loops"

        agent_result = {
            "num_loops_analyzed": len(loop_details),
            "num_loops_increased": 0
        }

        while user_prompt and attempts < self._max_attempts:
            attempts += 1
            logger.info(f"[INFO] Attempt {attempts} for task {task_id}")

            llm_response, chat_data = self.llm.chat_llm(
                system_prompt,
                user_prompt,
                VulnAwareRefinerResponse,
                llm_tools=self.get_tools(),
                call_function=self.handle_tool_calls,
                conversation_history=conversation
            )

            action, retry_prompt, current_coverage, error_tag = self.validate_llm_response(
                llm_response,
                attempts,
                current_coverage
            )

            self.log_task_attempt(task_id, attempts, chat_data, error=error_tag)

            if action == AgentAction.RETRY and attempts < self._max_attempts:
                self.restore_backup(tag)
                user_prompt = retry_prompt
            elif action == AgentAction.SKIP or attempts >= self._max_attempts:
                self.restore_backup(tag)
                self.discard_backup(tag)
                self.log_task_result(task_id, False, attempts)
                break
            elif action == AgentAction.SUCCESS:
                self.discard_backup(tag)
                self.log_task_result(task_id, True, attempts)
                user_prompt = None
                success_status = True
                agent_result["num_loops_increased"] += llm_response.num_new_loop_unwindings
            elif action == AgentAction.TERMINATE:
                self.restore_backup(tag)
                self.discard_backup(tag)
                self.log_task_result(task_id, False, attempts)
                break

        # Final coverage report
        final_coverage = self.get_overall_coverage()
        logger.info(f"[INFO] Final coverage: {json.dumps(final_coverage, indent=2)}")

        # Log results
        self.log_agent_result(agent_result)

        self.save_status('vuln_refiner')
        return success_status
