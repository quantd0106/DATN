from sqlalchemy import MetaData

from app.common.audit import AuditEvent
from app.common.db import Base
from app.common.idempotency import IdempotencyRecord
from app.common.outbox import OutboxEvent
from app.modules.appeals.models import Appeal
from app.modules.assessment.models import AssessmentAttempt, AttemptAnswer
from app.modules.assignment.models import AssignmentSubmission, AssignmentUpload
from app.modules.bank.models import (
    Activity,
    ActivityAudience,
    ActivityVersion,
    DueOverride,
    QuestionItem,
)
from app.modules.grading.models import Grade, GradeRevision, ScoringAdjustment
from app.modules.practice.models import PracticeAnswer, PracticeAttempt

__all__ = [
    "Activity",
    "ActivityAudience",
    "ActivityVersion",
    "Appeal",
    "AssessmentAttempt",
    "AssignmentSubmission",
    "AssignmentUpload",
    "AttemptAnswer",
    "AuditEvent",
    "DueOverride",
    "Grade",
    "GradeRevision",
    "IdempotencyRecord",
    "OutboxEvent",
    "PracticeAnswer",
    "PracticeAttempt",
    "QuestionItem",
    "ScoringAdjustment",
    "load_metadata",
]


def load_metadata() -> MetaData:
    return Base.metadata
