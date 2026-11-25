from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreatePracticeRequest(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    topic: str = Field(default="", max_length=200)


class CopyActivityRequest(RequestModel):
    target_class_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=255)


class SavePracticeDraftRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    topic: str | None = Field(default=None, max_length=200)
    questions: list[dict[str, Any]] = Field(default_factory=list)


class AttachBankItemsRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    learning_item_ids: list[UUID] = Field(min_length=1, max_length=100)


class ReviewPracticeQuestionRequest(RequestModel):
    expected_revision: int = Field(ge=1)
    approved: bool


class PublishPracticeRequest(RequestModel):
    expected_revision: int = Field(ge=1)


class StartPracticeAttemptRequest(RequestModel):
    intent: Literal["start", "resume", "retry"] = "start"


class SavePracticeAttemptDraftRequest(RequestModel):
    answers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    current_question_index: int | None = Field(default=None, ge=0)


class CheckPracticeAnswerRequest(RequestModel):
    question_id: str = Field(min_length=1, max_length=100)
    answer: dict[str, Any]


class ExplainMistakeRequest(RequestModel):
    question_id: str = Field(min_length=1, max_length=100)
