import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        ])

        self.assertIsNone(args.scope_bound)
        self.assertEqual(args.scope_time_budget, 2.5)

    def test_batch_runner_parser_defaults_scope_controls_to_none(self):
        parser = batch_run_tests.build_parser()
        args = parser.parse_args([
            "cases.json",
            "--proof_dir", "proofs",
        ])

        self.assertIsNone(args.scope_bound)
        self.assertIsNone(args.scope_time_budget)

    def test_build_run_command_omits_unset_scope_bound_and_forwards_budget(self):
        args = SimpleNamespace(
            base_dir="/tmp/project",
            mode="harness",
            container_engine="docker",
            scope_bound=None,
            scope_time_budget=3.0,
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

    def test_build_run_command_forwards_scope_bound_when_present(self):
        args = SimpleNamespace(
            base_dir="/tmp/project",
            mode="harness",
            container_engine="docker",
            scope_bound=4,
            scope_time_budget=None,
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


if __name__ == "__main__":
    unittest.main()
