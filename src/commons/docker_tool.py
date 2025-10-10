import logging
import docker
import os
from docker.errors import ImageNotFound, BuildError, APIError

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

        logging.info(f"[+] Building Docker image '{self.image_tag}' from {self.dockerfile_path}...")
        try:
            image, logs = self.client.images.build(
                path=os.path.dirname(os.path.abspath(self.dockerfile_path)),  # directory containing the Dockerfile
                dockerfile=os.path.basename(self.dockerfile_path),  # name of the Dockerfile
                tag=self.image_tag
            )
            logging.info(f"[+] Image '{self.image_tag}' built successfully.")
        except BuildError as e:
            logging.error("[!] Docker build failed!")
            for line in e.build_log:
                logging.error(line['stream'])
            raise
        except APIError as e:
            logging.error(f"[!] Docker API error: {e}")
            raise

        return self.image_tag

    def start_container(self):
        # Prepare host mapping
        volumes = {}
        # If host_dir is specified, we assume it is valid. Should have been checked early on
        if self.host_dir:
            volumes = {self.host_dir: {'bind': self.container_dir, 'mode': 'rw'}}
            logging.info(f"[+] Mapping host directory {self.host_dir} -> container {self.container_dir}")

        if not self.image:
            raise RuntimeError("Image not built. Call build_image() first.")

        # Create and run container
        logging.info(f"[+] Creating container '{self.container_name}' from image '{self.image}'...")
        self.container = self.client.containers.run(
            self.image,
            name=self.container_name,
            stdin_open=True,
            tty=True,
            detach=True,
            working_dir=self.container_dir,
            volumes=volumes
        )
        logging.info(f"[+] Container '{self.container_name}' is running.")

    def initialize_tools(self):
        """Initialize tools inside the container, if necessary."""
        
        # First, initialize cscope database if cscope is installed
        cscope_check = self.execute("which cscope")
        if cscope_check['exit_code'] == 0 and cscope_check['stdout'].strip():
            logging.info("[+] Initializing cscope database...")
            cscope_init = self.execute("cscope -Rbqk")
            if cscope_init['exit_code'] != 0:
                logging.warning("[!] cscope initialization failed.")
            else:
                logging.info("[+] cscope database initialized.")
        else:
            logging.info("[*] cscope not found in container; skipping cscope initialization.")

    def initialize(self):
        """Initialize container, building image if necessary."""
        self.image = self.build_image()

        self.start_container()

        self.initialize_tools()

    def execute(self, command: str) -> dict:
        """Execute a command inside the container."""
        if not self.container:
            raise RuntimeError("Container not initialized. Call initialize() first.")

        logging.debug(f"[>] Executing command: {command}")
        result = self.container.exec_run(command, demux=True)
        stdout, stderr = result.output
        stdout_decoded = stdout.decode("utf-8") if stdout else ""
        stderr_decoded = stderr.decode("utf-8") if stderr else ""
        logging.debug(stdout_decoded)
        logging.debug(stderr_decoded)
        return {
            "exit_code": result.exit_code,
            "stdout": stdout_decoded,
            "stderr": stderr_decoded
        }

    def terminate(self):
        """Stop and remove the container."""
        if self.container:
            logging.debug(f"[-] Stopping container '{self.container_name}'...")
            self.container.stop()
            logging.debug(f"[-] Removing container '{self.container_name}'...")
            self.container.remove()
            self.container = None
            logging.info("[+] Container terminated.")
