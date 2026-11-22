from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class AssessmentLock(BaseModel):
    user_id: UUID
    school_id: UUID
    active_official_assessment: StrictBool


class QuizImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID
    school_id: UUID
    idempotency_key: UUID
    item: dict[str, Any]
    level: str = Field(min_length=1, max_length=100)
    generated_by: Literal["AI"]
    approved_by: UUID
    edited_after_generation: StrictBool
    provenance: dict[str, Any]
    sources: list[dict[str, Any]]


class QuizImportReceipt(BaseModel):
    class_id: UUID
    school_id: UUID
    idempotency_key: UUID
    learning_item_id: UUID
