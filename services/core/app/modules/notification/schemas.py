from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

LearningEventType = Literal[
    "ASSESSMENT_RESULT_RELEASED", "GRADE_PROPOSAL_PENDING", "APPEAL_RESOLVED"
]
LearningResourceType = Literal["assessment_attempt", "grade_revision", "appeal"]
LEARNING_RESOURCE_BY_EVENT = {
    "ASSESSMENT_RESULT_RELEASED": "assessment_attempt",
    "GRADE_PROPOSAL_PENDING": "grade_revision",
    "APPEAL_RESOLVED": "appeal",
}


class LearningNotificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_event_id: UUID
    event_type: LearningEventType
    school_id: UUID
    class_id: UUID
    resource_type: LearningResourceType
    resource_id: UUID
    student_id: UUID | None = None

    @model_validator(mode="after")
    def validate_resource_and_recipient(self) -> Self:
        if self.resource_type != LEARNING_RESOURCE_BY_EVENT[self.event_type]:
            raise ValueError("Resource type does not match the event type")
        if (self.student_id is None) != (self.event_type == "GRADE_PROPOSAL_PENDING"):
            raise ValueError("Student ID does not match the event type")
        return self


class LearningNotificationReceipt(BaseModel):
    outbox_event_id: UUID
    deduped: bool


class NotificationResponse(BaseModel):
    id: UUID
    type: str
    payload_json: dict
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    unread_count: int
    next_cursor: str | None = None


class MarkReadResponse(BaseModel):
    id: UUID
    read: bool
