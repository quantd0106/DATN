from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from app.modules.grading.models import AdjustmentAction

Score = Annotated[FiniteFloat, Field(ge=0, le=9999.99)]
Reason = Annotated[str, Field(min_length=1, max_length=4000)]
Feedback = Annotated[str, Field(max_length=10000)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExpectedGrade(RequestModel):
    expected_grade_version: int = Field(ge=0)


class AssignmentGradeRequest(ExpectedGrade):
    score: Score
    feedback: Feedback = ""


class AssessmentGradeRequest(ExpectedGrade):
    scores: dict[str, Score] = Field(min_length=1)
    feedback: Feedback = ""


class ReleaseRequest(RequestModel):
    reason: str = Field(default="", max_length=4000)
    release_now: bool = False


class RevisionRequest(ExpectedGrade):
    scores: dict[str, Score] | None = None
    total_score: Score | None = None
    feedback: Feedback = ""
    reason: Reason
    appeal_id: UUID | None = None


class AdjustmentDecision(RequestModel):
    decision: Literal["APPROVE", "REJECT"]
    reason: Reason


class RevisionDecision(AdjustmentDecision, ExpectedGrade):
    pass


class AdjustmentRequest(RequestModel):
    question_id: str = Field(min_length=1, max_length=100)
    action: AdjustmentAction
    payload: dict = Field(default_factory=dict)
    reason: Reason
