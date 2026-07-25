import sys
import tempfile
import types
import unittest
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

litellm_stub = types.ModuleType("litellm")
litellm_stub.ModelResponse = object
litellm_stub.get_llm_provider = lambda name: (None, "openai")
sys.modules.setdefault("litellm", litellm_stub)

docker_stub = types.ModuleType("docker")
docker_stub.from_env = lambda: None
docker_errors_stub = types.ModuleType("docker.errors")
docker_errors_stub.DockerException = Exception
docker_errors_stub.BuildError = Exception
docker_errors_stub.APIError = Exception
docker_models_stub = types.ModuleType("docker.models")
docker_models_containers_stub = types.ModuleType("docker.models.containers")
docker_models_containers_stub.Container = object
sys.modules.setdefault("docker", docker_stub)
sys.modules.setdefault("docker.errors", docker_errors_stub)
sys.modules.setdefault("docker.models", docker_models_stub)
sys.modules.setdefault("docker.models.containers", docker_models_containers_stub)

import run as autoup_run
import autoup_vscode
from tests import run_tests as batch_run_tests


class ScopeCliTests(unittest.TestCase):
    def test_src_run_parser_accepts_optional_scope_controls(self):
        parser = autoup_run.build_parser()
        args = parser.parse_args([
            "harness",
            "--target_function_name", "demo",
            "--root_dir", "/tmp/project",
            "--harness_path", "/tmp/proof",
            "--target_file_path", "/tmp/project/demo.c",
            "--scope_time_budget", "2.5",
            "--scope_reducer_attempts", "8",
            "--make-timeout", "900",
        ])

        self.assertEqual(args.scope_bound, 1)
        self.assertEqual(args.scope_time_budget, 2.5)
        self.assertEqual(args.scope_reducer_attempts, 8)
        self.assertEqual(args.make_timeout, 900)

    def test_batch_runner_parser_defaults_scope_controls_to_none(self):
        parser = batch_run_tests.build_parser()
        args = parser.parse_args([
            "cases.json",
            "--proof_dir", "proofs",
        ])

        self.assertIsNone(args.scope_bound)
        self.assertIsNone(args.scope_time_budget)
        self.assertEqual(args.scope_reducer_attempts, 10)
        self.assertEqual(args.make_timeout, 1800)

    def test_build_run_command_omits_unset_scope_bound_and_forwards_budget(self):
        args = SimpleNamespace(
            base_dir="/tmp/project",
            mode="harness",
            container_engine="docker",
            scope_bound=None,
            scope_time_budget=3.0,
            scope_reducer_attempts=6,
            make_timeout=1200,
        )
        entry = {
            "function_name": "demo",
            "source_file": "/tmp/project/src/demo.c",
        }
        cmd = batch_run_tests.build_run_command(
            entry=entry,
            args=args,
            metrics_file=Path("/tmp/metrics.jsonl"),
            proof_dir=Path("/tmp/proofs/demo"),
        )

        self.assertNotIn("--scope_bound", cmd)
        self.assertIn("--scope_time_budget", cmd)
        self.assertIn("3.0", cmd)
        self.assertIn("--scope_reducer_attempts", cmd)
        self.assertIn("6", cmd)
        self.assertIn("--make_timeout", cmd)
        self.assertIn("1200", cmd)

    def test_build_run_command_forwards_scope_bound_when_present(self):
        args = SimpleNamespace(
            base_dir="/tmp/project",
            mode="harness",
            container_engine="docker",
            scope_bound=4,
            scope_time_budget=None,
            scope_reducer_attempts=10,
            make_timeout=1800,
        )
        entry = {
            "function_name": "demo",
            "source_file": "/tmp/project/src/demo.c",
        }
        cmd = batch_run_tests.build_run_command(
            entry=entry,
            args=args,
            metrics_file=Path("/tmp/metrics.jsonl"),
            proof_dir=Path("/tmp/proofs/demo"),
        )

        self.assertIn("--scope_bound", cmd)
        self.assertIn("4", cmd)
        self.assertNotIn("--scope_time_budget", cmd)
        self.assertIn("--scope_reducer_attempts", cmd)
        self.assertIn("--make_timeout", cmd)
        self.assertIn("1800", cmd)

    def test_vscode_bridge_defaults_scope_bound_to_one_and_make_timeout_to_1800(self):
        parser = autoup_vscode.build_parser()
        args = parser.parse_args([
            "start",
            "--workspace", "/tmp/project",
            "--source", "/tmp/project/demo.c",
            "--line", "12",
            "--column", "3",
            "--proof-dir", "/tmp/proof",
            "--job-id", "job-1",
            "--function-name", "demo",
        ])

        self.assertEqual(args.scope_bound, 1)
        self.assertEqual(args.scope_reducer_attempts, 10)
        self.assertEqual(args.make_timeout, 1800)

        run_args = autoup_vscode.build_run_args(args)
        self.assertEqual(run_args.scope_bound, 1)
        self.assertEqual(run_args.scope_reducer_attempts, 10)
        self.assertEqual(run_args.make_timeout, 1800)

    def test_all_mode_skips_standalone_model_generation_agents(self):
        created_agents: list[str] = []

        class FakeInitialHarnessGenerator:
            def __init__(self, *args, **kwargs):
                created_agents.append(type(self).__name__)

            def generate(self):
                return True

        class FakeCoverageDebugger:
            def __init__(self, *args, **kwargs):
                created_agents.append(type(self).__name__)

            def generate(self):
                return True

        class FakeProofDebugger:
            def __init__(self, *args, **kwargs):
                created_agents.append(type(self).__name__)

            def generate(self):
                return True

        class FakeVulnAwareRefiner:
            def __init__(self, *args, **kwargs):
                created_agents.append(type(self).__name__)

            def generate(self):
                return True

        with tempfile.NamedTemporaryFile() as metrics_file:
            args = SimpleNamespace(
                mode="all",
                harness_path="/tmp/proof",
                root_dir="/tmp/project",
                target_function_name="demo",
                target_file_path="/tmp/project/demo.c",
                metrics_file=metrics_file.name,
            )

            with mock.patch.object(autoup_run, "InitialHarnessGenerator", FakeInitialHarnessGenerator), \
                 mock.patch.object(autoup_run, "CoverageDebugger", FakeCoverageDebugger), \
                 mock.patch.object(autoup_run, "VulnAwareRefiner", FakeVulnAwareRefiner), \
                 mock.patch.object(autoup_run, "ProofDebugger", FakeProofDebugger), \
                 mock.patch.object(autoup_run, "StubGenerator", side_effect=AssertionError("StubGenerator should not run in all mode")), \
                 mock.patch.object(autoup_run, "FunctionPointerHandler", side_effect=AssertionError("FunctionPointerHandler should not run in all mode")):
                autoup_run.project_container = object()
                autoup_run.process_mode(args)

        self.assertEqual(
            created_agents,
            [
                "FakeInitialHarnessGenerator",
                "FakeCoverageDebugger",
                "FakeVulnAwareRefiner",
                "FakeProofDebugger",
            ],
        )

    def test_run_with_args_terminates_container_on_success(self):
        args = SimpleNamespace(
            container_engine="docker",
            root_dir="/tmp/project",
            log_file=None,
            metrics_file=None,
            vscode_progress=SimpleNamespace(enabled=False),
        )
        fake_container = mock.Mock()

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "demo-key"}, clear=False), \
             mock.patch.object(autoup_run, "load_dotenv"), \
             mock.patch.object(autoup_run, "init_logging"), \
             mock.patch.object(autoup_run, "setup_logger"), \
             mock.patch.object(autoup_run, "process_mode", return_value=True), \
             mock.patch.object(autoup_run, "DockerProjectContainer", return_value=fake_container):
            succeeded = autoup_run.run_with_args(args)

        self.assertTrue(succeeded)
        fake_container.initialize.assert_called_once()
        fake_container.terminate.assert_called_once()
        self.assertIsNone(autoup_run.project_container)
        self.assertIsNone(autoup_run.current_progress)

    def test_cleanup_marks_job_cancelled_and_exits_with_signal_status(self):
        progress = mock.Mock(enabled=True)
        container = mock.Mock()
        autoup_run.current_progress = progress
        autoup_run.project_container = container

        with mock.patch.object(autoup_run, "setup_logger"):
            with self.assertRaises(SystemExit) as ctx:
                autoup_run.cleanup(signal.SIGTERM, None)

        self.assertEqual(ctx.exception.code, 128 + signal.SIGTERM)
        progress.job_cancelled.assert_called_once()
        container.terminate.assert_called_once()
        self.assertIsNone(autoup_run.project_container)
        self.assertIsNone(autoup_run.current_progress)


if __name__ == "__main__":
    unittest.main()
