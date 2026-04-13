import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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

from commons.apptainer_tool import (  # noqa: E402
    ApptainerProjectContainer,
    IMAGE_FILE,
    IMAGE_LOCK_FILE,
)
from commons.container_constants import CSCOPE_INIT_TIMEOUT, DEFAULT_CONTAINER_USER  # noqa: E402


class ApptainerToolTests(unittest.TestCase):
    def setUp(self):
        self.host_dir_ctx = tempfile.TemporaryDirectory()
        self.repo_dir_ctx = tempfile.TemporaryDirectory()
        self.host_dir = self.host_dir_ctx.name
        self.repo_dir = self.repo_dir_ctx.name
        self.container = ApptainerProjectContainer(
            "container/tools.def",
            self.host_dir,
            repo_dir=self.repo_dir,
        )

    def tearDown(self):
        self.host_dir_ctx.cleanup()
        self.repo_dir_ctx.cleanup()

    @mock.patch("commons.apptainer_tool.shutil.which", return_value="/usr/bin/apptainer")
    @mock.patch("commons.apptainer_tool.os.path.getmtime")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    @mock.patch("commons.apptainer_tool.os.path.isdir")
    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_initialize_starts_bound_instance_and_provisions_user(
        self,
        mock_run,
        mock_isdir,
        mock_exists,
        mock_getmtime,
        _mock_which,
    ):
        host_dir = self.host_dir
        uid = self.container.uid
        side_effects = [
            self._completed(["apptainer", "--version"], 0, stdout="apptainer 1.0.0\n"),
            self._completed(["apptainer", "instance", "start"], 0),
            self._completed(["apptainer", "exec"], 0, stdout=f"{host_dir}\n"),
            self._completed(["apptainer", "exec"], 0, stdout=f"{DEFAULT_CONTAINER_USER}\n"),
            self._completed(["apptainer", "exec"], 0, stdout=""),
            self._completed(["apptainer", "exec"], 0, stdout=f"{uid}\n"),
            self._completed(["apptainer", "exec"], 0, stdout="/usr/bin/cscope\n"),
            self._completed(["apptainer", "exec"], 0, stdout=""),
        ]
        mock_run.side_effect = side_effects
        mock_isdir.return_value = True
        mock_exists.side_effect = lambda path: True
        mock_getmtime.side_effect = lambda path: 10

        self.container.initialize()

        calls = mock_run.call_args_list
        start_cmd = calls[1].args[0]
        self.assertEqual(start_cmd[:4], ["apptainer", "instance", "start", "--fakeroot"])
        self.assertIn("--writable-tmpfs", start_cmd)
        self.assertEqual(start_cmd.count("--bind"), 2)
        self.assertIn(f"{host_dir}:{host_dir}", start_cmd)
        self.assertIn(f"{self.repo_dir}:{self.repo_dir}", start_cmd)
        self.assertEqual(start_cmd[-2], IMAGE_FILE)
        self.assertEqual(start_cmd[-1], self.container.instance_name)

        sudo_setup_cmd = calls[4].args[0]
        self.assertIn("/etc/sudoers.d", sudo_setup_cmd[-1])
        self.assertIn(DEFAULT_CONTAINER_USER, sudo_setup_cmd[-1])

        exec_cmd = calls[-1].args[0]
        self.assertEqual(exec_cmd[0:3], ["apptainer", "exec", "--pwd"])
        self.assertIn("su", exec_cmd)
        self.assertIn(DEFAULT_CONTAINER_USER, exec_cmd)
        self.assertIn(f"timeout {CSCOPE_INIT_TIMEOUT}s", exec_cmd[-1])

    @mock.patch("commons.apptainer_tool.shutil.which", return_value="/usr/bin/apptainer")
    @mock.patch("commons.apptainer_tool.os.path.getmtime")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    @mock.patch("commons.apptainer_tool.os.path.isdir")
    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_initialize_raises_on_bind_or_workdir_failure(
        self,
        mock_run,
        mock_isdir,
        mock_exists,
        mock_getmtime,
        _mock_which,
    ):
        mock_run.side_effect = [
            self._completed(["apptainer", "--version"], 0, stdout="apptainer 1.0.0\n"),
            self._completed(["apptainer", "instance", "start"], 0),
            self._completed(
                ["apptainer", "exec"],
                255,
                stderr="FATAL:   failed to set working directory: chdir /missing: no such file or directory\n",
            ),
            self._completed(["apptainer", "instance", "stop"], 0),
        ]
        mock_isdir.return_value = True
        mock_exists.side_effect = lambda path: True
        mock_getmtime.side_effect = lambda path: 10

        with self.assertRaisesRegex(RuntimeError, "bind/workdir failure"):
            self.container.initialize()

    @mock.patch("commons.apptainer_tool.shutil.which", return_value="/usr/bin/apptainer")
    @mock.patch("commons.apptainer_tool.os.path.getmtime")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    @mock.patch("commons.apptainer_tool.os.path.isdir")
    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_initialize_raises_when_sudo_setup_fails(
        self,
        mock_run,
        mock_isdir,
        mock_exists,
        mock_getmtime,
        _mock_which,
    ):
        mock_run.side_effect = [
            self._completed(["apptainer", "--version"], 0, stdout="apptainer 1.0.0\n"),
            self._completed(["apptainer", "instance", "start"], 0),
            self._completed(["apptainer", "exec"], 0, stdout=f"{self.host_dir}\n"),
            self._completed(["apptainer", "exec"], 0, stdout=f"{DEFAULT_CONTAINER_USER}\n"),
            self._completed(["apptainer", "exec"], 1, stderr="permission denied\n"),
            self._completed(["apptainer", "instance", "stop"], 0),
        ]
        mock_isdir.return_value = True
        mock_exists.side_effect = lambda path: True
        mock_getmtime.side_effect = lambda path: 10

        with self.assertRaisesRegex(RuntimeError, "configure sudo"):
            self.container.initialize()

    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_execute_targets_running_instance_as_provisioned_user(self, mock_run):
        self.container.instance_name = "autoup_test"
        self.container.user_name = DEFAULT_CONTAINER_USER
        mock_run.return_value = self._completed(["apptainer", "exec"], 0, stdout="ok\n")

        result = self.container.execute("printf ok", workdir="/", timeout=17)

        self.assertEqual(result["stdout"], "ok\n")
        exec_cmd = mock_run.call_args.args[0]
        self.assertEqual(exec_cmd[:4], ["apptainer", "exec", "--pwd", "/"])
        self.assertEqual(exec_cmd[4], "instance://autoup_test")
        self.assertIn("su", exec_cmd)
        self.assertIn(DEFAULT_CONTAINER_USER, exec_cmd)
        self.assertIn("timeout 17s", exec_cmd[-1])

    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_execute_runs_sudo_prefixed_commands_as_root(self, mock_run):
        self.container.instance_name = "autoup_test"
        self.container.user_name = DEFAULT_CONTAINER_USER
        mock_run.return_value = self._completed(["apptainer", "exec"], 0, stdout="0\n")

        result = self.container.execute("sudo id -u", workdir="/", timeout=11)

        self.assertEqual(result["stdout"], "0\n")
        exec_cmd = mock_run.call_args.args[0]
        self.assertEqual(exec_cmd[:4], ["apptainer", "exec", "--pwd", "/"])
        self.assertEqual(exec_cmd[4], "instance://autoup_test")
        self.assertNotIn("su", exec_cmd)
        self.assertEqual(exec_cmd[-5:-2], ["timeout", "11s", "bash"])
        self.assertEqual(exec_cmd[-1], "sudo id -u")

    @mock.patch("commons.apptainer_tool.shutil.which", return_value="/usr/bin/apptainer")
    @mock.patch("commons.apptainer_tool.os.path.getmtime")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    @mock.patch("commons.apptainer_tool.os.path.isdir")
    @mock.patch("commons.apptainer_tool.subprocess.run")
    def test_initialize_skips_cscope_when_missing(
        self,
        mock_run,
        mock_isdir,
        mock_exists,
        mock_getmtime,
        _mock_which,
    ):
        mock_run.side_effect = [
            self._completed(["apptainer", "--version"], 0, stdout="apptainer 1.0.0\n"),
            self._completed(["apptainer", "instance", "start"], 0),
            self._completed(["apptainer", "exec"], 0, stdout=f"{self.host_dir}\n"),
            self._completed(["apptainer", "exec"], 0, stdout=f"{DEFAULT_CONTAINER_USER}\n"),
            self._completed(["apptainer", "exec"], 0, stdout=""),
            self._completed(["apptainer", "exec"], 0, stdout=f"{self.container.uid}\n"),
            self._completed(["apptainer", "exec"], 1, stdout=""),
        ]
        mock_isdir.return_value = True
        mock_exists.side_effect = lambda path: True
        mock_getmtime.side_effect = lambda path: 10

        self.container.initialize()

        self.assertEqual(mock_run.call_count, 7)

    @mock.patch("commons.apptainer_tool.os.path.getmtime")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    def test_stale_image_rebuilds_when_dependency_is_newer(self, mock_exists, mock_getmtime):
        mock_exists.return_value = True
        mtimes = {
            os.path.abspath(IMAGE_FILE): 10,
            os.path.abspath("container/tools.def"): 20,
            os.path.abspath("container/Makefile.include"): 9,
            os.path.abspath("container/general-stubs.c"): 9,
            os.path.abspath("container/zephyr-stubs.c"): 9,
        }
        mock_getmtime.side_effect = lambda path: mtimes[os.path.abspath(path)]

        self.assertFalse(self.container._ApptainerProjectContainer__image_is_fresh())

    @mock.patch("commons.apptainer_tool.FileLock")
    @mock.patch("commons.apptainer_tool.os.path.exists")
    def test_build_image_waits_for_lock_and_rechecks_freshness(self, mock_exists, mock_lock):
        mock_exists.return_value = True
        lock_instance = mock.MagicMock()
        mock_lock.return_value = lock_instance

        with mock.patch.object(
            self.container,
            "_ApptainerProjectContainer__image_is_fresh",
            side_effect=[False, True],
        ) as mock_fresh, mock.patch.object(
            self.container,
            "_ApptainerProjectContainer__run_subprocess",
        ) as mock_run:
            self.container._ApptainerProjectContainer__build_image()

        mock_lock.assert_called_once_with(os.path.abspath(IMAGE_LOCK_FILE))
        lock_instance.__enter__.assert_called_once()
        lock_instance.__exit__.assert_called_once()
        self.assertEqual(mock_fresh.call_count, 2)
        mock_run.assert_not_called()

    @staticmethod
    def _completed(args, returncode, stdout="", stderr=""):
        return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


