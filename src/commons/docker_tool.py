import docker
import os
from docker.errors import ImageNotFound, BuildError, APIError

from logger import setup_logger


logger = setup_logger(__name__)

class ProjectContainer:
    def __init__(self, dockerfile_path: str, host_dir: str, container_name: str, container_dir="/project",
                 image_tag="autoup_image:latest"):
        """
        :param container_name: Name of the container
        :param host_dir: Host directory to map into container
        :param container_dir: Directory inside container for host_dir
        :param dockerfile_path: Path to Dockerfile (required if building image)
        :param image_tag: Tag for the image
        """
        self.container_name = container_name
        self.host_dir = host_dir
        self.container_dir = container_dir
        self.dockerfile_path = dockerfile_path
        self.image_tag = image_tag

        self.client = docker.from_env()
        self.container = None
        self.image = None

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
        # If host_dir is specified, we assume it is valid. Should have been checked early on
        if self.host_dir:
            volumes = {self.host_dir: {'bind': self.container_dir, 'mode': 'rw'}}
            logger.info(f"[+] Mapping host directory {self.host_dir} -> container {self.container_dir}")

        if not self.image:
            raise RuntimeError("Image not built. Call build_image() first.")

        # Create and run container
        logger.info(f"[+] Creating container '{self.container_name}' from image '{self.image}'...")
        self.container = self.client.containers.run(
            self.image,
            name=self.container_name,
            stdin_open=True,
            tty=True,
            detach=True,
            working_dir=self.container_dir,
            volumes=volumes
        )
        logger.info(f"[+] Container '{self.container_name}' is running.")

    def initialize_tools(self):
        """Initialize tools inside the container, if necessary."""
        
        # First, initialize cscope database if cscope is installed
        cscope_check = self.execute("which cscope")
        if cscope_check['exit_code'] == 0 and cscope_check['stdout'].strip():
            logger.info("[+] Initializing cscope database...")
            cscope_init = self.execute("cscope -Rbqk")
            if cscope_init['exit_code'] != 0:
                logger.warning("[!] cscope initialization failed.")
            else:
                logger.info("[+] cscope database initialized.")
        else:
            logger.info("[*] cscope not found in container; skipping cscope initialization.")

    def initialize(self):
        """Initialize container, building image if necessary."""
        self.image = self.build_image()

        self.start_container()

        self.initialize_tools()

    def execute(self, command: str) -> dict:
        """Execute a command inside the container using bash shell."""
        if not self.container:
            raise RuntimeError("Container not initialized. Call initialize() first.")

        logger.debug(f"[>] Executing command: {command}")
        exec_command = ["timeout", "10s", "bash", "-c", command]
        result = self.container.exec_run(exec_command, demux=True)
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
