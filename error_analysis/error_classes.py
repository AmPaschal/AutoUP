class CoverageError(Exception):
    """
    Exception raised when an error is no longer covered by the current harness
    """
    def __init__(self, message, lines):
        super().__init__(self, message)
        self.missed_lines = lines


class PreconditionError(Exception):
    """
    Exception raised when an error is no longer covered by the current harness
    """
    def __init__(self, message, errors):
        super().__init__(self, message)
        self.new_errors = errors

class InsertError(Exception):
    """
    Exception raised when an error occurs while inserting a precondition into the harness
    """
    def __init__(self, message, prev_line, next_line):
        super().__init__(self, message)
        self.prev_line = prev_line
        self.next_line = next_line