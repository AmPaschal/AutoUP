"""Apptainer project container management"""

# System
from typing import Optional
import subprocess
import os
import re
import shlex
import shutil
import uuid

# Utils
from filelock import FileLock, Timeout

# AutoUP
from commons.project_container import ProjectContainer
from commons.container_constants import CSCOPE_INIT_TIMEOUT, DEFAULT_CONTAINER_USER
from logger import setup_logger

logger = setup_logger(__name__)

IMAGE_FILE = "tools.sif"
INSTANCE_PREFIX = "autoup"
APPTAINER_FATAL_PATTERNS = {
    "bind/workdir failure": (
        "failed to set working directory",
        "failed to mount",
        "mount hook function failure",
    ),
    "instance startup failure": (
        "container creation failed",
        "instance is not running",
        "no instance found",
        "instance not found",
        "failed to start instance",
    ),
    "command execution failure": (
        "could not open image",
        "while opening image",
        "failed to open image",
    ),
}
IMAGE_DEPENDENCIES = (
    "container/tools.def",
    "container/Makefile.include",
    "container/general-stubs.c",
    "container/zephyr-stubs.c",
)


class ApptainerProjectContainer(ProjectContainer):
    """Base class for project container management"""

    def __init__(self, apptainer_def_path: str, host_dir: str, repo_dir: Optional[str] = None):
        """
        :param host_dir: Host directory to map into container
        :param apptainer_def_path: Path to Apptainer definition file (required if building image)
        """
        self.host_dir = os.path.abspath(host_dir)
        self.repo_dir = os.path.abspath(repo_dir) if repo_dir else None
        self.apptainer_def_path = os.path.abspath(apptainer_def_path)
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.user_name = DEFAULT_CONTAINER_USER
        self.instance_name: Optional[str] = None

    def initialize(self):
        """Initialize container, building image if necessary."""
        self.__check_apptainer()
        self.__build_image()
        try:
            self.__start_instance()
            self.__provision_user()
            self.__initialize_tools()
        except Exception:
            self.terminate()
            raise

    def execute(self, command: str, workdir: Optional[str] = None, timeout: int = 30) -> dict:
        """Execute a command inside the container using bash shell."""
        if not self.instance_name:
            raise RuntimeError("Apptainer instance not initialized. Call initialize() first.")

        logger.debug(f"[>] Executing command: {command}")
        resolved_workdir = self.__resolve_workdir(workdir)
        if self.__should_execute_as_root(command):
            result = self.__execute_as_root(command, workdir=resolved_workdir, timeout=timeout)
        else:
            user_command = f"timeout {timeout}s bash -lc {shlex.quote(command)}"
            exec_command = [
                "apptainer", "exec", "--pwd", resolved_workdir,
                f"instance://{self.instance_name}",
                "su", "-s", "/bin/bash", self.user_name,
                "-c", user_command,
            ]
            result = self.__run_subprocess(exec_command)
        self.__raise_if_apptainer_failure(
            result=result,
            context=f"executing command in workdir '{resolved_workdir}'",
        )
        logger.debug(f"[DEBUG] exit_code: {result['exit_code']}")
        logger.debug(f"[DEBUG] stdout:\n{result['stdout']}")
        logger.debug(f"[DEBUG] stderr:\n{result['stderr']}")
        return result

    def terminate(self):
        """Stop and remove the running instance."""
        if not self.instance_name:
            return
        stop_command = ["apptainer", "instance", "stop", self.instance_name]
        result = self.__run_subprocess(stop_command, raise_on_missing_binary=False)
        if result["exit_code"] == 0:
            logger.info(f"[+] Apptainer instance '{self.instance_name}' terminated.")
        elif result["stderr"].strip():
            logger.warning(
                f"[!] Failed to terminate Apptainer instance '{self.instance_name}': "
                f"{result['stderr'].strip()}"
            )
        self.instance_name = None

    def __check_apptainer(self):
        if shutil.which("apptainer") is None:
            raise RuntimeError("Apptainer is not installed or not found in PATH.")
        result = self.__run_subprocess(["apptainer", "--version"])
        if result["exit_code"] != 0:
            raise RuntimeError(
                f"Apptainer preflight failed: {result['stderr'].strip() or result['stdout'].strip()}"
            )

    def __build_image(self):
        if not self.apptainer_def_path or not os.path.exists(self.apptainer_def_path):
            raise FileNotFoundError(
                f"Definition file path '{self.apptainer_def_path}' does not exist."
            )

        if self.__image_is_fresh():
            logger.info(f"[*] Apptainer image '{IMAGE_FILE}' is up to date; skipping build.")
            return

        if os.path.exists(IMAGE_FILE):
            logger.info(f"[*] Rebuilding stale Apptainer image '{IMAGE_FILE}'.")
            os.remove(IMAGE_FILE)

        logger.info(f"[+] Building Apptainer image from {self.apptainer_def_path}...")
        result = self.__run_subprocess(
            ["apptainer", "build", "--fakeroot", IMAGE_FILE, self.apptainer_def_path]
        )
        if result["exit_code"] == 0:
            logger.info(f"[+] Image '{IMAGE_FILE}' built successfully.")
        else:
            logger.error("[!] Apptainer build failed!")
            raise RuntimeError(
                "Apptainer build failed: "
                f"{result['stderr'].strip() or result['stdout'].strip() or 'unknown error'}"
            )

    def __image_is_fresh(self) -> bool:
        if not os.path.exists(IMAGE_FILE):
            return False

        image_mtime = os.path.getmtime(IMAGE_FILE)
        for dependency in IMAGE_DEPENDENCIES:
            dependency_path = os.path.abspath(dependency)
            if not os.path.exists(dependency_path):
                raise FileNotFoundError(f"Apptainer build dependency '{dependency_path}' does not exist.")
            if os.path.getmtime(dependency_path) > image_mtime:
                return False
        return True

    def __start_instance(self):
        if not os.path.isdir(self.host_dir):
            raise FileNotFoundError(f"Host directory '{self.host_dir}' does not exist.")
        if self.repo_dir and not os.path.isdir(self.repo_dir):
            raise FileNotFoundError(f"Repo directory '{self.repo_dir}' does not exist.")

        self.instance_name = f"{INSTANCE_PREFIX}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        logger.info(f"[+] Starting Apptainer instance '{self.instance_name}'...")
        bind_args = ["--bind", f"{self.host_dir}:{self.host_dir}"]
        if self.repo_dir and self.repo_dir != self.host_dir:
            bind_args.extend(["--bind", f"{self.repo_dir}:{self.repo_dir}"])
        start_command = [
            "apptainer", "instance", "start",
            "--fakeroot",
            "--writable-tmpfs",
            *bind_args,
            IMAGE_FILE,
            self.instance_name,
        ]
        result = self.__run_subprocess(start_command)
        self.__raise_if_apptainer_failure(result, "starting Apptainer instance")
        if result["exit_code"] != 0:
            raise RuntimeError(
                "Apptainer instance startup failed: "
                f"{result['stderr'].strip() or result['stdout'].strip() or 'unknown error'}"
            )

        validation = self.__execute_as_root("pwd", workdir=self.host_dir, timeout=10)
        self.__raise_if_apptainer_failure(validation, "validating Apptainer instance working directory")
        if validation["exit_code"] != 0:
            raise RuntimeError(
                "Apptainer instance validation failed: "
                f"{validation['stderr'].strip() or validation['stdout'].strip() or 'unknown error'}"
            )

    def __provision_user(self):
        existing_user = self.__execute_as_root(
            f"getent passwd {self.uid} | cut -d: -f1",
            workdir="/",
            timeout=10,
        )
        if existing_user["exit_code"] == 0 and existing_user["stdout"].strip():
            self.user_name = existing_user["stdout"].strip()
        else:
            existing_group = self.__execute_as_root(
                f"getent group {self.gid} | cut -d: -f1",
                workdir="/",
                timeout=10,
            )
            group_name = existing_group["stdout"].strip() or self.user_name
            if not existing_group["stdout"].strip():
                create_group = self.__execute_as_root(
                    f"groupadd -g {self.gid} {group_name}",
                    workdir="/",
                    timeout=10,
                )
                if create_group["exit_code"] != 0:
                    raise RuntimeError(
                        "Failed to create Apptainer group: "
                        f"{create_group['stderr'].strip() or create_group['stdout'].strip()}"
                    )
            create_user = self.__execute_as_root(
                f"useradd -m -u {self.uid} -g {group_name} -s /bin/bash {self.user_name}",
                workdir="/",
                timeout=10,
            )
            if create_user["exit_code"] != 0:
                raise RuntimeError(
                    "Failed to create Apptainer user: "
                    f"{create_user['stderr'].strip() or create_user['stdout'].strip()}"
                )

        sudo_setup = self.__execute_as_root(
            (
                "mkdir -p /etc/sudoers.d && "
                f"echo '{self.user_name} ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/{self.user_name} && "
                f"chmod 0440 /etc/sudoers.d/{self.user_name}"
            ),
            workdir="/",
            timeout=10,
        )
        if sudo_setup["exit_code"] != 0:
            raise RuntimeError(
                "Failed to configure sudo inside Apptainer instance: "
                f"{sudo_setup['stderr'].strip() or sudo_setup['stdout'].strip()}"
            )

        user_check = self.__execute_as_root(
            f"su -s /bin/bash {self.user_name} -c {shlex.quote('id -u')}",
            workdir="/",
            timeout=10,
        )
        if user_check["exit_code"] != 0 or user_check["stdout"].strip() != str(self.uid):
            raise RuntimeError(
                "Failed to verify Apptainer user provisioning: "
                f"{user_check['stderr'].strip() or user_check['stdout'].strip() or 'unknown error'}"
            )

    def __initialize_tools(self):
        """Initialize tools inside the container, if necessary."""
        cscope_check = self.__execute_as_root("command -v cscope", workdir="/", timeout=10)
        self.__raise_if_apptainer_failure(cscope_check, "probing for cscope")
        if cscope_check["exit_code"] != 0 or not cscope_check["stdout"].strip():
            logger.info("[*] cscope not found in container; skipping cscope initialization.")
            return
        lock_path = os.path.join(self.host_dir, ".cscope.lock")
        lock = FileLock(lock_path, timeout=0)
        try:
            with lock:
                logger.info("[+] Acquired cscope lock; initializing database...")
                cscope_init = self.execute("cscope -Rbqk", timeout=CSCOPE_INIT_TIMEOUT)
                self.__raise_if_apptainer_failure(cscope_init, "initializing cscope database")
                if cscope_init["exit_code"] == 0:
                    logger.info("[+] cscope database initialized successfully.")
                else:
                    logger.warning(
                        f"[!] cscope initialization failed: {cscope_init['stderr'].strip()}"
                    )
        except Timeout:
            logger.info("[*] Another process is building the cscope database; skipping.")

    def __resolve_workdir(self, workdir: Optional[str]) -> str:
        resolved = workdir if workdir else self.host_dir
        if os.path.isabs(resolved) and not os.path.exists(resolved):
            raise FileNotFoundError(f"Working directory '{resolved}' does not exist on the host.")
        return resolved

    def __execute_as_root(self, command: str, workdir: Optional[str], timeout: int) -> dict:
        if not self.instance_name:
            raise RuntimeError("Apptainer instance not initialized. Call initialize() first.")
        resolved_workdir = self.__resolve_workdir(workdir)
        exec_command = [
            "apptainer", "exec", "--pwd", resolved_workdir,
            f"instance://{self.instance_name}",
            "timeout", f"{timeout}s",
            "bash", "-lc", command,
        ]
        result = self.__run_subprocess(exec_command)
        logger.debug(f"[DEBUG] root exit_code: {result['exit_code']}")
        logger.debug(f"[DEBUG] root stdout:\n{result['stdout']}")
        logger.debug(f"[DEBUG] root stderr:\n{result['stderr']}")
        return result

    def __run_subprocess(self, command: list[str], raise_on_missing_binary: bool = True) -> dict:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="ignore",
                check=False,
            )
        except FileNotFoundError as exc:
            if raise_on_missing_binary:
                raise RuntimeError(f"Failed to run '{command[0]}': {exc}") from exc
            return {
                "timeout": False,
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
            }
        return {
            "timeout": completed.returncode == 124,
            "exit_code": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }

    def __should_execute_as_root(self, command: str) -> bool:
        # fakeroot instances allow `su` to drop to the synthetic host-mapped user,
        # but sudo from that user cannot re-escalate due the container's
        # no-new-privileges setting. Run sudo-bearing commands as root instead.
        return re.search(r"(^|&&|\|\||;|\|)\s*sudo(\s|$)", command) is not None

    def __raise_if_apptainer_failure(self, result: dict, context: str):
        if result["exit_code"] == 0:
            return
        failure_category = self.__classify_apptainer_failure(result["stderr"])
        if failure_category is None:
            return
        message = result["stderr"].strip() or result["stdout"].strip() or "unknown error"
        raise RuntimeError(f"Apptainer {failure_category} while {context}: {message}")

    def __classify_apptainer_failure(self, stderr: str) -> Optional[str]:
        lowered = stderr.lower()
        for label, patterns in APPTAINER_FATAL_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                return label
        return None
