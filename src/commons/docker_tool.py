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
    def __init__(self, container_name: str, host_dir: Optional[os.PathLike] = None,
                 dockerfile_path: Optional[os.PathLike] = None,
                 image_tag: str = "autoup_project_image",
                 output_dir: Optional[os.PathLike] = None):
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
        self.output_dir = output_dir
        
        # Determine container mount point based on OS
        if os.name == 'nt':  # Windows
            self.container_mount_point = "/app"
            self.container_output_dir = "/output"
        else:  # Linux/Mac
            self.container_mount_point = str(self.host_dir)
            self.container_output_dir = str(self.output_dir)
        
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
        if not self.dockerfile_path or not self.dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile path '{self.dockerfile_path}' does not exist.")

        logger.info(f"[+] Building Docker image '{self.image_tag}' from {self.dockerfile_path}...")
        try:
            image, logs = self.client.images.build(
                path=str(self.dockerfile_path.parent),  # directory containing the Dockerfile
                dockerfile=self.dockerfile_path.name,  # name of the Dockerfile
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

        # If host_dir is specified, we assume it is valid. Should have been checked early on
        if self.host_dir.exists():
            volumes = {
                self.host_dir: {'bind': self.container_mount_point, 'mode': 'rw'},
                self.output_dir: {'bind': self.container_output_dir, 'mode': 'rw'}
            }
            logger.info(f"[+] Mapping host directory {self.host_dir} -> container {self.container_mount_point}")
            logger.info(f"[+] Mapping host output directory {self.output_dir} -> container {self.container_output_dir}")

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
            "working_dir": self.container_mount_point,
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
        lock_path = self.host_dir.joinpath(".cscope.lock")
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

    def host_to_container_path(self, host_path) -> str:
        """
        Convert a Windows host path to a Linux container path.
        
        Args:
            host_path: Path object or string representing a path on the host system
            
        Returns:
            String representing the equivalent path in the container
        """
        from pathlib import Path, PureWindowsPath, PurePosixPath
        
        # Convert to Path object if string
        if isinstance(host_path, str):
            host_path = Path(host_path)
        
        # Convert to absolute path
        try:
            host_path = host_path.resolve()
        except:
            # If resolve fails, use as-is
            pass
        
        # Check if path is under host_dir (project root)
        try:
            relative_to_host = host_path.relative_to(self.host_dir)
            # Path is under project root, map to container mount point
            container_path = PurePosixPath(self.container_mount_point) / relative_to_host
            return container_path.as_posix()
        except ValueError:
            pass
        
        # Check if path is under output_dir
        try:
            relative_to_output = host_path.relative_to(self.output_dir)
            # Path is under output directory, map to container output
            container_path = PurePosixPath(self.container_output_dir) / relative_to_output
            return container_path.as_posix()
        except ValueError:
            pass
        
        # If path is not under either directory, it might already be a container path
        # or an absolute container path (starts with /)
        path_str = str(host_path)
        if path_str.startswith('/'):
            # Already looks like a container path
            return path_str
        
        # Default: assume it's relative to container mount point
        logger.warning(f"Path {host_path} not under host_dir or output_dir, treating as container path")
        return path_str

    def container_to_host_path(self, container_path):
        """
        Convert a Linux container path to a Windows host path.
        
        Args:
            container_path: String representing a path in the container
            
        Returns:
            Path object representing the equivalent path on the host system
        """
        from pathlib import Path, PurePosixPath
        
        # Convert to PurePosixPath
        if isinstance(container_path, str):
            container_path = PurePosixPath(container_path)
        
        # Check if path is under container mount point
        try:
            relative_to_mount = container_path.relative_to(self.container_mount_point)
            # Map to host directory
            return self.host_dir / relative_to_mount
        except ValueError:
            pass
        
        # Check if path is under container output directory
        try:
            relative_to_output = container_path.relative_to(self.container_output_dir)
            # Map to host output directory
            return self.output_dir / relative_to_output
        except ValueError:
            pass
        
        # If not under either mount, return as-is
        logger.warning(f"Container path {container_path} not under known mounts")
        return Path(str(container_path))

    def execute(self, command: str, workdir: Optional[str] = None, timeout: int = 30) -> dict:
        """Execute a command inside the container using bash shell.
        
        Args:
            command: Command to execute
            workdir: Working directory (can be host path or container path, will be auto-converted)
            timeout: Timeout in seconds
        """
        if not self.container:
            raise RuntimeError("Container not initialized. Call initialize() first.")

        # Auto-translate workdir if it's a host path
        if workdir is not None:
            # If workdir is a Path object or looks like a host path, convert it
            from pathlib import Path
            if isinstance(workdir, Path) or (isinstance(workdir, str) and not workdir.startswith('/')):
                original_workdir = workdir
                workdir = self.host_to_container_path(workdir)
                logger.debug(f"[>] Translated workdir: {original_workdir} -> {workdir}")

        logger.debug(f"[>] Executing command: {command}")
        if workdir:
            logger.debug(f"[>] Working directory: {workdir}")
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
