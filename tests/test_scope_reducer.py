import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from commons.utils import Status
from scope_reducer.scope_reducer import (
    DEFAULT_EXCLUDED_FUNCTIONS,
    MAX_SCOPE_REDUCER_ITERATIONS,
    ScopeReducer,
)


class FakeLLM:
    def __init__(self, harness_suffixes: list[str] | None = None):
        self.harness_suffixes = list(harness_suffixes or [])

    def chat_llm(
        self,
        system_prompt,
        user_prompt,
        output_format,
        llm_tools=None,
        call_function=None,
        conversation_history=None,
    ):
        suffix = self.harness_suffixes.pop(0) if self.harness_suffixes else "/* reducer stub */\n"
        return output_format(
            analysis="stubbed function",
            harness_code=user_prompt.split("<harness>\n", 1)[1].split("\n</harness>", 1)[0] + "\n" + suffix,
        ), {"model_name": "fake"}


class FakeReducerAgent:
    def __init__(
        self,
        temp_dir: str,
        call_graph_output: str,
        symbol_table: dict,
        compile_results: list[dict] | None = None,
        verification_results: list[dict] | None = None,
    ):
        self.temp_dir = Path(temp_dir)
        self.root_dir = str(self.temp_dir / "project")
        self.harness_dir = str(self.temp_dir / "proofs" / "demo" / "target")
        self.target_function = "target"
        self.target_file_path = str(self.temp_dir / "project" / "src" / "target.c")
        self.harness_file_name = "target_harness.c"
        self.harness_file_path = str(Path(self.harness_dir) / self.harness_file_name)
        self.makefile_path = str(Path(self.harness_dir) / "Makefile")
        self.metrics_file = None
        self.progress = None
        self.project_container = object()
        self.args = SimpleNamespace(llm_model="gpt-5.3-codex")
        self._call_graph_output = call_graph_output
        self._symbol_table = symbol_table
        self._compile_results = list(compile_results or [])
        self._verification_results = list(verification_results or [])
        self._reports: list[bool] = []
        self.llm = FakeLLM()
        self.logged_results: list[dict] = []

        Path(self.root_dir, "src").mkdir(parents=True, exist_ok=True)
        Path(self.harness_dir).mkdir(parents=True, exist_ok=True)
        Path(self.target_file_path).write_text("void target(void) {}\n", encoding="utf-8")
        Path(self.harness_file_path).write_text("void harness(void) {}\n", encoding="utf-8")
        Path(self.makefile_path).write_text("LINK = baseline.c\n", encoding="utf-8")
        build_dir = Path(self.harness_dir) / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        build_dir.joinpath("target.goto").write_text("goto\n", encoding="utf-8")

    def execute_command(self, cmd: str, workdir: str, timeout: int) -> dict:
        if cmd.startswith("goto-instrument --reachable-call-graph "):
            return {"exit_code": 0, "stdout": self._call_graph_output, "stderr": ""}
        if cmd.startswith("goto-instrument --show-symbol-table "):
            return {
                "exit_code": 0,
                "stdout": json.dumps([{}, {}, {"symbolTable": self._symbol_table}]),
                "stderr": "",
            }
        if cmd.startswith("cscope -dL -1 "):
            function_name = cmd.split()[-1]
            symbol = self._symbol_table[function_name]
            file_rel = symbol["location"]["namedSub"]["file"]["id"]
            line = symbol["location"]["namedSub"]["line"]["id"]
            return {
                "exit_code": 0,
                "stdout": f"{file_rel} context {line} {function_name}(",
                "stderr": "",
            }
        return {"exit_code": 1, "stdout": "", "stderr": f"Unexpected command: {cmd}"}

    def run_make(self, compile_only: bool = False) -> dict:
        if compile_only:
            result = dict(self._compile_results.pop(0) if self._compile_results else {
                "status": Status.SUCCESS,
                "exit_code": 0,
                "elapsed_seconds": 0.1,
            })
            Path(self.harness_dir, "build", "target.goto").write_text("goto\n", encoding="utf-8")
            return {
                "status": result.get("status", Status.SUCCESS),
                "exit_code": result.get("exit_code", 0),
                "elapsed_seconds": result.get("elapsed_seconds", 0.1),
                "stdout": "",
                "stderr": "",
            }

        result = dict(self._verification_results.pop(0) if self._verification_results else {
            "status": Status.SUCCESS,
            "exit_code": 0,
            "elapsed_seconds": 0.1,
            "report": True,
        })
        report_exists = result.get("report", False)
        report_path = Path(self.harness_dir) / "build" / "report" / "json" / "viewer-property.json"
        if report_exists:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("{}", encoding="utf-8")
        elif report_path.exists():
            report_path.unlink()

        self._reports.append(report_exists)
        return {
            "status": result.get("status", Status.SUCCESS),
            "exit_code": result.get("exit_code", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0.1),
            "stdout": "",
            "stderr": "",
        }

    def validate_verification_report(self) -> bool:
        return Path(
            self.harness_dir,
            "build",
            "report",
            "json",
            "viewer-property.json",
        ).exists()

    def update_harness(self, harness_code: str) -> None:
        Path(self.harness_file_path).write_text(harness_code, encoding="utf-8")

    def get_harness(self) -> str:
        return Path(self.harness_file_path).read_text(encoding="utf-8")

    def get_makefile(self) -> str:
        return Path(self.makefile_path).read_text(encoding="utf-8")

    def get_tools(self):
        return []

    def handle_tool_calls(self, tool_name, function_args):
        raise AssertionError("Reducer tests should not need tool calls")

    def log_task_attempt(self, task_id, attempt_number, llm_data, error):
        return None

    def log_agent_result(self, data: dict):
        self.logged_results.append(data)

    def create_backup(self, tag: str):
        Path(self.harness_dir, f"{self.harness_file_name}.{tag}.backup").write_text(
            self.get_harness(),
            encoding="utf-8",
        )
        Path(self.harness_dir, f"Makefile.{tag}.backup").write_text(
            self.get_makefile(),
            encoding="utf-8",
        )

    def restore_backup(self, tag: str):
        self.update_harness(
            Path(self.harness_dir, f"{self.harness_file_name}.{tag}.backup").read_text(
                encoding="utf-8"
            )
        )

    def discard_backup(self, tag: str):
        for file_name in [
            f"{self.harness_file_name}.{tag}.backup",
            f"Makefile.{tag}.backup",
        ]:
            backup_path = Path(self.harness_dir, file_name)
            if backup_path.exists():
                backup_path.unlink()


class ScopeReducerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def create_source(self, relative_path: str, content: str) -> str:
        source_path = Path(self.temp_dir.name) / "project" / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(content, encoding="utf-8")
        return str(source_path)

    def make_symbol(self, relative_path: str, line: int = 1) -> dict:
        return {
            "location": {
                "namedSub": {
                    "file": {"id": relative_path},
                    "working_directory": {"id": str(Path(self.temp_dir.name) / "project")},
                    "line": {"id": str(line)},
                }
            }
        }

    def test_build_edge_metrics_counts_incoming_and_outgoing_edges(self):
        caller_edges, callee_edges, functions = ScopeReducer.build_edge_metrics(
            "\n".join([
                "harness -> target",
                "target -> alpha",
                "target -> beta",
                "alpha -> gamma",
                "beta -> gamma",
            ])
        )

        self.assertEqual(callee_edges["target"], 2)
        self.assertEqual(caller_edges["gamma"], 2)
        self.assertIn("alpha", functions)

    def test_rank_candidates_orders_by_weight_and_excludes_denylist(self):
        self.create_source("src/alpha.c", "int alpha(void) { return 0; }\n")
        self.create_source("src/beta.c", "int beta(void) { return 0; }\n")
        self.create_source("src/gamma.c", "int gamma(void) { return 0; }\n")
        self.create_source("src/memcpy.c", "int memcpy(void) { return 0; }\n")
        symbol_table = {
            "alpha": self.make_symbol("src/alpha.c"),
            "beta": self.make_symbol("src/beta.c"),
            "gamma": self.make_symbol("src/gamma.c"),
            "memcpy": self.make_symbol("src/memcpy.c"),
            "target": self.make_symbol("src/target.c"),
        }
        agent = FakeReducerAgent(
            self.temp_dir.name,
            call_graph_output="\n".join([
                "harness -> target",
                "target -> alpha",
                "target -> beta",
                "alpha -> gamma",
                "beta -> gamma",
                "target -> memcpy",
            ]),
            symbol_table=symbol_table,
        )
        reducer = ScopeReducer(agent)

        candidates = reducer.rank_candidates(
            os.path.join(agent.harness_dir, "build", "target.goto"),
            already_modeled=set(),
        )

        self.assertEqual([candidate.name for candidate in candidates], ["alpha", "beta", "gamma"])
        self.assertIn("memcpy", DEFAULT_EXCLUDED_FUNCTIONS)

    def test_select_candidate_skips_already_modeled_functions(self):
        self.create_source("src/alpha.c", "int alpha(void) { return 0; }\n")
        self.create_source("src/beta.c", "int beta(void) { return 0; }\n")
        symbol_table = {
            "alpha": self.make_symbol("src/alpha.c"),
            "beta": self.make_symbol("src/beta.c"),
            "target": self.make_symbol("src/target.c"),
        }
        agent = FakeReducerAgent(
            self.temp_dir.name,
            call_graph_output="\n".join([
                "harness -> target",
                "target -> alpha",
                "target -> beta",
            ]),
            symbol_table=symbol_table,
        )
        reducer = ScopeReducer(agent)

        candidate = reducer.select_candidate(
            os.path.join(agent.harness_dir, "build", "target.goto"),
            already_modeled={"alpha"},
        )

        self.assertEqual(candidate.name, "beta")

    def test_reduce_scope_succeeds_after_multiple_stub_iterations(self):
        self.create_source("src/alpha.c", "int alpha(void) { return 0; }\n")
        self.create_source("src/beta.c", "int beta(void) { return 0; }\n")
        symbol_table = {
            "alpha": self.make_symbol("src/alpha.c"),
            "beta": self.make_symbol("src/beta.c"),
            "target": self.make_symbol("src/target.c"),
        }
        agent = FakeReducerAgent(
            self.temp_dir.name,
            call_graph_output="\n".join([
                "harness -> target",
                "target -> alpha",
                "target -> beta",
            ]),
            symbol_table=symbol_table,
            verification_results=[
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
                {"status": Status.SUCCESS, "exit_code": 0, "elapsed_seconds": 5.0, "report": True},
            ],
        )
        reducer = ScopeReducer(agent)

        self.assertTrue(reducer.reduce_scope(time_budget_seconds=60.0))
        self.assertEqual(agent.logged_results[-1]["scope_reducer_functions"], ["alpha", "beta"])

    def test_reduce_scope_restores_original_harness_when_candidates_exhausted(self):
        original_harness = "void harness(void) {}\n"
        self.create_source("src/alpha.c", "int alpha(void) { return 0; }\n")
        symbol_table = {
            "alpha": self.make_symbol("src/alpha.c"),
            "target": self.make_symbol("src/target.c"),
        }
        agent = FakeReducerAgent(
            self.temp_dir.name,
            call_graph_output="\n".join([
                "harness -> target",
                "target -> alpha",
            ]),
            symbol_table=symbol_table,
            verification_results=[
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
            ],
        )
        agent.update_harness(original_harness)
        reducer = ScopeReducer(agent)

        self.assertFalse(reducer.reduce_scope(time_budget_seconds=60.0))
        self.assertEqual(agent.get_harness(), original_harness)

    def test_reduce_scope_stops_after_max_iterations(self):
        self.create_source("src/alpha.c", "int alpha(void) { return 0; }\n")
        self.create_source("src/beta.c", "int beta(void) { return 0; }\n")
        self.create_source("src/gamma.c", "int gamma(void) { return 0; }\n")
        self.create_source("src/delta.c", "int delta(void) { return 0; }\n")
        symbol_table = {
            "alpha": self.make_symbol("src/alpha.c"),
            "beta": self.make_symbol("src/beta.c"),
            "gamma": self.make_symbol("src/gamma.c"),
            "delta": self.make_symbol("src/delta.c"),
            "target": self.make_symbol("src/target.c"),
        }
        agent = FakeReducerAgent(
            self.temp_dir.name,
            call_graph_output="\n".join([
                "harness -> target",
                "target -> alpha",
                "target -> beta",
                "target -> gamma",
                "target -> delta",
            ]),
            symbol_table=symbol_table,
            verification_results=[
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
            ],
        )
        reducer = ScopeReducer(agent)

        self.assertFalse(reducer.reduce_scope(time_budget_seconds=60.0))
        self.assertEqual(
            agent.logged_results[-1]["scope_reducer_iterations"],
            MAX_SCOPE_REDUCER_ITERATIONS,
        )


if __name__ == "__main__":
    unittest.main()
