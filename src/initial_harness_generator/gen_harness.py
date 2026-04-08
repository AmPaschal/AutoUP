

from pathlib import Path
import json
import os
import re
import uuid
from agent import AIAgent
from commons.models import GPT, Generable
from makefile.output_models import HarnessResponse
from logger import setup_logger
from makefile_generator.makefile_generator import MakefileGenerator
from scope_widener.scope_widener import ScopeWidener
from commons.utils import Status
from stub_generator.gen_function_stubs import StubGenerator
from stub_generator.handle_function_pointers import FunctionPointerHandler

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
        else:
            # Copy inside the container
            copy_cmd = f"cp {src_path} {dest_path}"
            copy_results = self.project_container.execute(copy_cmd, workdir='/')
            if copy_results.get('exit_code', -1) != 0:
                logger.error(f'Failed to copy Makefile.include: {copy_results.get("stderr", "")}')
                return
            logger.info(f'Copied Makefile.include to {dest_path}')

        # Copy general-stubs.c to harness parent directory
        src_stubs_path = os.path.join('makefiles', 'general-stubs.c')
        dest_stubs_path = os.path.join(os.path.dirname(self.harness_dir), 'general-stubs.c')
        if os.path.exists(dest_stubs_path):
            logger.info(f'general-stubs.c already exists at {dest_stubs_path}, skipping copy.')
        else:
            copy_cmd = f"cp {src_stubs_path} {dest_stubs_path}"
            copy_results = self.project_container.execute(copy_cmd, workdir='/')
            if copy_results.get('exit_code', -1) != 0:
                logger.error(f'Failed to copy general-stubs.c: {copy_results.get("stderr", "")}')
                return
            logger.info(f'Copied general-stubs.c to {dest_stubs_path}')

        # Copy zephyr-stubs.c to harness parent directory
        src_stubs_path = os.path.join('makefiles', 'zephyr-stubs.c')
        dest_stubs_path = os.path.join(os.path.dirname(self.harness_dir), 'zephyr-stubs.c')
        if os.path.exists(dest_stubs_path):
            logger.info(f'general-stubs.c already exists at {dest_stubs_path}, skipping copy.')
        else:
            copy_cmd = f"cp {src_stubs_path} {dest_stubs_path}"
            copy_results = self.project_container.execute(copy_cmd, workdir='/')
            if copy_results.get('exit_code', -1) != 0:
                logger.error(f'Failed to copy zephyr-stubs.c: {copy_results.get("stderr", "")}')
                return
            logger.info(f'Copied zephyr-stubs.c to {dest_stubs_path}')


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
        pattern = r'^\s*#\s*(?:ifdef|if)\s+(?:defined\s*\(\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\)?'

        for line in lines:
            match = re.match(pattern, line)
            if match:
                config = match.group(1)
                configs.add(config)

        return list(configs)

    @staticmethod
    def _is_over_time_budget(make_results: dict, time_budget_seconds: float) -> bool:
        return (
            make_results.get("status") == Status.TIMEOUT
            or make_results.get("exit_code") == 124
            or make_results.get("elapsed_seconds", 0.0) > time_budget_seconds
        )

    def _run_integrated_model_generation(
        self,
        verify_after_generation: bool,
    ) -> bool:
        if not self._run_function_pointer_generation(
            verify_after_generation=verify_after_generation,
        ):
            return False

        if not self._run_stub_generation(
            verify_after_generation=verify_after_generation,
        ):
            return False

        return True

    def _run_function_pointer_generation(
        self,
        verify_after_generation: bool,
    ) -> bool:
        function_pointer_handler = FunctionPointerHandler(
            args=self.args,
            project_container=self.project_container,
        )
        if not function_pointer_handler.generate(
            verify_after_generation=verify_after_generation,
        ):
            logger.error(
                "Function pointer generation failed in integrated scope widening."
            )
            return False

        return True

    def _run_stub_generation(
        self,
        verify_after_generation: bool,
    ) -> bool:
        stub_generator = StubGenerator(
            args=self.args,
            project_container=self.project_container,
        )
        if not stub_generator.generate(
            verify_after_generation=verify_after_generation,
        ):
            logger.error("Stub generation failed in integrated scope widening.")
            return False

        return True

    def _create_backup_tag(self) -> str:
        return uuid.uuid4().hex[:4].upper()

    def _discard_backup_if_present(self, tag: str | None) -> None:
        if tag is not None:
            self.discard_backup(tag)

    def _run_budgeted_scope_widening(
        self,
        widener: ScopeWidener,
        scope_bound: int | None,
        time_budget_minutes: float,
    ) -> int:
        time_budget_seconds = time_budget_minutes * 60.0
        current_level = 1

        if not self._run_function_pointer_generation(
            verify_after_generation=False,
        ):
            return -1

        accepted_pre_stub_tag = self._create_backup_tag()
        self.create_backup(accepted_pre_stub_tag)
        accepted_post_stub_tag: str | None = None
        try:
            if not self._run_stub_generation(
                verify_after_generation=False,
            ):
                return -1

            baseline_results = self.run_make(compile_only=False)
            if self._is_over_time_budget(baseline_results, time_budget_seconds):
                logger.info(
                    "Baseline verification at scope level 1 exceeded the "
                    "time budget (%.2fs > %.2fs or timed out). No widening "
                    "will be attempted.",
                    baseline_results.get("elapsed_seconds", 0.0),
                    time_budget_seconds,
                )
                return current_level

            if not self.validate_verification_report():
                logger.error(
                    "Baseline verification did not produce a report. "
                    "Aborting scope widening."
                )
                return -1

            accepted_post_stub_tag = self._create_backup_tag()
            self.create_backup(accepted_post_stub_tag)

            if scope_bound is not None and scope_bound <= 1:
                logger.info("Scope bound is %s; no widening needed.", scope_bound)
                return current_level

            while widener._within_scope_bound(current_level, scope_bound):
                self.restore_backup(accepted_pre_stub_tag)
                step_result = widener.widen_scope_level(current_level)
                if step_result.outcome == "complete":
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                if step_result.outcome == "failed":
                    logger.warning(
                        "Scope widening failed at level %s. Restoring the "
                        "previous accepted scope.",
                        current_level + 1,
                    )
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                if not self._run_function_pointer_generation(
                    verify_after_generation=False,
                ):
                    logger.warning(
                        "Function pointer generation failed at scope level %s. "
                        "Restoring the previous accepted scope.",
                        step_result.level,
                    )
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                candidate_pre_stub_tag = self._create_backup_tag()
                self.create_backup(candidate_pre_stub_tag)

                if not self._run_stub_generation(
                    verify_after_generation=False,
                ):
                    logger.warning(
                        "Stub generation failed at scope level %s. "
                        "Restoring the previous accepted scope.",
                        step_result.level,
                    )
                    self.discard_backup(candidate_pre_stub_tag)
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                verification_results = self.run_make(compile_only=False)
                if self._is_over_time_budget(
                    verification_results,
                    time_budget_seconds,
                ):
                    logger.info(
                        "Scope widening level %s exceeded the time budget. "
                        "Restoring the previous accepted scope.",
                        step_result.level,
                    )
                    self.discard_backup(candidate_pre_stub_tag)
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                if not self.validate_verification_report():
                    logger.warning(
                        "Scope widening level %s did not produce a "
                        "verification report. Restoring the previous "
                        "accepted scope.",
                        step_result.level,
                    )
                    self.discard_backup(candidate_pre_stub_tag)
                    self.restore_backup(accepted_post_stub_tag)
                    return current_level

                self.discard_backup(accepted_pre_stub_tag)
                self.discard_backup(accepted_post_stub_tag)
                accepted_pre_stub_tag = candidate_pre_stub_tag
                accepted_post_stub_tag = self._create_backup_tag()
                self.create_backup(accepted_post_stub_tag)
                current_level = step_result.level

            return current_level
        finally:
            self._discard_backup_if_present(accepted_pre_stub_tag)
            self._discard_backup_if_present(accepted_post_stub_tag)

    def _run_bound_only_scope_widening(
        self,
        widener: ScopeWidener,
        scope_bound: int,
    ) -> int:
        current_level = 1
        if scope_bound <= 1:
            logger.info("Scope bound is %s; no widening needed.", scope_bound)
        else:
            while widener._within_scope_bound(current_level, scope_bound):
                rollback_tag = self._create_backup_tag()
                self.create_backup(rollback_tag)
                try:
                    step_result = widener.widen_scope_level(current_level)
                    if step_result.outcome == "complete":
                        break

                    if step_result.outcome == "failed":
                        logger.warning(
                            "Scope widening failed at level %s. Restoring "
                            "the previous compiled scope.",
                            current_level + 1,
                        )
                        self.restore_backup(rollback_tag)
                        break

                    current_level = step_result.level
                finally:
                    self.discard_backup(rollback_tag)

        model_tag = self._create_backup_tag()
        self.create_backup(model_tag)
        try:
            if not self._run_integrated_model_generation(
                verify_after_generation=False,
            ):
                logger.warning(
                    "Final model generation failed after bound-only scope "
                    "widening. Restoring the last compiled scope."
                )
                self.restore_backup(model_tag)
                return -1
            return current_level
        finally:
            self.discard_backup(model_tag)

    def _run_scope_widening_flow(
        self,
        scope_bound: int | None,
        time_budget_minutes: float | None,
    ) -> int:
        widener = ScopeWidener(agent=self)

        if time_budget_minutes is not None:
            return self._run_budgeted_scope_widening(
                widener=widener,
                scope_bound=scope_bound,
                time_budget_minutes=time_budget_minutes,
            )

        if scope_bound is None:
            return -1

        return self._run_bound_only_scope_widening(
            widener=widener,
            scope_bound=scope_bound,
        )

    def generate(self) -> bool:

        # First generate initial harnesses
        os.makedirs(self.harness_dir, exist_ok=True)

        system_prompt, user_prompt = self.prepare_prompt()
        tools = self.get_tools()
        attempts = 0

        logger.info(f'System Prompt:\n{system_prompt}')

        conversation = []   
        harness_generated = False
        agent_result = {"compilation_status": False, "verification_status": False}

        while user_prompt and attempts <= self._max_attempts:

            attempts += 1
            llm_response, llm_data = self.llm.chat_llm(system_prompt, user_prompt, HarnessResponse, llm_tools=tools, call_function=self.handle_tool_calls, conversation_history=conversation)

            if not llm_response or not isinstance(llm_response, HarnessResponse):
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
            self.log_agent_result(agent_result)
            return False

        # Then generate initial Makefile

        # Copy makefile.include from docker to harness parent directory
        self.create_makefile_include()

        initial_configs = self.extract_configs_from_sourcefile()

        # We setup the initial Makefile
        makefile = self.setup_initial_makefile(initial_configs)
        self.update_makefile(makefile)   

        # Now, we try to resolve all the make errors
        makefile_generator = MakefileGenerator(
                                args=self.args,
                                project_container=self.project_container
                            )
        status = makefile_generator.generate()
        completed_scope_level = 1
        agent_result["compilation_status"] = status
        if status:
            logger.info("Initial harness compiles. Checking verification...")

            # --- Scope widening ---
            scope_bound = getattr(self.args, "scope_bound", None)
            scope_time_budget_minutes = getattr(
                self.args,
                "scope_time_budget",
                None,
            )
            if scope_bound is not None or scope_time_budget_minutes is not None:
                logger.info(
                    "Running scope widening with bound=%s and time budget=%s minute(s)...",
                    scope_bound,
                    scope_time_budget_minutes,
                )
                completed_scope_level = self._run_scope_widening_flow(
                    scope_bound=scope_bound,
                    time_budget_minutes=scope_time_budget_minutes,
                )
                if completed_scope_level >= 0:
                    logger.info(
                        "Scope widening completed at level %s.",
                        completed_scope_level,
                    )
                else:
                    logger.warning(
                        "Scope widening did not fully succeed. "
                        "Continuing with current compilation state."
                    )

            make_results = self.run_make(compile_only=False)
            
            status_code = make_results.get('status', Status.ERROR)

            if status_code == Status.SUCCESS and make_results.get('exit_code', -1) == 0:
                agent_result["verification_status"] = True
                logger.info("Initial harness verification succeeded.")

        agent_result["completed_scope_level"] = completed_scope_level
        self.log_agent_result(agent_result)

        self.save_status('harness')
        return status
        
