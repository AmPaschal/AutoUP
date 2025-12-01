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
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    INCONCLUSIVE = "INCONCLUSIVE"

class ValidationResult(BaseModel):
    precondition: str|None
    function_model: str|None
    verdict: Verdict
    analysis: str

    def to_dict(self):
        return {
            "precondition": self.precondition,
            "function_model": self.function_model,
            "verdict": self.verdict,
            "analysis": self.analysis
        }

class PreconditionValidatorResponse(BaseModel):
    preconditions_analyzed: int
    function_models_analyzed: int
    validation_result: list[ValidationResult]
    

    def to_dict(self):
        return {
            "preconditions_analyzed": self.preconditions_analyzed,
            "function_models_analyzed": self.function_models_analyzed,
            "validation_result": [
                v.to_dict() for v in self.validation_result
            ]
        }