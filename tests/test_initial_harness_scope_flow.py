import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

from commons.utils import Status
from initial_harness_generator.gen_harness import InitialHarnessGenerator

GENERATION_ORDER: list[str] = []


class FakeStubGenerator:
    calls: list[bool] = []
    results_queue: list[bool] = []
    makefile_suffixes: list[str] = []

    def __init__(self, *args, **kwargs):
        self.args = kwargs.get("args")

    def generate(self, verify_after_generation: bool = True) -> bool:
        type(self).calls.append(verify_after_generation)
        GENERATION_ORDER.append("stub")
        if type(self).makefile_suffixes:
            makefile_path = Path(self.args.harness_path) / "Makefile"
            makefile_path.write_text(
                makefile_path.read_text(encoding="utf-8") + type(self).makefile_suffixes.pop(0),
                encoding="utf-8",
            )
        if type(self).results_queue:
            return type(self).results_queue.pop(0)
        return True


class FakeFunctionPointerHandler:
    calls: list[bool] = []
    results_queue: list[bool] = []
    makefile_suffixes: list[str] = []

    def __init__(self, *args, **kwargs):
        self.args = kwargs.get("args")

    def generate(self, verify_after_generation: bool = True) -> bool:
        type(self).calls.append(verify_after_generation)
        GENERATION_ORDER.append("fp")
        if type(self).makefile_suffixes:
            makefile_path = Path(self.args.harness_path) / "Makefile"
            makefile_path.write_text(
                makefile_path.read_text(encoding="utf-8") + type(self).makefile_suffixes.pop(0),
                encoding="utf-8",
            )
        if type(self).results_queue:
            return type(self).results_queue.pop(0)
        return True


class FakeWidener:
    def __init__(self, agent, step_results):
        self.agent = agent
        self.step_results = list(step_results)
        self.entered_makefiles: list[str] = []

    def _within_scope_bound(self, current_level: int, scope_bound: int | None) -> bool:
        return scope_bound is None or current_level < scope_bound

    def widen_scope_level(self, current_level: int):
        self.entered_makefiles.append(self.agent.get_makefile())
        step = dict(self.step_results.pop(0))
        if step.get("makefile_suffix"):
            self.agent.update_makefile(
                self.agent.get_makefile() + step["makefile_suffix"]
            )
        return SimpleNamespace(
            outcome=step["outcome"],
            level=step.get("level", current_level),
            new_files=[],
        )


