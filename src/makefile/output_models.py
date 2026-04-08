from pydantic import BaseModel, model_validator
from typing import Optional
from enum import Enum

class MakefileFields(BaseModel):
    analysis: str
    updated_makefile: str
    updated_harness: Optional[str] = None

    def to_dict(self):
        return {
            "analysis": self.analysis,
            "updated_makefile": self.updated_makefile,
            "updated_harness": self.updated_harness
        }
    
class HarnessResponse(BaseModel):
    analysis: str
    harness_code: str

    def to_dict(self):
        return {
            "analysis": self.analysis,
            "harness_code": self.harness_code
        }

class CoverageDebuggerResponse(BaseModel):
    analysis: str
    proposed_modifications: str
    updated_harness: Optional[str] = None
    updated_makefile: Optional[str] = None

    def to_dict(self):
        return {
            "analysis": self.analysis,
            "proposed_modifications": self.proposed_modifications,
            "updated_harness": self.updated_harness,
            "updated_makefile": self.updated_makefile
        }

class ViolationType(Enum):
    DATA_STRUCTURE_BOUND = "data-structure-bound"
    IRRELEVANT = "irrelevant"
    INCOMPLETE = "incomplete"
    FUNCTION_POINTER_CONSTRAINTS = "function-pointer-constraints"
    ANGELIC_ASSUMPTION = "angelic-assumption"
    EXPLOITABLE = "exploitable"

class ValidationResult(BaseModel):
    precondition: str
    parent_function: str
    violated: bool
    violation_type: Optional[ViolationType]
    reasoning: str
    detailed_analysis: str

    @model_validator(mode="after")
    def validate_violation_fields(self):
        if self.violated and self.violation_type is None:
            raise ValueError("violation_type must be provided when violated is true")
        if not self.violated and self.violation_type is not None:
            raise ValueError("violation_type must be null when violated is false")
        return self

    def to_dict(self):
        return {
            "precondition": self.precondition,
            "parent_function": self.parent_function,
            "violated": self.violated,
            "violation_type": self.violation_type.value if self.violation_type else None,
            "reasoning": self.reasoning,
            "detailed_analysis": self.detailed_analysis
        }

class PreconditionValidatorResponse(BaseModel):
    preconditions_analyzed: int
    validation_result: list[ValidationResult]
    updated_harness: Optional[str] = None

    def to_dict(self, include_updated_harness: bool = True):
        result = {
            "preconditions_analyzed": self.preconditions_analyzed,
            "validation_result": [
                v.to_dict() for v in self.validation_result
            ]
        }
        if include_updated_harness:
            result["updated_harness"] = self.updated_harness
        return result

class VulnAwareRefinerResponse(BaseModel):
    """
    Response model for the VulnAwareRefiner agent.
    
    The LLM analyzes loops with unwinding failures and returns:
    - analysis: Detailed analysis of iteration-dependent memory operations
    - num_loop_unwindings_set: Number of custom loop unwindings to set or increase
    - updated_makefile: Complete Makefile with appropriate --unwindset flags
    """
    analysis: str
    num_loop_unwindings_set: int
    updated_makefile: str

    def to_dict(self):
        return {
            "analysis": self.analysis,
            "num_loop_unwindings_set": self.num_loop_unwindings_set,
            "updated_makefile": self.updated_makefile
        }
