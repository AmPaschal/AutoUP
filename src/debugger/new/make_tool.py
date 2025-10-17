"""Make tool"""

# System
import subprocess

# AutoUp
from agent_tool import AgentTool


class MakeTool(AgentTool):
    """Agentic Tool which allows GPT to access 'make' command"""
    
    def __init__(self) -> None:
        super().__init__("make_tool")

    def use_tool(self, *_args, **kwargs):
        """Implementation of the corresponding tool"""
        path = kwargs.get("path")
        if not path:
            raise ValueError("Path is required")
        with subprocess.Popen(
            ["make"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        ) as process:
            _stdout, _stderr = process.communicate()

    def get_tool_signature(self) -> dict:
        """Returns a dictionary with the signature of the function tool"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Ejecute make in a given directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        }
