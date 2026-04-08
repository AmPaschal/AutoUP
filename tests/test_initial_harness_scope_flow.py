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
        self.stub_patch = mock.patch(
            "initial_harness_generator.gen_harness.StubGenerator",
            FakeStubGenerator,
        )
        self.fp_patch = mock.patch(
            "initial_harness_generator.gen_harness.FunctionPointerHandler",
            FakeFunctionPointerHandler,
        )
        self.stub_patch.start()
        self.fp_patch.start()
        self.addCleanup(self.stub_patch.stop)
        self.addCleanup(self.fp_patch.stop)

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
        self.assertEqual(GENERATION_ORDER, ["fp", "stub", "fp", "stub"])
        self.assertIn("fp\n", harness.get_makefile())
        self.assertIn("stub\n", harness.get_makefile())
        self.assertNotIn("level2\n", harness.get_makefile())
        self.assertNotIn("fp2\n", harness.get_makefile())
        self.assertNotIn("stub2\n", harness.get_makefile())
        self.assertNotEqual(harness.get_makefile(), original_makefile)

    def test_bound_only_flow_defers_model_generation_until_final_scope(self):
        harness = HarnessFlowHarness(self.temp_dir.name)
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
        self.assertEqual(GENERATION_ORDER, ["fp", "stub"])
        self.assertEqual(harness.run_make_calls, [])
        self.assertIn("level2", harness.get_makefile())


if __name__ == "__main__":
    unittest.main()
