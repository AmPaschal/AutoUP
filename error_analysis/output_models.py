from pydantic import BaseModel
from typing import Optional

class ExistingPrecondition(BaseModel):
    precondition: str
    function: str
    line: int

class PreconditionChecks(BaseModel):
    uses_cprover_assume_format: bool
    only_harness_variables: bool
    placed_in_harness_before_last_line: bool
    placed_in_harness_after_initialization: bool
    is_not_redundant: bool

class PreconditionFunction(BaseModel):
    name: str
    local_vars: list[str]

class NewPrecondition(BaseModel):
    function: PreconditionFunction
    precondition: str
    precondition_as_code: str
    previous_line_of_code: str # Keeps inserting things at the wrong line numbers
    previous_line_number: int # Don't actually need this line number, but requiring it can discourage hallucinations
    next_line_of_code: str
    next_line_number: int 
    reasoning: str
    is_valid: PreconditionChecks

class FunctionModel(BaseModel):
    function: str
    definition: str

class Variable(BaseModel):
    name: str
    provided_value: str
    original_scope: str
    modifications_after_harness: list[str]
    value_at_point_of_error: str

class OptionalQuestions(BaseModel):
    question: str
    analysis: str

class DebuggingQuestions(BaseModel):
    provided_debugging_step: int
    provided_debugging_question: str
    further_analysis_questions: Optional[list[OptionalQuestions]]
    was_cause_of_error: bool
    problem_variables: Optional[list[Variable]]
    reasoning: str

class PreviousSuggestions(BaseModel):
    preconditions: list[str]
    resolved_error: bool
    should_keep: bool
    analyis: str

class ModelOutput(BaseModel):
    existing_preconditions: list[ExistingPrecondition]
    debugging_analysis_questions: list[DebuggingQuestions]
    previous_suggestions: Optional[list[PreviousSuggestions]]
    new_preconditions: list[NewPrecondition]
    # func_models: list[FunctionModel]