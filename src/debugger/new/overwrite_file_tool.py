"""Override make tool"""

# System
import subprocess

# AutoUp
from agent_tool import AgentTool


class OverrideFileTool(AgentTool):
    """Agentic Tool which allows GPT to access 'make' command"""

    def __init__(self) -> None:
        super().__init__("override_file_tool")

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
                "name": "override_file",
                "description": "Override a file given a string",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
        }
