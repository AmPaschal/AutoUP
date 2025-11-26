"""Programatic handler"""

# System
from abc import ABC, abstractmethod
import json
from pathlib import Path

# AutoUP
from debugger.error_report import CBMCError
from logger import setup_logger

logger = setup_logger(__name__)

class ErrorHandler(ABC):
    """Error handler interface"""

    def __init__(self, harness_path: str, root_dir: str, harness_file_path: str) -> None:
        self.root_dir = root_dir
        self.report_path = Path(harness_path) / "build" / "report" / "json"
        self.harness_file_path = harness_file_path

    def analyze(self, error: CBMCError) -> str | None:
        """Analyze the given error"""
        steps = self.__load_steps(error.error_id)
        result = self.do_analysis(error.error_id, steps)
        if result is None:
            return None
        variable, line = result
        return self.__update_harness_content(variable, line)

    @abstractmethod
    def do_analysis(self, error: str, steps: list) -> tuple[str, int] | None:
        """Implements the specific analysis to a error"""

    def __load_steps(self, error_id: str) -> list:
        with (self.report_path / "viewer-trace.json").open("r", encoding="utf-8") as file:
            data = json.loads(file.read())
        return data["viewer-trace"]["traces"][error_id]

    def __update_harness_content(self, variable: str, line: int) -> str:
        logger.info("Updating harness: inserting check for '%s' at line '%i'", variable, line)
        with open(
            self.harness_file_path,
            "r",
            encoding="utf-8",
        ) as file:
            lines = file.readlines()
        precondition = f"__CPROVER_assume({variable} != NULL);"
        lines.insert(line, precondition + "\n")
        updated_file = "".join(lines)
        return updated_file
