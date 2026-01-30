"""Project container base class"""

# System
from abc import ABC, abstractmethod
from typing import Optional


class ProjectContainer(ABC):
    """Base class for project container management"""

    @abstractmethod
    def initialize(self):
        """Initialize container, building image if necessary."""

    @abstractmethod
    def execute(self, command: str, workdir: Optional[str] = None, timeout: int = 30) -> dict:
        """Execute a command inside the container using bash shell."""

    @abstractmethod
    def terminate(self):
        """Stop and remove the container."""