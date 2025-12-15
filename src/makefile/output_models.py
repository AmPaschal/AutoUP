from pydantic import BaseModel
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

class Verdict(Enum):
    VALID = "VALID"
    VIOLATED_NOT_BUGGY = "VIOLATED_NOT_BUGGY"
    VIOLATED_BUGGY = "VIOLATED_BUGGY"

class ValidationResult(BaseModel):
    precondition: str
    parent_function: str
    verdict: Verdict
    untrusted_input_source: str
    reasoning: str
    detailed_analysis: str

    def to_dict(self):
        return {
            "precondition": self.precondition,
            "parent_function": self.parent_function,
            "verdict": self.verdict.value,
            "untrusted_input_source": self.untrusted_input_source,
            "reasoning": self.reasoning,
            "detailed_analysis": self.detailed_analysis
        }

class PreconditionValidatorResponse(BaseModel):
    preconditions_analyzed: int
    validation_result: list[ValidationResult]
    

    def to_dict(self):
        return {
            "preconditions_analyzed": self.preconditions_analyzed,
            "validation_result": [
                v.to_dict() for v in self.validation_result
            ]
        }