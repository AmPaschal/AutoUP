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
docker_errors_stub.DockerException = type("FakeDockerException", (Exception,), {})
docker_errors_stub.BuildError = type("FakeBuildError", (Exception,), {})
docker_errors_stub.APIError = type("FakeAPIError", (Exception,), {})
docker_models_stub = types.ModuleType("docker.models")
docker_models_containers_stub = types.ModuleType("docker.models.containers")
docker_models_containers_stub.Container = object
sys.modules.setdefault("docker", docker_stub)
sys.modules.setdefault("docker.errors", docker_errors_stub)
sys.modules.setdefault("docker.models", docker_models_stub)
sys.modules.setdefault("docker.models.containers", docker_models_containers_stub)

from commons.docker_tool import DockerProjectContainer  # noqa: E402

FakeAPIError = docker_errors_stub.APIError


class FakeRunningContainer:
    def __init__(self):
        self.exec_calls = []
        self.stop_calls = []
        self.remove_calls = []
        self.stop_error = None
        self.remove_error = None

    def exec_run(self, args, user=None):
        self.exec_calls.append({
            "args": args,
            "user": user,
        })
        return types.SimpleNamespace(exit_code=0, output=(b"", b""))

    def stop(self, timeout=None):
        self.stop_calls.append({"timeout": timeout})
        if self.stop_error is not None:
            raise self.stop_error

    def remove(self, force=False):
        self.remove_calls.append({"force": force})
        if self.remove_error is not None:
            raise self.remove_error


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

    def test_terminate_forces_removal_when_clean_stop_fails(self):
        container = DockerProjectContainer(
            "container/tools.Dockerfile",
            "/tmp/project",
            "autoup_test",
        )
        running = FakeRunningContainer()
        running.stop_error = FakeAPIError("daemon hiccup")
        container.container = running

        container.terminate()

        self.assertEqual(running.stop_calls, [{"timeout": 10}])
        self.assertEqual(running.remove_calls, [{"force": True}])
        self.assertIsNone(container.container)

    def test_terminate_ignores_missing_container_during_remove(self):
        container = DockerProjectContainer(
            "container/tools.Dockerfile",
            "/tmp/project",
            "autoup_test",
        )
        running = FakeRunningContainer()
        running.remove_error = FakeAPIError("404 no such container")
        container.container = running

        container.terminate()

        self.assertEqual(running.stop_calls, [{"timeout": 10}])
        self.assertEqual(running.remove_calls, [{"force": True}])
        self.assertIsNone(container.container)


if __name__ == "__main__":
    unittest.main()
