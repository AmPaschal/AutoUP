import sys
import tempfile
import types
import unittest
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

import agent
import run as autoup_run


class AgentModelRoutingTests(unittest.TestCase):
    def make_args(self, **overrides):
        base = {
            "root_dir": "/tmp/project",
            "harness_path": "/tmp/project/proof",
            "target_function_name": "demo",
            "target_file_path": "/tmp/project/demo.c",
            "metrics_file": None,
            "llm_model": "gpt-5.3-codex",
            "llm_provider": None,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_ai_agent_uses_gpt_for_openai_provider(self):
        args = self.make_args(llm_provider="openai")
        with mock.patch("agent.GPT") as mock_gpt, mock.patch("agent.LiteLLM") as mock_litellm:
            ai_agent = agent.AIAgent("demo", args, project_container=None)

        self.assertIsNotNone(ai_agent.llm)
        mock_gpt.assert_called_once_with(name="gpt-5.3-codex", max_input_tokens=270000)
        mock_litellm.assert_not_called()

    def test_ai_agent_uses_litellm_for_non_openai_provider(self):
        args = self.make_args(llm_model="openrouter/deepseek/deepseek-r1")
        with mock.patch("agent.resolve_model_provider", return_value="openrouter"), \
             mock.patch("agent.GPT") as mock_gpt, \
             mock.patch("agent.LiteLLM") as mock_litellm:
            ai_agent = agent.AIAgent("demo", args, project_container=None)

        self.assertEqual(args.llm_provider, "openrouter")
        self.assertIsNotNone(ai_agent.llm)
        mock_gpt.assert_not_called()
        mock_litellm.assert_called_once_with(name="openrouter/deepseek/deepseek-r1", max_input_tokens=270000)

    def test_run_with_args_resolves_provider_for_direct_cli(self):
        temp_root = tempfile.mkdtemp(prefix="autoup-routing-")
        args = SimpleNamespace(
            mode="all",
            target_function_name="demo",
            root_dir=temp_root,
            harness_path=temp_root,
            target_file_path=f"{temp_root}/demo.c",
            log_file=None,
            metrics_file=None,
            container_engine="docker",
            llm_model="openrouter/deepseek/deepseek-r1",
            llm_provider=None,
            scope_bound=1,
            scope_time_budget=None,
            make_timeout=1800,
            job_id=None,
            execution_host=None,
        )

        fake_container = mock.Mock()
        fake_logger = mock.Mock()
        with mock.patch("run.resolve_model_provider", return_value="openrouter"), \
             mock.patch("run.init_logging"), \
             mock.patch("run.setup_logger", return_value=fake_logger), \
             mock.patch("run.DockerProjectContainer", return_value=fake_container), \
             mock.patch("run.process_mode", return_value=True), \
             mock.patch("run.teardown_runtime"):
            result = autoup_run.run_with_args(args)

        self.assertTrue(result)
        self.assertEqual(args.llm_provider, "openrouter")
        fake_container.initialize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
