from typing import Optional
from filelock import FileLock, Timeout
import docker
import os
from docker.errors import DockerException, BuildError, APIError
from docker.models.containers import Container

from logger import setup_logger


logger = setup_logger(__name__)

# =====================================================
# 🧱 Message Constants
# =====================================================

MSG_OK = "[OK] Connected to Docker daemon. Version: {version}"

MSG_PERMISSION_DENIED = (
    "[ERROR] Permission denied when accessing the Docker socket.\n"
    "Your user likely isn't part of the 'docker' group."
)
MSG_SOCKET_NOT_FOUND = (
    "[ERROR] Docker socket not found. The Docker daemon may not be running."
)
MSG_CONNECTION_REFUSED = (
    "[ERROR] Cannot connect to the Docker daemon. It may not be running."
)
MSG_DOCKER_NOT_FOUND = (
    "[ERROR] Docker is not installed or not found in your PATH."
)
MSG_SDK_NOT_INSTALLED = (
    "[ERROR] The Docker SDK for Python is not installed."
)
MSG_UNKNOWN_ERROR = "[ERROR] Unexpected error while checking Docker:\n{error}"

# ---- Suggested fixes ----
FIX_PERMISSION = "sudo usermod -aG docker $USER"
FIX_START_DAEMON = "sudo systemctl start docker"
FIX_INSTALL_DOCKER = "https://docs.docker.com/get-docker/"
FIX_INSTALL_SDK = "pip install docker"

# ---- Message templates ----
SUGGEST_GROUP = f"Add your user to the 'docker' group and re-login:\n {FIX_PERMISSION}"
SUGGEST_START = f"Start the Docker service using:\n {FIX_START_DAEMON}"
SUGGEST_INSTALL = f"Install Docker from:\n {FIX_INSTALL_DOCKER}"
SUGGEST_SDK = f"Install the Python Docker SDK using:\n {FIX_INSTALL_SDK}"

