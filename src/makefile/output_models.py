from pydantic import BaseModel
from typing import Optional

class MakefileFields(BaseModel):
    analysis: str
    updated_makefile: str

    def to_dict(self):
        return {
            "analysis": self.analysis,
            "updated_makefile": self.updated_makefile
        }