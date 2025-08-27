from pydantic import BaseModel
from typing import Optional

class MakefileFields(BaseModel):
    LINK: Optional[list[str]]
    H_CBMCFLAGS: Optional[list[str]]
    H_DEF: Optional[list[str]]
    H_INC: Optional[list[str]]