class ProjectContainer:
    def __init__(self, dockerfile_path: str, host_dir: str, container_name: str,
                 image_tag="autoup_image:latest"):
        """
        :param container_name: Name of the container
        :param host_dir: Host directory to map into container
        :param dockerfile_path: Path to Dockerfile (required if building image)
        :param image_tag: Tag for the image
        """
        self.container_name = container_name
        self.host_dir = host_dir
        self.dockerfile_path = dockerfile_path
        self.image_tag = image_tag

        
        self.container: Optional[Container] = None
        self.image = None

    def suggest_fix(self, error, suggestion=None):
        logger.error(error)
        if suggestion:
            logger.error(f"    {suggestion}\n")

    def check_docker(self):
        """Check Docker daemon connectivity and permissions."""
        try:
            self.client = docker.from_env()
            self.client.ping()
            version_info = self.client.version()
            logger.info(MSG_OK.format(version=version_info.get("Version", "unknown")))
            return True
        except DockerException as e:
            error_msg = str(e).lower()
            if "docker" in error_msg and "not found" in error_msg:
                self.suggest_fix(MSG_DOCKER_NOT_FOUND, SUGGEST_INSTALL)
            elif "permission denied" in error_msg or "permissionerror" in error_msg:
                self.suggest_fix(MSG_PERMISSION_DENIED, SUGGEST_GROUP)
            elif "connection refused" in error_msg or "cannot connect" in error_msg:
                self.suggest_fix(MSG_CONNECTION_REFUSED, SUGGEST_START)
            elif "file not found" in error_msg or "no such file" in error_msg:
                self.suggest_fix(MSG_SOCKET_NOT_FOUND, SUGGEST_START)
            else:
                self.suggest_fix(MSG_UNKNOWN_ERROR.format(error=str(e)), None)
            return False
        except Exception as e:
            self.suggest_fix(MSG_UNKNOWN_ERROR.format(error=str(e)), None)
            return False

    def build_image(self) -> str:
        """Build a Docker image from Dockerfile."""
        if not self.dockerfile_path or not os.path.exists(self.dockerfile_path):
            raise FileNotFoundError(f"Dockerfile path '{self.dockerfile_path}' does not exist.")

        logger.info(f"[+] Building Docker image '{self.image_tag}' from {self.dockerfile_path}...")
        try:
            image, logs = self.client.images.build(
                path=os.path.dirname(os.path.abspath(self.dockerfile_path)),  # directory containing the Dockerfile
                dockerfile=os.path.basename(self.dockerfile_path),  # name of the Dockerfile
                tag=self.image_tag
            )
            logger.info(f"[+] Image '{self.image_tag}' built successfully.")
        except BuildError as e:
            logger.error("[!] Docker build failed!")
            for line in e.build_log:
                logger.error(line['stream'])
            raise
        except APIError as e:
            logger.error(f"[!] Docker API error: {e}")
            raise

        return self.image_tag

    def start_container(self):
        # Prepare host mapping
        volumes = {}
        # Default container path matches host path (works for Linux-to-Linux)
        container_mount_point = self.host_dir

        # If host_dir is specified, we assume it is valid. Should have been checked early on
        if os.path.exists(self.host_dir):
            # On Windows, we must map the host path (with drive letter) to a valid Linux path
            if os.name == 'nt':
                # Use a standard mount point for the project root to avoid "too many colons" error
                # caused by drive letters in the container path.
                container_mount_point = "/app"
            
            volumes = {self.host_dir: {'bind': container_mount_point, 'mode': 'rw'}}
            logger.info(f"[+] Mapping host directory {self.host_dir} -> container {container_mount_point}")

        if not self.image:
            raise RuntimeError("Image not built. Call build_image() first.")

        # Create and run container
        logger.info(f"[+] Creating container '{self.container_name}' from image '{self.image}'...")
        
        run_kwargs = {
            "image": self.image,
            "name": self.container_name,
            "stdin_open": True,
            "tty": True,
            "detach": True,
            "working_dir": container_mount_point,
            "volumes": volumes
        }

        if os.name == 'posix':
            run_kwargs["user"] = f"{os.getuid()}:{os.getgid()}"

        self.container = self.client.containers.run(**run_kwargs)
        logger.info(f"[+] Container '{self.container_name}' is running.")

    def initialize_tools(self):
        """Initialize tools inside the container, if necessary."""

        # --- Step 1: Check if cscope is available ---
        cscope_check = self.execute("which cscope")
        if cscope_check["exit_code"] != 0 or not cscope_check["stdout"].strip():
            logger.info("[*] cscope not found in container; skipping cscope initialization.")
            return

        # --- Step 2: Try to acquire a file-based lock before initializing cscope ---
        lock_path = os.path.join(self.host_dir, ".cscope.lock")
        lock = FileLock(lock_path, timeout=0)  # non-blocking: skip if busy

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

    def initialize(self):
        """Initialize container, building image if necessary."""

        if not self.check_docker():
            raise RuntimeError("Docker daemon is not accessible. Cannot initialize container.")

        self.image = self.build_image()

        self.start_container()

        self.initialize_tools()

    def execute(self, command: str, workdir: Optional[str] = None, timeout: int = 30) -> dict:
        """Execute a command inside the container using bash shell."""
        if not self.container:
            raise RuntimeError("Container not initialized. Call initialize() first.")

        logger.debug(f"[>] Executing command: {command}")
        exec_command = ["timeout", f"{timeout}s", "bash", "-c", command]
        result = self.container.exec_run(exec_command, workdir=workdir, demux=True)
        stdout, stderr = result.output
        stdout_decoded = stdout.decode("utf-8", errors="ignore") if stdout else ""
        stderr_decoded = stderr.decode("utf-8", errors="ignore") if stderr else ""

        logger.debug(f"[DEBUG] exit_code: {result.exit_code}")
        logger.debug(f"[DEBUG] stdout:\n{stdout_decoded}")
        logger.debug(f"[DEBUG] stderr:\n{stderr_decoded}")
        return {
            "timeout": result.exit_code == 124,
            "exit_code": result.exit_code,
            "stdout": stdout_decoded,
            "stderr": stderr_decoded
        }


    def terminate(self):
        """Stop and remove the container."""
        if self.container:
            logger.debug(f"[-] Stopping container '{self.container_name}'...")
            self.container.stop()
            logger.debug(f"[-] Removing container '{self.container_name}'...")
            self.container.remove()
            self.container = None
            logger.info("[+] Container terminated.")
