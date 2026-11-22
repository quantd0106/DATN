from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    model_validator,
)

from app.modules.bank.grading_engine import normalize_question_type

GradingPolicy = Literal["BEST", "LATEST", "AVERAGE"]
QuestionId = Annotated[str, Field(min_length=1, max_length=100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RevisionRequest(InputModel):
    expected_revision: int = Field(ge=1)


class AssessmentSettings(InputModel):
    title: str = Field(min_length=1, max_length=255)
    topic: str = Field(default="", max_length=200)
    code: str = Field(default="", max_length=64)
    opens_at: AwareDatetime = Field(validation_alias=AliasChoices("opens_at", "opensAt"))
    closes_at: AwareDatetime = Field(validation_alias=AliasChoices("closes_at", "closesAt"))
    duration_minutes: int = Field(
        gt=0, le=525600, validation_alias=AliasChoices("duration_minutes", "durationMinutes")
    )
    max_attempts: int = Field(ge=1, validation_alias=AliasChoices("max_attempts", "maxAttempts"))
    grading_policy: GradingPolicy = Field(
        validation_alias=AliasChoices("grading_policy", "gradingPolicy")
    )
    results_at: AwareDatetime | None = Field(
        default=None, validation_alias=AliasChoices("results_at", "resultsAt")
    )
    appeal_window_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def valid_window(self):
        if self.opens_at >= self.closes_at:
            raise ValueError("Invalid assessment window")
        return self


class Choice(InputModel):
    id: QuestionId
    text: str = Field(min_length=1)
    label: str = ""


class Question(InputModel):
    id: QuestionId
    type: Literal["single_choice", "multiple_choice", "true_false", "true_false_4", "short_answer"]
    prompt: str = Field(min_length=1)
    points: FiniteFloat = Field(gt=0, le=9999.99)
    key: str | list[str] | dict[str, bool] | None = None
    options: list[Choice] = Field(default_factory=list)
    statements: list[Choice] = Field(default_factory=list)
    explanation: str = ""
    rubric: str = ""
    reviewed: bool = Field(default=False, validation_alias=AliasChoices("reviewed", "isReviewed"))
    ai_proposed: bool = Field(
        default=False, validation_alias=AliasChoices("ai_proposed", "aiProposed")
    )
    manual_grading_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("manual_grading_required", "manualGradingRequired"),
    )
    accepted_answers: list[str] = Field(default_factory=list, max_length=5)
    tolerance: FiniteFloat = Field(default=1e-6, ge=0)
    source: dict | None = None
    topic: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def unique_choices(self):
        self.type = normalize_question_type(self.type)
        for choices in (self.options, self.statements):
            if len({choice.id for choice in choices}) != len(choices):
                raise ValueError("Duplicate choice")
        if self.type == "true_false" and len(self.statements) > 4:
            raise ValueError("Too many statements")
        return self


class SaveDraftRequest(RevisionRequest):
    settings: AssessmentSettings
    questions: list[Question] = Field(max_length=500)

    @model_validator(mode="after")
    def valid_questions(self):
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Duplicate question")
        if sum(q.points for q in self.questions) > 9999.99:
            raise ValueError("Question points exceed grade range")
        return self


class PublishDecision(RevisionRequest):
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "REJECT" and not self.reason:
            raise ValueError("Rejection needs a reason")
        return self


class Answer(InputModel):
    question_id: QuestionId | None = Field(
        default=None, validation_alias=AliasChoices("question_id", "questionId")
    )
    value: str | list[str] | dict[str, bool]


class SaveAnswersRequest(InputModel):
    expected_revision: int = Field(ge=0)
    answers: dict[QuestionId, Answer]


class SubmitRequest(InputModel):
    expected_revision: int = Field(ge=0)
    operation_id: UUID


class AssessmentReceipt(BaseModel):
    id: UUID
    class_id: UUID
    assessment_id: UUID
    attempt_id: UUID
    version_id: UUID
    version_number: int
    reason: Literal["MANUAL", "TIMEOUT"]
    submitted_at: datetime
    saved_answer_count: int
    total_question_count: int
    saved_revision: int
    result_released: bool = False