@unittest.skipUnless(
    shutil.which("apptainer") and os.environ.get("AUTOUP_RUN_APPTAINER_INTEGRATION") == "1",
    "Apptainer integration test requires Apptainer and AUTOUP_RUN_APPTAINER_INTEGRATION=1",
)
class ApptainerIntegrationTests(unittest.TestCase):
    def test_instance_allows_user_owned_files_and_sudo(self):
        from commons.apptainer_tool import ApptainerProjectContainer

        with tempfile.TemporaryDirectory() as host_dir:
            repo_dir = str(Path(__file__).resolve().parents[1])
            container = ApptainerProjectContainer(
                "container/tools.def",
                host_dir,
                repo_dir=repo_dir,
            )
            try:
                container.initialize()
                user_file = os.path.join(host_dir, "user-owned.txt")
                result = container.execute(f"printf hello > {user_file}")
                self.assertEqual(result["exit_code"], 0)
                self.assertTrue(os.path.exists(user_file))
                self.assertEqual(os.stat(user_file).st_uid, os.getuid())

                sudo_result = container.execute("sudo touch /tmp/autoup-apptainer-sudo-test")
                self.assertEqual(sudo_result["exit_code"], 0)
            finally:
                container.terminate()

    def test_container_script_starts_without_missing_clang_module(self):
        from commons.apptainer_tool import ApptainerProjectContainer

        with tempfile.TemporaryDirectory() as host_dir:
            repo_dir = str(Path(__file__).resolve().parents[1])
            container = ApptainerProjectContainer(
                "container/tools.def",
                host_dir,
                repo_dir=repo_dir,
            )
            try:
                container.initialize()
                script_path = os.path.join(
                    repo_dir,
                    "src",
                    "stub_generator",
                    "find_function_pointers.py",
                )
                result = container.execute(
                    f"python3 {script_path} /tmp/missing.c demo /tmp/missing.makefile",
                    workdir=host_dir,
                    timeout=20,
                )
                self.assertNotIn("ModuleNotFoundError: No module named 'clang'", result["stderr"])
            finally:
                container.terminate()
