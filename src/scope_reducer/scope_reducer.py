import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from logger import setup_logger
from makefile.output_models import HarnessResponse
from scope_widener.scope_widener import ScopeWidener
from stub_generator.source_locations import (
    is_builtin_source_location,
    resolve_source_path,
)

logger = setup_logger(__name__)

MAX_SCOPE_REDUCER_ITERATIONS = 3
MAX_SCOPE_REDUCER_LLM_ATTEMPTS = 5
DEFAULT_EXCLUDED_FUNCTIONS = {
    "calloc",
    "free",
    "harness",
    "malloc",
    "memcpy",
    "memmove",
    "memset",
    "realloc",
    "strcat",
    "strcmp",
    "strcpy",
    "strlen",
    "strncat",
    "strncmp",
    "strncpy",
}


@dataclass
class ScopeReducerCandidate:
    name: str
    file: str
    line: int
    caller_edges: int
    callee_edges: int
    weight: int


class ScopeReducer:
    def __init__(self, agent):
        self.agent = agent
        self.harness_dir = agent.harness_dir
        self.root_dir = agent.root_dir
        self.target_function = agent.target_function
        self.scope_widener = ScopeWidener(agent)

    @staticmethod
    def parse_reachable_call_graph(
        call_graph_output: str,
    ) -> List[Tuple[str, str]]:
        edges: List[Tuple[str, str]] = []
        for raw_line in call_graph_output.splitlines():
            line = raw_line.strip()
            if "->" not in line:
                continue
            parts = [part.strip() for part in line.split("->", 1)]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                continue
            edges.append((parts[0], parts[1]))
        return edges

    @classmethod
    def build_edge_metrics(
        cls,
        call_graph_output: str,
    ) -> tuple[dict[str, int], dict[str, int], set[str]]:
        caller_edges: Dict[str, int] = {}
        callee_edges: Dict[str, int] = {}
        functions: Set[str] = set()

        for caller, callee in cls.parse_reachable_call_graph(call_graph_output):
            callee_edges[caller] = callee_edges.get(caller, 0) + 1
            caller_edges[callee] = caller_edges.get(callee, 0) + 1
            functions.add(caller)
            functions.add(callee)

        return caller_edges, callee_edges, functions

    @staticmethod
    def compute_weight(callee_edges: int, caller_edges: int) -> int:
        return (callee_edges ** 2) * caller_edges

    @staticmethod
    def _parse_symbol_table(stdout: str) -> dict:
        try:
            goto_symbols = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            return {}

        if len(goto_symbols) != 3 or "symbolTable" not in goto_symbols[2]:
            return {}
        return goto_symbols[2]["symbolTable"]

    def _get_symbol_table(self, goto_file: str) -> dict:
        goto_symbols_result = self.agent.execute_command(
            f"goto-instrument --show-symbol-table {goto_file} --json-ui",
            workdir=self.harness_dir,
            timeout=60,
        )
        if goto_symbols_result.get("exit_code", -1) != 0:
            logger.error("Failed to get symbol table from GOTO binary.")
            return {}

        symbol_table = self._parse_symbol_table(goto_symbols_result.get("stdout", ""))
        if not symbol_table:
            logger.error("Unexpected format of goto symbols output.")
        return symbol_table

    @staticmethod
    def _extract_location(symbol: dict) -> tuple[str, str, str]:
        named = symbol.get("location", {}).get("namedSub", {})
        return (
            named.get("file", {}).get("id", ""),
            named.get("working_directory", {}).get("id", ""),
            named.get("line", {}).get("id", ""),
        )

    def _resolve_candidate_source(
        self,
        function_name: str,
        file_rel: str,
        working_directory: str,
        declaration_line: str,
    ) -> Optional[str]:
        declaration_hint = resolve_source_path(file_rel, working_directory) or ""
        source_path = self.scope_widener.locate_function_source(
            function_name,
            declaration_hint=declaration_hint,
            declaration_line=declaration_line,
        )
        if source_path:
            return os.path.realpath(source_path)

        if declaration_hint.endswith(".c") and os.path.isfile(declaration_hint):
            return os.path.realpath(declaration_hint)

        return None

    def rank_candidates(
        self,
        goto_file: str,
        already_modeled: Set[str],
    ) -> List[ScopeReducerCandidate]:
        call_graph_result = self.agent.execute_command(
            f"goto-instrument --reachable-call-graph {goto_file}",
            workdir=self.harness_dir,
            timeout=60,
        )
        if call_graph_result.get("exit_code", -1) != 0:
            logger.error("Failed to get reachable call graph.")
            return []

        caller_edges, callee_edges, functions = self.build_edge_metrics(
            call_graph_result.get("stdout", "")
        )
        if not functions:
            return []

        symbol_table = self._get_symbol_table(goto_file)
        candidates: List[ScopeReducerCandidate] = []

        for function_name in functions:
            if function_name == self.target_function:
                continue
            if function_name in already_modeled:
                continue
            if function_name in DEFAULT_EXCLUDED_FUNCTIONS:
                continue

            symbol = symbol_table.get(function_name)
            if not symbol:
                logger.warning("Skipping reducer candidate without symbol: %s", function_name)
                continue

            file_rel, working_directory, line_id = self._extract_location(symbol)
            if is_builtin_source_location(file_rel):
                continue

            source_path = self._resolve_candidate_source(
                function_name=function_name,
                file_rel=file_rel,
                working_directory=working_directory,
                declaration_line=line_id,
            )
            if not source_path or not os.path.isfile(source_path):
                continue

            start_line = int(line_id) if line_id.isdigit() and int(line_id) > 0 else 1
            num_caller_edges = caller_edges.get(function_name, 0)
            num_callee_edges = callee_edges.get(function_name, 0)
            candidates.append(
                ScopeReducerCandidate(
                    name=function_name,
                    file=source_path,
                    line=start_line,
                    caller_edges=num_caller_edges,
                    callee_edges=num_callee_edges,
                    weight=self.compute_weight(
                        num_callee_edges,
                        num_caller_edges,
                    ),
                )
            )

        candidates.sort(
            key=lambda candidate: (
                -candidate.weight,
                -candidate.callee_edges,
                -candidate.caller_edges,
                candidate.name,
            )
        )
        return candidates

    def select_candidate(
        self,
        goto_file: str,
        already_modeled: Set[str],
    ) -> Optional[ScopeReducerCandidate]:
        candidates = self.rank_candidates(goto_file, already_modeled)
        return candidates[0] if candidates else None

    def extract_function_signature(
        self,
        file_path: str,
        func_name: str,
        start_line: int,
    ) -> str:
        if is_builtin_source_location(file_path):
            return ""

        if not file_path or not os.path.isfile(file_path):
            return ""

        signature_lines: List[str] = []
        inside_signature = False

        with open(file_path, "r", encoding="utf-8", errors="ignore") as file_handle:
            lines = file_handle.readlines()

        for index in range(max(start_line - 1, 0), len(lines)):
            line = lines[index].strip()

            if not inside_signature and re.search(rf"\b{re.escape(func_name)}\b", line):
                inside_signature = True

            if inside_signature:
                signature_lines.append(line)
                if "{" in line or ";" in line:
                    break

        if not signature_lines:
            for line in lines:
                stripped = line.strip()
                if re.search(rf"\b{re.escape(func_name)}\b", stripped):
                    signature_lines.append(stripped)
                    if "{" in stripped or ";" in stripped:
                        break

        signature = " ".join(signature_lines)
        signature = re.sub(r"\s+", " ", signature)
        signature = signature.split("{")[0].strip()
        return signature

    def prepare_prompt(
        self,
        candidate: ScopeReducerCandidate,
    ) -> tuple[str, str]:
        with open("prompts/gen_stubs_system.prompt", "r", encoding="utf-8") as file_handle:
            system_prompt = file_handle.read()

        with open("prompts/gen_stubs_user.prompt", "r", encoding="utf-8") as file_handle:
            user_prompt = file_handle.read()

        signature = (
            self.extract_function_signature(
                candidate.file,
                candidate.name,
                candidate.line,
            )
            or "<signature unavailable>"
        )
        stub_info = (
            f"\n            Function Name: {candidate.name}\n"
            f"            Signature: {signature}\n"
            f"            Source File: {candidate.file}\n"
        )

        user_prompt = user_prompt.replace("{HARNESS_CODE}", self.agent.get_harness())
        user_prompt = user_prompt.replace("{MAKEFILE_CODE}", self.agent.get_makefile())
        user_prompt = user_prompt.replace("{STUBS_REQUIRED}", stub_info)
        user_prompt = user_prompt.replace("{PROJECT_DIR}", self.root_dir)
        return system_prompt, user_prompt

    def _generate_candidate_stub(
        self,
        candidate: ScopeReducerCandidate,
    ) -> bool:
        system_prompt, user_prompt = self.prepare_prompt(candidate)
        tools = self.agent.get_tools()
        conversation: list = []
        attempts = 0

        while user_prompt and attempts < MAX_SCOPE_REDUCER_LLM_ATTEMPTS:
            llm_response, llm_data = self.agent.llm.chat_llm(
                system_prompt,
                user_prompt,
                HarnessResponse,
                llm_tools=tools,
                call_function=self.agent.handle_tool_calls,
                conversation_history=conversation,
            )

            if not llm_response or not isinstance(llm_response, HarnessResponse):
                self.agent.log_task_attempt(
                    "scope_reducer_stub_generation",
                    attempts + 1,
                    llm_data,
                    "invalid_response",
                )
                user_prompt = (
                    "The LLM did not return a valid response. "
                    "Please provide a response using the expected format.\n"
                )
                attempts += 1
                continue

            self.agent.log_task_attempt(
                "scope_reducer_stub_generation",
                attempts + 1,
                llm_data,
                "",
            )
            self.agent.update_harness(llm_response.harness_code)
            compile_results = self.agent.run_make(compile_only=True)

            if compile_results.get("exit_code", -1) == 0:
                return True

            user_prompt = f"""
            The previously generated harness did not compile successfully.
            Here are the results from the make command:

            Exit Code: {compile_results.get('exit_code', -1)}
            Stdout: {compile_results.get('stdout', '')}
            Stderr: {compile_results.get('stderr', '')}

            Please analyze the errors and generate an updated harness that addresses these issues.
            """
            attempts += 1

        return False

    def _is_over_threshold(
        self,
        make_results: dict,
        time_budget_seconds: float | None,
    ) -> bool:
        if time_budget_seconds is None:
            return (
                make_results.get("status") == "TIMEOUT"
                or make_results.get("exit_code") == 124
            )
        return (
            make_results.get("status") == "TIMEOUT"
            or make_results.get("exit_code") == 124
            or make_results.get("elapsed_seconds", 0.0) > time_budget_seconds
        )

    def reduce_scope(self, time_budget_seconds: float | None) -> bool:
        reducer_tag = uuid.uuid4().hex[:4].upper()
        self.agent.create_backup(reducer_tag)
        already_modeled: Set[str] = set()
        selected_functions: List[str] = []
        reduction_succeeded = False

        try:
            for iteration in range(1, MAX_SCOPE_REDUCER_ITERATIONS + 1):
                compile_results = self.agent.run_make(compile_only=True)
                if compile_results.get("exit_code", -1) != 0:
                    logger.error(
                        "Scope reducer failed to compile the current harness at iteration %s.",
                        iteration,
                    )
                    break

                goto_file = os.path.join(
                    self.harness_dir,
                    "build",
                    f"{self.target_function}.goto",
                )
                if not os.path.exists(goto_file):
                    logger.error("GOTO file not found for scope reducer: %s", goto_file)
                    break

                candidate = self.select_candidate(goto_file, already_modeled)
                if candidate is None:
                    logger.info("Scope reducer found no eligible functions to stub.")
                    break

                logger.info(
                    "Scope reducer iteration %s selected '%s' (weight=%s, caller_edges=%s, callee_edges=%s).",
                    iteration,
                    candidate.name,
                    candidate.weight,
                    candidate.caller_edges,
                    candidate.callee_edges,
                )

                iteration_tag = uuid.uuid4().hex[:4].upper()
                self.agent.create_backup(iteration_tag)
                generated = False
                try:
                    generated = self._generate_candidate_stub(candidate)
                    if not generated:
                        logger.warning(
                            "Scope reducer could not generate a compilable stub for '%s'.",
                            candidate.name,
                        )
                        self.agent.restore_backup(iteration_tag)
                        already_modeled.add(candidate.name)
                        continue

                    verification_results = self.agent.run_make(compile_only=False)
                    selected_functions.append(candidate.name)
                    already_modeled.add(candidate.name)

                    if self._is_over_threshold(verification_results, time_budget_seconds):
                        logger.info(
                            "Scope reducer kept '%s' but verification is still over budget.",
                            candidate.name,
                        )
                        continue

                    if not self.agent.validate_verification_report():
                        logger.warning(
                            "Scope reducer generated '%s' but verification did not produce a valid report.",
                            candidate.name,
                        )
                        self.agent.restore_backup(iteration_tag)
                        selected_functions.pop()
                        continue

                    reduction_succeeded = True
                    logger.info(
                        "Scope reducer brought verification within budget after stubbing '%s'.",
                        candidate.name,
                    )
                    break
                finally:
                    self.agent.discard_backup(iteration_tag)
        finally:
            if not reduction_succeeded:
                self.agent.restore_backup(reducer_tag)
            self.agent.discard_backup(reducer_tag)
            self.agent.log_agent_result(
                {
                    "scope_reducer_succeeded": reduction_succeeded,
                    "scope_reducer_iterations": len(selected_functions),
                    "scope_reducer_functions": selected_functions,
                }
            )

        return reduction_succeeded
