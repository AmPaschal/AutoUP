"""Apptainer project container management"""

# System
from typing import Optional
import subprocess
import os

# Utils
from filelock import FileLock, Timeout

# AutoUP
from src.commons.project_container import ProjectContainer
from logger import setup_logger

logger = setup_logger(__name__)

IMAGE_FILE = "tools.sif"


class ApptainerProjectContainer(ProjectContainer):
    """Base class for project container management"""

    def __init__(self, apptainer_def_path: str, host_dir: str):
        """
        :param host_dir: Host directory to map into container
        :param apptainer_def_path: Path to Apptainer definition file (required if building image)
        """
        self.host_dir = host_dir
        self.apptainer_def_path = apptainer_def_path

    def initialize(self):
        """Initialize container, building image if necessary."""
        self.__build_image()
        self.__initialize_tools()

    def execute(self, command: str, workdir: Optional[str] = None, timeout: int = 30) -> dict:
        """Execute a command inside the container using bash shell."""
        logger.debug(f"[>] Executing command: {command}")
        exec_command = ["apptainer", "exec", "--pwd", workdir,
                        IMAGE_FILE, "timeout", f"{timeout}s", "bash", "-c", command]
        with subprocess.Popen(
            exec_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        ) as process:
            stdout, stderr = process.communicate()
            exit_code = process.poll()
        logger.debug(f"[DEBUG] exit_code: {exit_code}")
        logger.debug(f"[DEBUG] stdout:\n{stdout}")
        logger.debug(f"[DEBUG] stderr:\n{stderr}")
        return {
            "timeout": exit_code == 124,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr
        }

    def terminate(self):
        """Stop and remove the container.""" # No needed for Apptainer   

    def __build_image(self):
        if not self.apptainer_def_path or not os.path.exists(self.apptainer_def_path):
            raise FileNotFoundError(
                f"Definition file path '{self.apptainer_def_path}' does not exist."
            )
        logger.info(f"[+] Building Apptainer image from {self.apptainer_def_path}...")

        with subprocess.Popen(
            ["apptainer", "build", "--fakeroot", IMAGE_FILE, self.apptainer_def_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        ) as process:
            process.communicate()
            exit_code = process.poll()
        if exit_code == 0:
            logger.info(f"[+] Image '{self.apptainer_def_path}' built successfully.")
        else:
            logger.error("[!] Docker build failed!")
            raise RuntimeError(f"Apptainer build failed with exit code {exit_code}.")

    def __initialize_tools(self):
        """Initialize tools inside the container, if necessary."""
        cscope_check = self.execute("which cscope")
        if cscope_check["exit_code"] != 0 or not cscope_check["stdout"].strip():
            logger.info("[*] cscope not found in container; skipping cscope initialization.")
            return
        lock_path = os.path.join(self.host_dir, ".cscope.lock")
        lock = FileLock(lock_path, timeout=0)
        try:
            with lock:
                logger.info("[+] Acquired cscope lock; initializing database...")
                cscope_init = self.execute("cscope -Rbqk")
                if cscope_init["exit_code"] == 0:
                    logger.info("[+] cscope database initialized successfully.")
                else:
                    logger.warning("[!] cscope initialization failed.")
        except Timeout:
            logger.info("[*] Another process is building the cscope database; skipping.")