class HarnessFlowHarness(InitialHarnessGenerator):
    def __init__(self, temp_dir: str, full_results: list[dict] | None = None):
        self.temp_dir = Path(temp_dir)
        self.root_dir = str(self.temp_dir / "project")
        self.harness_dir = str(self.temp_dir / "proofs" / "demo" / "target")
        self.target_function = "target"
        self.target_file_path = str(self.temp_dir / "project" / "src" / "target.c")
        self.harness_file_name = "target_harness.c"
        self.harness_file_path = str(Path(self.harness_dir) / self.harness_file_name)
        self.makefile_path = str(Path(self.harness_dir) / "Makefile")
        self.snapshot_dir = str(Path(self.harness_dir) / "snapshots")
        self.args = SimpleNamespace(
            llm_model="gpt-5.3-codex",
            root_dir=self.root_dir,
            harness_path=self.harness_dir,
            target_function_name=self.target_function,
            target_file_path=self.target_file_path,
            metrics_file=None,
        )
        self.project_container = object()
        self.progress = None
        self.full_results = list(full_results or [])
        self.run_make_calls: list[bool] = []

        Path(self.root_dir, "src").mkdir(parents=True, exist_ok=True)
        Path(self.target_file_path).write_text("void target(void) {}\n", encoding="utf-8")
        Path(self.harness_dir).mkdir(parents=True, exist_ok=True)
        Path(self.harness_file_path).write_text("void harness(void) {}\n", encoding="utf-8")
        Path(self.makefile_path).write_text("LINK = baseline.c\n", encoding="utf-8")

    def run_make(self, compile_only: bool = False) -> dict:
        self.run_make_calls.append(compile_only)
        if compile_only:
            return {
                "status": Status.SUCCESS,
                "exit_code": 0,
                "elapsed_seconds": 0.1,
                "stdout": "",
                "stderr": "",
            }

        result = dict(self.full_results.pop(0))
        build_dir = Path(self.harness_dir) / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        report_dir = build_dir / "report" / "json"

        if result.get("report", False):
            report_dir.mkdir(parents=True, exist_ok=True)
            report_dir.joinpath("viewer-property.json").write_text(
                "{}",
                encoding="utf-8",
            )
        elif report_dir.exists():
            shutil.rmtree(report_dir.parent.parent)

        return {
            "status": result.get("status", Status.SUCCESS),
            "exit_code": result.get("exit_code", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0.0),
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


class FakeScopeReducer:
    calls: list[float | None] = []
    results_queue: list[bool] = []

    def __init__(self, agent):
        self.agent = agent

    def reduce_scope(self, time_budget_seconds: float | None) -> bool:
        type(self).calls.append(time_budget_seconds)
        if type(self).results_queue:
            return type(self).results_queue.pop(0)
        return True


class FakeHarnessLLM:
    def chat_llm(
        self,
        system_prompt,
        user_prompt,
        output_format,
        llm_tools=None,
        call_function=None,
        conversation_history=None,
    ):
        return output_format(
            analysis="ok",
            harness_code="void harness(void) {}\n",
        ), {"model_name": "fake"}


class FakeProjectContainer:
    def execute(self, command: str, workdir=None, timeout=None):
        return {
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }


class FakeMakefileGenerator:
    calls = 0
    result = True

    def __init__(self, *args, **kwargs):
        type(self).calls += 1

    def generate(self):
        return type(self).result


class GenerateHarness(InitialHarnessGenerator):
    def __init__(
        self,
        temp_dir: str,
        compile_only_results: list[dict] | None = None,
        verification_results: list[dict] | None = None,
        linked_ok: bool = True,
        called_ok: bool = True,
    ):
        self.temp_dir = Path(temp_dir)
        self.root_dir = str(self.temp_dir / "project")
        self.harness_dir = str(self.temp_dir / "project" / "proofs" / "demo" / "target")
        self.target_function = "target"
        self.target_file_path = str(self.temp_dir / "project" / "src" / "target.c")
        self.harness_file_name = "target_harness.c"
        self.harness_file_path = str(Path(self.harness_dir) / self.harness_file_name)
        self.makefile_path = str(Path(self.harness_dir) / "Makefile")
        self.snapshot_dir = str(Path(self.harness_dir) / "snapshots")
        self.args = SimpleNamespace(
            llm_model="gpt-5.3-codex",
            llm_provider="openai",
            root_dir=self.root_dir,
            harness_path=self.harness_dir,
            target_function_name=self.target_function,
            target_file_path=self.target_file_path,
            metrics_file=None,
            scope_bound=None,
            scope_time_budget=None,
        )
        self.project_container = FakeProjectContainer()
        self.progress = None
        self.llm = FakeHarnessLLM()
        self._max_attempts = 1
        self._compile_only_results = list(compile_only_results or [])
        self._verification_results = list(verification_results or [])
        self._linked_ok = linked_ok
        self._called_ok = called_ok
        self.validation_targets: list[tuple[str, str | None]] = []

        Path(self.root_dir, "src").mkdir(parents=True, exist_ok=True)
        Path(self.target_file_path).write_text(
            "\n".join(
                [
                    "#ifdef FEATURE_FLAG",
                    "void target(void) {}",
                    "#endif",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        Path(self.harness_dir).mkdir(parents=True, exist_ok=True)

    def prepare_prompt(self):
        return "system", "user"

    def get_tools(self):
        return []

    def create_makefile_include(self):
        return None

    def run_make(self, compile_only: bool = False) -> dict:
        if compile_only:
            result = dict(self._compile_only_results.pop(0) if self._compile_only_results else {
                "status": Status.SUCCESS,
                "exit_code": 0,
            })
            return {
                "status": result.get("status", Status.SUCCESS),
                "exit_code": result.get("exit_code", 0),
                "elapsed_seconds": result.get("elapsed_seconds", 0.1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }

        result = dict(self._verification_results.pop(0) if self._verification_results else {
            "status": Status.SUCCESS,
            "exit_code": 0,
        })
        return {
            "status": result.get("status", Status.SUCCESS),
            "exit_code": result.get("exit_code", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0.1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    def validate_linked_target(self, linked_target_function=None) -> bool:
        self.validation_targets.append(("linked", linked_target_function))
        return self._linked_ok

    def validate_called_target(self, called_target_function=None) -> bool:
        self.validation_targets.append(("called", called_target_function))
        return self._called_ok

    def log_task_attempt(self, *args, **kwargs):
        return None

    def log_task_result(self, *args, **kwargs):
        return None

    def log_agent_result(self, *args, **kwargs):
        return None

    def save_status(self, *args, **kwargs):
        return None


class InitialHarnessScopeFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        GENERATION_ORDER.clear()
        FakeStubGenerator.calls = []
        FakeStubGenerator.results_queue = []
        FakeStubGenerator.makefile_suffixes = []
        FakeFunctionPointerHandler.calls = []
        FakeFunctionPointerHandler.results_queue = []
        FakeFunctionPointerHandler.makefile_suffixes = []
        FakeScopeReducer.calls = []
        FakeScopeReducer.results_queue = []
        self.stub_patch = mock.patch(
            "initial_harness_generator.gen_harness.StubGenerator",
            FakeStubGenerator,
        )
        self.fp_patch = mock.patch(
            "initial_harness_generator.gen_harness.FunctionPointerHandler",
            FakeFunctionPointerHandler,
        )
        self.reducer_patch = mock.patch(
            "initial_harness_generator.gen_harness.ScopeReducer",
            FakeScopeReducer,
        )
        self.stub_patch.start()
        self.fp_patch.start()
        self.reducer_patch.start()
        self.addCleanup(self.stub_patch.stop)
        self.addCleanup(self.fp_patch.stop)
        self.addCleanup(self.reducer_patch.stop)

    def test_budgeted_flow_accepts_valid_report_even_when_verification_exit_code_is_nonzero(self):
        harness = HarnessFlowHarness(
            self.temp_dir.name,
            full_results=[
                {"exit_code": 0, "elapsed_seconds": 5.0, "report": True},
                {
                    "status": Status.FAILURE,
                    "exit_code": 10,
                    "elapsed_seconds": 7.0,
                    "report": True,
                },
            ],
        )
        widener = FakeWidener(
            harness,
            step_results=[
                {"outcome": "advanced", "level": 2, "makefile_suffix": "level2\n"},
                {"outcome": "complete", "level": 2},
            ],
        )

        self.assertEqual(
            harness._run_budgeted_scope_widening(
                widener=widener,
                scope_bound=3,
                time_budget_minutes=1.0,
            ),
            2,
        )
        self.assertEqual(FakeStubGenerator.calls, [False, False])
        self.assertEqual(FakeFunctionPointerHandler.calls, [False, False])
        self.assertEqual(FakeScopeReducer.calls, [])
        self.assertEqual(GENERATION_ORDER, ["fp", "stub", "fp", "stub"])
        self.assertEqual(harness.run_make_calls, [False, False])
        self.assertIn("level2", harness.get_makefile())

    def test_budgeted_flow_restores_previous_scope_after_timeout(self):
        FakeFunctionPointerHandler.makefile_suffixes = ["fp\n", "fp2\n"]
        FakeStubGenerator.makefile_suffixes = ["stub\n", "stub2\n"]
        harness = HarnessFlowHarness(
            self.temp_dir.name,
            full_results=[
                {"exit_code": 0, "elapsed_seconds": 5.0, "report": True},
                {
                    "status": Status.TIMEOUT,
                    "exit_code": 124,
                    "elapsed_seconds": 90.0,
                    "report": False,
                },
            ],
        )
        original_makefile = harness.get_makefile()
        widener = FakeWidener(
            harness,
            step_results=[
                {"outcome": "advanced", "level": 2, "makefile_suffix": "level2\n"},
            ],
        )

        self.assertEqual(
            harness._run_budgeted_scope_widening(
                widener=widener,
                scope_bound=2,
                time_budget_minutes=1.0,
            ),
            1,
        )
        self.assertIn("fp\n", widener.entered_makefiles[0])
        self.assertNotIn("stub\n", widener.entered_makefiles[0])
        self.assertEqual(FakeScopeReducer.calls, [])
        self.assertEqual(GENERATION_ORDER, ["fp", "stub", "fp", "stub"])
        self.assertIn("fp\n", harness.get_makefile())
        self.assertIn("stub\n", harness.get_makefile())
        self.assertNotIn("level2\n", harness.get_makefile())
        self.assertNotIn("fp2\n", harness.get_makefile())
        self.assertNotIn("stub2\n", harness.get_makefile())
        self.assertNotEqual(harness.get_makefile(), original_makefile)

    def test_bound_only_flow_defers_model_generation_until_final_scope(self):
        harness = HarnessFlowHarness(
            self.temp_dir.name,
            full_results=[
                {"exit_code": 0, "elapsed_seconds": 1.0, "report": True},
            ],
        )
        widener = FakeWidener(
            harness,
            step_results=[
                {"outcome": "advanced", "level": 2, "makefile_suffix": "level2\n"},
                {"outcome": "complete", "level": 2},
            ],
        )

        self.assertEqual(
            harness._run_bound_only_scope_widening(
                widener=widener,
                scope_bound=3,
            ),
            2,
        )
        self.assertEqual(FakeStubGenerator.calls, [False])
        self.assertEqual(FakeFunctionPointerHandler.calls, [False])
        self.assertEqual(FakeScopeReducer.calls, [])
        self.assertEqual(GENERATION_ORDER, ["fp", "stub"])
        self.assertEqual(harness.run_make_calls, [False])
        self.assertIn("level2", harness.get_makefile())

    def test_budgeted_flow_uses_scope_reducer_before_widening_when_baseline_exceeds_budget(self):
        FakeScopeReducer.results_queue = [True]
        harness = HarnessFlowHarness(
            self.temp_dir.name,
            full_results=[
                {"exit_code": 0, "elapsed_seconds": 90.0, "report": False},
                {"exit_code": 0, "elapsed_seconds": 5.0, "report": True},
                {"exit_code": 0, "elapsed_seconds": 7.0, "report": True},
            ],
        )
        widener = FakeWidener(
            harness,
            step_results=[
                {"outcome": "advanced", "level": 2, "makefile_suffix": "level2\n"},
                {"outcome": "complete", "level": 2},
            ],
        )

        self.assertEqual(
            harness._run_budgeted_scope_widening(
                widener=widener,
                scope_bound=3,
                time_budget_minutes=1.0,
            ),
            2,
        )
        self.assertEqual(FakeScopeReducer.calls, [60.0])
        self.assertIn("level2", harness.get_makefile())

    def test_bound_only_flow_runs_scope_reducer_after_final_timeout(self):
        FakeScopeReducer.results_queue = [True]
        harness = HarnessFlowHarness(
            self.temp_dir.name,
            full_results=[
                {"status": Status.TIMEOUT, "exit_code": 124, "elapsed_seconds": 1800.0, "report": False},
            ],
        )
        widener = FakeWidener(
            harness,
            step_results=[
                {"outcome": "complete", "level": 1},
            ],
        )

        self.assertEqual(
            harness._run_bound_only_scope_widening(
                widener=widener,
                scope_bound=2,
            ),
            1,
        )
        self.assertEqual(FakeScopeReducer.calls, [None])


class CompileCommandsSeedTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        FakeMakefileGenerator.calls = 0
        FakeMakefileGenerator.result = True
        self.makefile_generator_patch = mock.patch(
            "initial_harness_generator.gen_harness.MakefileGenerator",
            FakeMakefileGenerator,
        )
        self.makefile_generator_patch.start()
        self.addCleanup(self.makefile_generator_patch.stop)

    def create_harness(self, **kwargs) -> GenerateHarness:
        return GenerateHarness(self.temp_dir.name, **kwargs)

    def write_compile_commands(self, relative_path: str, entries: list[dict]) -> Path:
        compile_commands_path = Path(self.temp_dir.name) / "project" / relative_path
        compile_commands_path.parent.mkdir(parents=True, exist_ok=True)
        compile_commands_path.write_text(json.dumps(entries), encoding="utf-8")
        return compile_commands_path

    def make_command_entry(self, harness: GenerateHarness, **overrides) -> dict:
        entry = {
            "directory": harness.root_dir,
            "file": harness.target_file_path,
            "command": (
                "cc -DFEATURE_FLAG=1 -DOTHER=2 "
                f"-I{harness.root_dir}/include "
                "-include generated/autoconf.h "
                "-isystem /opt/ext/include "
                f"-c {harness.target_file_path}"
            ),
        }
        entry.update(overrides)
        return entry

    def test_find_compile_commands_files_prefers_root_then_build(self):
        harness = self.create_harness()
        root_path = self.write_compile_commands("compile_commands.json", [])
        build_path = self.write_compile_commands("build/compile_commands.json", [])

        self.assertEqual(
            harness._find_compile_commands_files(),
            [str(root_path), str(build_path)],
        )

    def test_find_first_matching_compile_entry_matches_relative_file_from_build_db(self):
        harness = self.create_harness()
        other_entry = self.make_command_entry(
            harness,
            file="src/other.c",
        )
        target_entry = self.make_command_entry(
            harness,
            file="src/target.c",
            directory=harness.root_dir,
        )
        self.write_compile_commands(
            "build/compile_commands.json",
            [other_entry, target_entry],
        )

        matched_entry = harness._find_first_matching_compile_entry()

        self.assertEqual(matched_entry, target_entry)

    def test_find_first_matching_compile_entry_uses_first_match_only(self):
        harness = self.create_harness()
        first_entry = self.make_command_entry(harness, command="cc -DFIRST=1 -c " + harness.target_file_path)
        second_entry = self.make_command_entry(harness, command="cc -DSECOND=1 -c " + harness.target_file_path)
        self.write_compile_commands("compile_commands.json", [first_entry, second_entry])

        matched_entry = harness._find_first_matching_compile_entry()

        self.assertEqual(matched_entry, first_entry)

    def test_extract_compile_command_seed_supports_command_and_path_rewriting(self):
        harness = self.create_harness()
        entry = self.make_command_entry(harness)

        h_def_flags, h_inc_flags = harness._extract_compile_command_seed(entry)

        self.assertEqual(h_def_flags, ["-DFEATURE_FLAG=1", "-DOTHER=2"])
        self.assertEqual(
            h_inc_flags,
            [
                "-I$(ROOT)/include",
                "-include",
                "$(ROOT)/generated/autoconf.h",
                "-isystem",
                "/opt/ext/include",
            ],
        )

    def test_extract_compile_command_seed_supports_arguments_format(self):
        harness = self.create_harness()
        entry = {
            "directory": harness.root_dir,
            "file": "src/target.c",
            "arguments": [
                "cc",
                "-D",
                "FEATURE_FLAG=1",
                "-U",
                "LEGACY_FLAG",
                "-I",
                "include",
                "-iquote",
                "quotes",
                "-c",
                harness.target_file_path,
            ],
        }

        h_def_flags, h_inc_flags = harness._extract_compile_command_seed(entry)

        self.assertEqual(
            h_def_flags,
            ["-D", "FEATURE_FLAG=1", "-U", "LEGACY_FLAG"],
        )
        self.assertEqual(
            h_inc_flags,
            ["-I", "$(ROOT)/include", "-iquote", "$(ROOT)/quotes"],
        )

    def test_setup_initial_makefile_suppresses_duplicate_heuristic_macros(self):
        harness = self.create_harness()

        makefile = harness.setup_initial_makefile(
            ["FEATURE_FLAG", "SECOND_FLAG"],
            seeded_h_def_flags=["-DFEATURE_FLAG=1"],
            seeded_h_inc_flags=["-I$(ROOT)/include"],
        )

        self.assertIn("H_DEF = -DFEATURE_FLAG=1 -DSECOND_FLAG=1", makefile)
        self.assertNotIn("-DFEATURE_FLAG=1 -DFEATURE_FLAG=1", makefile)
        self.assertIn("H_INC = -I$(ROOT) -I$(ROOT)/include", makefile)

    def test_generate_skips_makefile_generator_after_seeded_compile_success(self):
        harness = self.create_harness(
            compile_only_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
            verification_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
        )
        self.write_compile_commands(
            "compile_commands.json",
            [self.make_command_entry(harness)],
        )

        self.assertTrue(harness.generate())
        self.assertEqual(FakeMakefileGenerator.calls, 0)
        self.assertEqual(
            harness.validation_targets,
            [("linked", "target"), ("called", "target")],
        )
        makefile_content = Path(harness.makefile_path).read_text(encoding="utf-8")
        self.assertIn("-DFEATURE_FLAG=1", makefile_content)
        self.assertIn("-include $(ROOT)/generated/autoconf.h", makefile_content)

    def test_generate_falls_back_to_makefile_generator_after_seeded_compile_failure(self):
        harness = self.create_harness(
            compile_only_results=[
                {"status": Status.FAILURE, "exit_code": 2, "stderr": "compile failed"},
            ],
            verification_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
        )
        self.write_compile_commands(
            "compile_commands.json",
            [self.make_command_entry(harness)],
        )

        self.assertTrue(harness.generate())
        self.assertEqual(FakeMakefileGenerator.calls, 1)
        self.assertEqual(harness.validation_targets, [])

    def test_generate_falls_back_after_first_matching_entry_validation_failure(self):
        harness = self.create_harness(
            compile_only_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
            verification_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
            linked_ok=False,
            called_ok=True,
        )
        first_entry = self.make_command_entry(harness, command="cc -DFIRST=1 -c " + harness.target_file_path)
        second_entry = self.make_command_entry(harness, command="cc -DSECOND=1 -c " + harness.target_file_path)
        self.write_compile_commands("compile_commands.json", [first_entry, second_entry])

        self.assertTrue(harness.generate())
        self.assertEqual(FakeMakefileGenerator.calls, 1)
        makefile_content = Path(harness.makefile_path).read_text(encoding="utf-8")
        self.assertIn("-DFIRST=1", makefile_content)
        self.assertNotIn("-DSECOND=1", makefile_content)

    def test_generate_without_compile_commands_uses_current_flow(self):
        harness = self.create_harness(
            verification_results=[
                {"status": Status.SUCCESS, "exit_code": 0},
            ],
        )

        self.assertTrue(harness.generate())
        self.assertEqual(FakeMakefileGenerator.calls, 1)


if __name__ == "__main__":
    unittest.main()
