"""Dereference handler"""

# System
import re
from pathlib import Path

# AutoUP
from debugger.programmatic_handler import ErrorHandler
from logger import setup_logger

logger = setup_logger(__name__)


class DerefereneErrorHandler(ErrorHandler):
    """Handle programatically dereferences to a NULL"""

    def do_analysis(self, error: str, steps: list) -> tuple[str, int] | None:
        """Implements the specific analysis to a error"""
        suggestion_line = 0
        result = self.__locate_error_and_variable_name(error, steps)
        logger.info("Initial variable and location: %s", result)
        if result is None:
            return None
        index, variable = result
        while index > 0:
            if steps[index]["kind"] == "parameter-assignment":
                if steps[index]["detail"]["lhs"] == variable:
                    result = self.__handle_parameter_assignment(steps, index)
                    logger.info("Parameter assignment: %s", result)
                    if result is None:
                        return None
                    index, variable = result
            if steps[index]["kind"] == "variable-assignment":
                if steps[index]["detail"]["lhs"] == variable:
                    result = self.__handle_variable_assignment(steps, index)
                    suggestion_line = steps[index]["location"]["line"]
                    if result is None:
                        return None
                    index, variable, is_in_harness = result
                    if is_in_harness:
                        logger.info("Precondition suggested!")
                        return variable, suggestion_line
            index -= 1
        logger.warning("No precondition suggested")
        return None

    def __locate_error_and_variable_name(
        self,
        error_id: str,
        steps: list,
    ) -> tuple[int, str] | None:
        """Locate the step where the error ocurred"""
        for index, step in reversed(list(enumerate(steps))):
            if "detail" in step and "property" in step["detail"]:
                if step["detail"]["property"] == error_id:
                    match_result = re.match(
                        r"dereference failure: pointer NULL in ([_a-zA-Z]+)->[_a-zA-Z]+",
                        step["detail"]["reason"],
                    )
                    if match_result:
                        return index, match_result.group(1)
                    match_result = re.match(
                        r"dereference failure: pointer NULL in \*([_a-zA-Z]+)",
                        step["detail"]["reason"],
                    )
                    if match_result:
                        return index, match_result.group(1)
        return None

    def __handle_parameter_assignment(self, steps: list, step_index: int) -> tuple[int, str] | None:
        """Handle function call"""
        index = 0
        while steps[step_index]["kind"] != "function-call":
            index += 1
            step_index -= 1
        file_path = steps[step_index]["location"]["file"]
        line_number = steps[step_index]["location"]["line"]
        variable_name = self.__extract_argument_name(
            index, file_path, line_number)
        if variable_name is None:
            return None
        return step_index, variable_name

    def __handle_variable_assignment(self, steps: list, step_index: int) -> tuple[int, str, bool] | None:
        """Get the new variable to track"""
        file_path = steps[step_index]["location"]["file"]
        line_number = steps[step_index]["location"]["line"]
        with (Path(self.root_dir) / file_path).open("r", encoding="utf-8") as file:
            line = file.readlines()[line_number - 1]
        logger.info("Path: %s", Path(self.root_dir) / file_path)
        logger.info("linenumber: %s", line_number)
        logger.info("Line: %s", line)
        match_result = re.search(r"= ?(?:\([\w \*]+\))? ?([-\w>]+);", line)
        if match_result:  # Variable
            logger.info("new var: %s", match_result.group(1))
            return step_index, match_result.group(1), False
        match_result = re.search(
            r"(\w+) ?= ?(?:\(\w+ ?\*?\)) ?(\w+) ?\(\w*\);", line)
        if match_result:  # Function
            if match_result.group(2) == "malloc" and "_harness.c" in file_path:
                logger.info("Malloc var: %s", match_result.group(1))
                return step_index, match_result.group(1), True
        return None

    def __extract_argument_name(  # TODO: what if function call is multiline?
        self,
        argument_index: int,
        file_path: str,
        line_number: int,
    ) -> str | None:
        """ Get the name of an argument given a function"""
        with (Path(self.root_dir) / file_path).open("r", encoding="utf-8") as file:
            line = file.readlines()[line_number - 1]
        args = re.findall(r'\(([^)]*)\)', line)
        if args:
            arg_list = [a.strip() for a in args[-1].split(',') if a.strip()]
            if argument_index - 1 < len(arg_list):
                return arg_list[argument_index - 1]
        return None
