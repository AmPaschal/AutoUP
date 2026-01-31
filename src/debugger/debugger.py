""" Debugger class"""

# System
from typing import Optional
from pathlib import Path
import subprocess
import os

# Utils
from datetime import datetime
import json
import uuid

# AutoUp
from agent import AIAgent
from commons.models import GPT, Generable
from debugger.output_models import ModelOutput
from logger import setup_logger
from commons.utils import Status
from debugger.dereference_handler import DerefereneErrorHandler

# OLD
from debugger.error_report import ErrorReport, CBMCError
from debugger.parser import extract_errors_and_payload, get_json_errors
from debugger.advice import get_advice_for_cluster
from makefile.makefile_debugger import MakefileDebugger
from validator.precondition_validator import PreconditionValidator

logger = setup_logger(__name__)


class ProofDebugger(AIAgent, Generable):
    """Agentic Proof Debugger"""

    def __init__(self, args, project_container):
        super().__init__(
            agent_name="debugger",
            args=args,
            project_container=project_container,
        )
        self.programmatic_handler = DerefereneErrorHandler(
            root_dir=self.root_dir,
            harness_path=self.harness_dir,
            harness_file_path=self.harness_file_path
        )
        self.validator = PreconditionValidator(args=self.args, project_container=self.project_container)
        
        self.__max_attempts = 3

    def generate(self) -> bool:
        """Iterates over errors"""
        make_result = self.run_make()
        if make_result["status"] != Status.SUCCESS:
            logger.error("Initial proof does not build successfully.")
            self.log_agent_result({
                "initial_errors": None,
                "final_errors": None,
                "errors_solved": None,
                "errors_solved_programatically": None,
                "final_coverage": None,
            })
            return False
        current_coverage = self.get_overall_coverage()
        if current_coverage:
            logger.info(f"[INFO] Initial Overall Coverage: {json.dumps(current_coverage, indent=2)}")

        error_clusters = extract_errors_and_payload(self.harness_file_name, self.harness_file_path)
        error_report = ErrorReport(
            error_clusters
        )
        initial_errors = len(error_report.errors_by_line)
        logger.info("Unresolved Errors: %i", initial_errors)
        errors_to_skip = set()
        total_errors_solved = 0
        errors_solved_programatically = 0
        error = self.__pop_error(error_report, errors_to_skip)
        while error is not None:
            logger.info("Target Error: %s", error)
            tag = uuid.uuid4().hex[:4].upper()
            self.create_backup(tag)
            result = None
            # First, we try to fix the error programmatically
            if error.cluster == "deref_null":
                result = self.generate_fix_programmatically(error, current_coverage)
            # If not successful, we use the LLM to fix it
            if result:
                total_errors_solved += 1
                errors_solved_programatically += 1
            else:
                self.restore_backup(tag)
                result, current_coverage = self.generate_fix_with_llm(error, current_coverage, tag)
                if result: # LLM fix succeeded
                    total_errors_solved += 1
                else:
                    self.restore_backup(tag)
            
            errors_to_skip.add(error.error_id)
            self.discard_backup(tag)
            error_clusters = extract_errors_and_payload(self.harness_file_name, self.harness_file_path)
            error_report = ErrorReport(
                error_clusters
            )
            logger.info("Unresolved Errors: %i", len(error_report.errors_by_line))
            error = self.__pop_error(error_report, errors_to_skip)
        current_coverage = self.get_overall_coverage()
        logger.info(f"[INFO] Final Overall Coverage: {json.dumps(current_coverage, indent=2)}")
        final_errors = len(error_report.errors_by_line)
        self.log_agent_result({
            "initial_errors": initial_errors,
            "final_errors": final_errors,
            "errors_solved": total_errors_solved,
            "errors_solved_programatically": errors_solved_programatically,
            "debugger_final_coverage": self.get_overall_coverage(),
        })
        self.validator.complete_validation()
        self.save_status('debugger')
        return True

    def get_overall_coverage(self):
        coverage_report_path = os.path.join(self.harness_dir, "build/report/json/viewer-coverage.json")
        if not os.path.exists(coverage_report_path):
            logger.error(f"[ERROR] Coverage report not found: {coverage_report_path}")
            return {}

        with open(coverage_report_path, "r") as f:
            coverage_data = json.load(f)

        viewer_coverage = coverage_data.get("viewer-coverage", {})
        overall_coverage = viewer_coverage.get("overall_coverage", {})

        return overall_coverage

    def get_property_count(self, property_file_path: str = None) -> int:
        """Get the number of memory safety properties from viewer-property.json.
        
        Args:
            property_file_path: Optional path to property file. If None, uses default location.
            
        Returns:
            Number of properties in the file, or -1 if file not found/error.
        """
        if property_file_path is None:
            property_file_path = os.path.join(self.harness_dir, "build/report/json/viewer-property.json")
        
        if not os.path.exists(property_file_path):
            logger.error(f"[ERROR] Property report not found: {property_file_path}")
            return -1

        try:
            with open(property_file_path, "r") as f:
                property_data = json.load(f)
            
            properties = property_data.get("viewer-property", {}).get("properties", {})
            return len(properties)
        except Exception as e:
            logger.error(f"[ERROR] Failed to read property file: {e}")
            return -1

    def get_properties_diff(self, tag: str) -> tuple[list[str], str]:
        """Compare current properties with backed-up properties to find removed ones.
        
        Args:
            tag: The backup tag used when create_backup was called.
            
        Returns:
            Tuple of (list of removed property IDs, diff output string)
        """
        current_property_path = os.path.join(self.harness_dir, "build/report/json/viewer-property.json")
        backup_property_path = os.path.join(self.harness_dir, f"build_backup.{tag}/report/json/viewer-property.json")
        
        removed_properties = []
        diff_output = ""
        
        if not os.path.exists(backup_property_path):
            logger.error(f"[ERROR] Backup property file not found: {backup_property_path}")
            return removed_properties, diff_output
        
        if not os.path.exists(current_property_path):
            logger.error(f"[ERROR] Current property file not found: {current_property_path}")
            return removed_properties, diff_output
        
        # Use diff command to compare property files
        diff_command = f"diff {backup_property_path} {current_property_path}"
        diff_result = self.execute_command(diff_command, workdir=self.harness_dir, timeout=60)
        
        logger.info(f"Diff stdout:\n {diff_result.get('stdout', '')}")
        logger.info(f"Diff stderr:\n {diff_result.get('stderr', '')}")
        
        diff_output = diff_result.get("stdout", "")
        
        # Extract removed properties from the backup file that aren't in current
        # Lines starting with "< " in diff output indicate content removed from backup
        try:
            with open(backup_property_path, "r") as f:
                backup_data = json.load(f)
            with open(current_property_path, "r") as f:
                current_data = json.load(f)
            
            backup_properties = set(backup_data.get("viewer-property", {}).get("properties", {}).keys())
            current_properties = set(current_data.get("viewer-property", {}).get("properties", {}).keys())
            
            removed_properties = list(backup_properties - current_properties)
        except Exception as e:
            logger.error(f"[ERROR] Failed to extract removed properties: {e}")
        
        return removed_properties, diff_output
    
    def generate_fix_programmatically(self, error: CBMCError, current_coverage: dict) -> bool:
            
        """Generate the fix of a given error using programmatic handler"""
        updated_harness = self.programmatic_handler.analyze(error)
        if not updated_harness:
            logger.error("Programmatic handler could not analyze the error.")
            return False
        self.__update_harness(updated_harness)
        make_result = self.run_make()
        if make_result.get("status") != Status.SUCCESS:
            logger.error("Make command failed after programmatic fix.")
            return False
        if not self.__is_error_covered(error):
            logger.error("Error not covered after programmatic fix.")
            return False
        new_coverage = self.get_overall_coverage()
        if new_coverage.get("hit", 0.0) < current_coverage.get("hit", 0.0):
            logger.error("Overall coverage decreased after programmatic fix.")
            return False
        if not self.__is_error_solved(error):
            logger.error("Error not solved after programmatic fix.")
            return False
        logger.info("Error resolved programmatically!")
        return True

    def create_error_trace_file(self, error: CBMCError):

        json_report_path = os.path.join(self.harness_dir, "build/report/json")
        if not os.path.exists(json_report_path):
            logger.error(f"[ERROR] JSON report path not found: {json_report_path}")
            return 

        with open(os.path.join(json_report_path, "viewer-trace.json"), 'r') as file:
            error_traces = json.load(file)

        error_trace = error_traces.get('viewer-trace', {}).get('traces', {}).get(error.error_id, {})
        
        error_trace_file = f"{self.harness_dir}/error_trace.json"
        with open(error_trace_file, 'w') as outfile:
            json.dump(error_trace, outfile, indent=4)

    def validate_preconditions(self, error: CBMCError, tag: str, analysis: str) -> Status:

        # Execute command to get diff between harness tagged backup and current harness
        harness_backup_path = f"{self.harness_file_name}.{tag}.backup"
        diff_command = f"diff {harness_backup_path} {self.harness_file_name}"
        diff_result = self.execute_command(diff_command, workdir=self.harness_dir, timeout=60)

        logger.info(f"Stdout:\n {diff_result.get('stdout', '')}")
        logger.info(f"Stderr:\n {diff_result.get('stderr', '')}")

        if diff_result.get("exit_code") != 1 and diff_result.get("exit_code") != 0:
            logger.error("[ERROR] Diff command failed.")
            return Status.ERROR

        diff_output = diff_result.get("stdout", "")
        if not diff_output:
            logger.info("No differences found between harness and backup; no preconditions to validate.")
            return Status.SUCCESS

        # Use Precondition Validator to validate the preconditions

        validation_status = self.validator.validate(error, diff_output, analysis)
        if validation_status != Status.SUCCESS:
            logger.error("[ERROR] Precondition validation failed.")
            return validation_status

        return Status.SUCCESS

    def generate_fix_with_llm(self, error: CBMCError, current_coverage: dict, tag: str) -> tuple[bool, dict]:
        """Generate the fix of a given error"""
        cause_of_failure = None
        conversation_history = []
        attempt = 0

        self.create_error_trace_file(error)
        
        # Track initial property count for validation
        initial_property_count = self.get_property_count()
        logger.info(f"Initial property count: {initial_property_count}")

        while attempt < self.__max_attempts:
            attempt += 1
            logger.info("Attempt: %i", attempt)
            logger.info("Cluster: %s", error.cluster)
            logger.info("Error id: %s", error.error_id)

            error_covered_initially = self.__is_error_covered(error)

            if not error_covered_initially:
                logger.info("Error not covered initially. Continuing to fix.")

            system_prompt = self.__get_prompt("general_system")
            user_prompt = self.__compute_user_prompt(error, cause_of_failure)
            output, chat_data = self.llm.chat_llm(
                system_messages=system_prompt,
                input_messages=user_prompt,
                output_format=ModelOutput,
                llm_tools=self.get_tools(),
                call_function=self.handle_tool_calls,
                conversation_history=conversation_history,
            )
            if not output or not isinstance(output, ModelOutput):
                logger.error("[ERROR] No valid response from LLM.")
                self.log_task_attempt(error.error_id, attempt, chat_data, error="no_valid_response")
                break
            if not output.updated_harness:
                logger.info("[INFO] No updated harness provided by LLM.")
                self.log_task_attempt(error.error_id, attempt, chat_data, error="no_updated_harness")
                break
            self.__update_harness(output.updated_harness)
            make_result = self.run_make()
            if make_result.get("status") == Status.ERROR:
                logger.error("[ERROR] Make command failed to execute.")
                self.log_task_attempt(error.error_id, attempt, chat_data, error="make_invocation_failed")
                break
            if make_result.get("status") == Status.FAILURE:
                self.log_task_attempt(error.error_id, attempt, chat_data, error="make_failed")
                # Let's use the makefile debugger to fix this error
                makefile_debugger = MakefileDebugger(
                    args=self.args,
                    project_container=self.project_container,
                )
                compile_errors_fixed = makefile_debugger.generate()
                if not compile_errors_fixed:
                    cause_of_failure = {"reason": "make_failed", "make_output": make_result}
                    continue
                make_result = self.run_make()
            if make_result.get("status") == Status.TIMEOUT:
                logger.error("[ERROR] Make command timed out.")
                self.log_task_attempt(error.error_id, attempt, chat_data, error="make_timeout")
                break
            if error_covered_initially and not self.__is_error_covered(error):
                self.log_task_attempt(error.error_id, attempt, chat_data, error="error_not_covered")
                cause_of_failure = {"reason": "error_not_covered"}
                continue
            new_coverage = self.get_overall_coverage()
            if new_coverage.get("hit", 0.0) < current_coverage.get("hit", 0.0) or new_coverage.get("percentage", 0.0) < current_coverage.get("percentage", 0.0):
                self.log_task_attempt(error.error_id, attempt, chat_data, error="overall_coverage_decreased")
                cause_of_failure = {"reason": "overall_coverage_decreased"}
                continue
            # Property count validation: ensure LLM didn't remove functions that reduce properties
            new_property_count = self.get_property_count()
            if new_property_count >= 0 and initial_property_count >= 0 and new_property_count < initial_property_count:
                logger.error(f"[ERROR] Property count reduced from {initial_property_count} to {new_property_count}")
                removed_properties, diff_output = self.get_properties_diff(tag)
                self.log_task_attempt(error.error_id, attempt, chat_data, error="properties_reduced")
                cause_of_failure = {
                    "reason": "properties_reduced",
                    "initial_count": initial_property_count,
                    "new_count": new_property_count,
                    "removed_properties": removed_properties,
                    "diff": diff_output
                }
                continue
            if not self.__is_error_solved(error):
                self.log_task_attempt(error.error_id, attempt, chat_data, error="error_not_fixed")
                cause_of_failure = {"reason": "error_not_fixed"}
                continue
            logger.info("Error resolved! Validating proposed preconditions...")
            self.validate_preconditions(error, tag, output.analysis)
            logger.info("Preconditions validated!")
            self.log_task_attempt(error.error_id, attempt, chat_data, error=None)
            self.log_task_result(error.error_id, True, attempt)
            logger.info(f"[INFO] Current Overall Coverage: {json.dumps(new_coverage, indent=2)}")
            return True, new_coverage
        self.log_task_result(error.error_id, False, attempt)
        logger.info("Error not resolved...")
        return False, current_coverage
    
    def __update_harness(self, harness_content: str):
        with open(self.harness_file_path, "w+", encoding="utf-8") as f:
            f.write(harness_content)

    def __is_error_covered(self, error: CBMCError) -> bool:

        coverage_status = self._get_function_coverage_status(error.file, error.func)

        # CASE 5 — Target function unreachable now
        if not coverage_status:
            logger.error("[ERROR] Function coverage status not found.")
            return False

        # ✅ CASE — Success: block covered!
        result = coverage_status.get(error.line) != "missed"
    
        if result:
            logger.info("Error '%s' line %s covered", error.error_id, error.line)
        else:
            logger.info("Error '%s' line %s not covered", error.error_id, error.line)
        return result
 
    def __is_error_solved(self, error) -> bool:
        current_errors = get_json_errors(self.harness_dir)
        result = error.error_id not in current_errors
        if result:
            logger.info("Error '%s' solved", error.error_id)
        else:
            logger.info("Error '%s' not solved", error.error_id)
        return result

    def __compute_user_prompt(self, error: CBMCError, cause_of_failure):
        logger.info("Computing user prompt using 'cause_of_failure' %s", cause_of_failure)
        if cause_of_failure is None:
            logger.info("cause_of_failure is None")
            
            user_prompt = self.__get_prompt("no_previous_user")
            user_prompt = user_prompt.replace("{message}", error.msg)
            if error.is_built_in:
                user_prompt = user_prompt.replace("{error_file}", "<builtin-library-strcpy>")
            else:
                user_prompt = user_prompt.replace("{error_file}", error.file)
            user_prompt = user_prompt.replace("{error_function}", error.func)
            user_prompt = user_prompt.replace("{error_line}", str(error.line))
            user_prompt = user_prompt.replace("{harness_dir}", self.harness_dir) 
            if error.vars:
                user_prompt = user_prompt.replace(
                    "{variables}", json.dumps(error.vars, indent=4))
                
            harness_content = self.get_harness()
            makefile_content = self.get_makefile()
            user_prompt = user_prompt.replace("{harness_content}", harness_content)
            user_prompt = user_prompt.replace("{makefile_content}", makefile_content)
            return user_prompt
        if cause_of_failure["reason"] == "make_failed":
            logger.info("Reason: make_failed")
            user_prompt = self.__get_prompt("make_failed_user")
            make_output = cause_of_failure.get("make_output", {})  
            prompt_text = f"""
            Stdout:
            {make_output.get("stdout", "")}
            Stderr:
            {make_output.get("stderr", "")}
            """ 
            user_prompt = user_prompt.replace("{make_output}", prompt_text)
            return user_prompt
        if cause_of_failure["reason"] == "error_not_covered":
            logger.info("Reason: error_not_covered")
            user_prompt = self.__get_prompt("error_not_covered_user")
            return user_prompt
        if cause_of_failure["reason"] == "overall_coverage_decreased":
            logger.info("Reason: overall_coverage_decreased")
            user_prompt = self.__get_prompt("overall_coverage_decreased")
            return user_prompt
        if cause_of_failure["reason"] == "error_not_fixed":
            logger.info("Reason: error_not_fixed")
            user_prompt = self.__get_prompt("error_not_fixed_user")
            if error.vars:
                user_prompt = user_prompt.replace(
                    "{variables}", json.dumps(error.vars, indent=4)
                )
            return user_prompt
        if cause_of_failure["reason"] == "properties_reduced":
            logger.info("Reason: properties_reduced")
            user_prompt = self.__get_prompt("properties_reduced")
            initial_count = cause_of_failure.get("initial_count", 0)
            new_count = cause_of_failure.get("new_count", 0)
            removed_properties = cause_of_failure.get("removed_properties", [])
            diff = cause_of_failure.get("diff", "")
            
            user_prompt = user_prompt.replace("{initial_count}", str(initial_count))
            user_prompt = user_prompt.replace("{new_count}", str(new_count))
            user_prompt = user_prompt.replace("{removed_count}", str(initial_count - new_count))
            
            # Format removed properties as a bullet list
            if removed_properties:
                props_text = "\n".join(f"  - {prop}" for prop in removed_properties[:20])
                if len(removed_properties) > 20:
                    props_text += f"\n  ... and {len(removed_properties) - 20} more"
            else:
                props_text = "  (Unable to determine specific removed properties)"
            user_prompt = user_prompt.replace("{removed_properties}", props_text)
            user_prompt = user_prompt.replace("{diff}", diff)
            return user_prompt
        raise ValueError(
            f"Unknown cause_of_failure reason: {cause_of_failure['reason']}",
        )

    def run_make(self):
        make_results = self.execute_command("make -j4", workdir=self.harness_dir, timeout=1800)
        logger.info('Stdout:\n' + make_results.get('stdout', ''))
        logger.info('Stderr:\n' + make_results.get('stderr', ''))
        return make_results

    
# TODO: Refactor Error Handling
    def __pop_error(self, error_report: ErrorReport, errors_to_skip: set) -> Optional[CBMCError]:
        
        error = error_report.get_next_error(errors_to_skip)
        if error[2] is None:
            return None
        error[2].cluster = "" if error[0] is None else error[0]
        error[2].error_id = "" if error[1] is None else error[1]
        return error[2]

    def __get_prompt(self, prompt_name: str) -> str:
        with open(f"prompts/debugger/{prompt_name}.prompt", encoding="utf-8") as f:
            return "".join(line for line in f if not line.lstrip().startswith("#"))

    def __get_advice(self, cluster: str):
        return get_advice_for_cluster(cluster, self.harness_file_name)
   