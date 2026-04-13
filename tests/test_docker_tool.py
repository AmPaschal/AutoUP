import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

from commons.docker_tool import DockerProjectContainer  # noqa: E402


class FakeRunningContainer:
    def __init__(self):
        self.exec_calls = []

    def exec_run(self, args, user=None):
        self.exec_calls.append({
            "args": args,
            "user": user,
        })
        return types.SimpleNamespace(exit_code=0, output=(b"", b""))


class FakeContainersClient:
    def __init__(self):
        self.last_run_kwargs = None
        self.running_container = FakeRunningContainer()

    def run(self, image, **kwargs):
        self.last_run_kwargs = {"image": image, **kwargs}
        return self.running_container


class FakeDockerClient:
    def __init__(self):
        self.containers = FakeContainersClient()


class DockerToolTests(unittest.TestCase):
    def test_start_container_binds_host_and_repo_directories(self):
        with tempfile.TemporaryDirectory() as host_dir, tempfile.TemporaryDirectory() as repo_dir:
            container = DockerProjectContainer(
                "container/tools.Dockerfile",
                host_dir,
                "autoup_test",
                repo_dir=repo_dir,
            )
            container.client = FakeDockerClient()
            container.image = "autoup_image:latest"

            container.start_container()

            run_kwargs = container.client.containers.last_run_kwargs
            self.assertIsNotNone(run_kwargs)
            self.assertEqual(run_kwargs["working_dir"], host_dir)
            self.assertIn(host_dir, run_kwargs["volumes"])
            self.assertIn(repo_dir, run_kwargs["volumes"])
            self.assertEqual(run_kwargs["volumes"][host_dir]["bind"], host_dir)
            self.assertEqual(run_kwargs["volumes"][repo_dir]["bind"], repo_dir)
            self.assertEqual(len(container.client.containers.running_container.exec_calls), 1)


if __name__ == "__main__":
    unittest.main()
