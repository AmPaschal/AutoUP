from pydantic import BaseModel
from typing import Optional

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