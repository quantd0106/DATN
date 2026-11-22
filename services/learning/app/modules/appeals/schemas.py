from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_type: Literal["ASSESSMENT_ATTEMPT", "ASSIGNMENT_SUBMISSION"]
    target_id: UUID
    question_id: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    reason: str = Field(min_length=1, max_length=4000)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    resolution_note: str = Field(min_length=1, max_length=4000)
    linked_revision_id: UUID | None = None
