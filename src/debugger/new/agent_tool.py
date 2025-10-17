""" Agent Tool"""


# System
from abc import ABC, abstractmethod


class AgentTool(ABC):
    """Tool used by a Agent"""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    @abstractmethod
    def use_tool(self, *args, **kwargs):
        """Implementation of the corresponding tool"""

    @abstractmethod
    def get_tool_signature(self) -> dict:
        """Returns a dictionary with the signature of the function tool"""