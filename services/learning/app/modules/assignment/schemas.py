from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RevisionRequest(InputModel):
    expected_revision: int = Field(ge=1)


class AssignmentPolicy(InputModel):
    max_versions: int = Field(
        default=1, ge=1, validation_alias=AliasChoices("max_versions", "maxVersions")
    )
    allow_late: bool = Field(
        default=False, validation_alias=AliasChoices("allow_late", "allowLate")
    )
    late_until: AwareDatetime | None = Field(
        default=None, validation_alias=AliasChoices("late_until", "lateUntil")
    )
    resubmit_until: AwareDatetime | None = Field(
        default=None, validation_alias=AliasChoices("resubmit_until", "resubmitUntil")
    )
    graded_version: Literal["LATEST", "FIRST"] = Field(
        default="LATEST", validation_alias=AliasChoices("graded_version", "gradedVersion")
    )
    max_bytes: int | None = Field(
        default=None, gt=0, validation_alias=AliasChoices("max_bytes", "maxBytes")
    )
    accepted_types: list[str] = Field(
        default_factory=list,
        max_length=25,
        validation_alias=AliasChoices("accepted_types", "acceptedTypes"),
    )
    new_enrollment: Literal["AUTO", "REVIEW"] = Field(
        default="AUTO", validation_alias=AliasChoices("new_enrollment", "newEnrollment")
    )
    appeal_window_days: int | None = Field(default=None, ge=1, le=365)
    max_score: float = Field(default=10.0, gt=0, le=9999.99, allow_inf_nan=False)


class SaveDraftRequest(RevisionRequest):
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(default="", max_length=200)
    instructions: str = Field(default="", max_length=20000)
    due_at: AwareDatetime
    policy: AssignmentPolicy
    source: dict | None = None
    publish: bool = False
    appeal_window_days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def validate_late_window(self):
        if self.policy.late_until is not None and self.policy.late_until < self.due_at:
            raise ValueError("Late cutoff precedes due date")
        return self


class RecipientsRequest(RevisionRequest):
    student_ids: list[UUID]


class DueOverrideRequest(InputModel):
    student_id: UUID | None = None
    new_due_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=4000)


class SubmitRequest(RevisionRequest):
    upload_id: UUID
    operation_id: UUID
    note: str = Field(default="", max_length=10000)
