import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
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

import autoup_vscode


class VSCodeModelValidationTests(unittest.TestCase):
    def test_validate_model_reports_resolved_provider(self):
        args = SimpleNamespace(model="openrouter/deepseek/deepseek-r1", provider="openrouter")

        with mock.patch("autoup_vscode.resolve_model_provider", return_value="openrouter"):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = autoup_vscode.validate_model(args)

        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {
            "ok": True,
            "resolved_provider": "openrouter",
        })

    def test_validate_model_returns_structured_error(self):
        args = SimpleNamespace(model="unsupported/demo", provider="custom")

        with mock.patch("autoup_vscode.resolve_model_provider", side_effect=ValueError("bad model")):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = autoup_vscode.validate_model(args)

        self.assertEqual(exit_code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "bad model")


if __name__ == "__main__":
    unittest.main()
