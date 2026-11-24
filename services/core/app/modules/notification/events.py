"""Payload allowlist and recipient policy for in-app notification producers."""

import hashlib
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.common.outbox import OUTBOX_SCHEMA_VERSION, OutboxEvent
from app.modules.classroom.public import (
    ClassAccessAction,
    check_class_action,
    get_class_lecturer_ids,
    get_notification_audience,
)
from app.modules.identity.public import check_user_is_lecturer, get_users_summaries
from app.modules.notification.schemas import LEARNING_RESOURCE_BY_EVENT, LearningResourceType
from app.modules.social.calendar.public import get_visible_calendar_event_ref
from app.modules.social.feed.public import get_visible_post_ref


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: UUID
    school_id: UUID | None = None
    recipient_ids: list[UUID] | None = None


class ClassChanged(EventPayload):
    version: int = Field(ge=1)
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


class StudentTransferred(EventPayload):
    destination_class_id: UUID
    student_id: UUID
    effective_at: AwareDatetime
    version: int | None = Field(default=None, ge=1)


class HomeroomTransferred(EventPayload):
    destination_homeroom_id: UUID
    student_id: UUID
    effective_at: AwareDatetime


class PostCreated(EventPayload):
    post_id: UUID
    author_id: UUID


class CommentCreated(PostCreated):
    comment_id: UUID
    post_author_id: UUID


class DeadlineChanged(EventPayload):
    event_id: UUID
    external_ref: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    activity_kind: Literal["ASSIGNMENT", "ASSESSMENT"] | None = None


class CalendarEventChanged(EventPayload):
    calendar_event_id: UUID
    actor_id: UUID
    version: int = Field(ge=1)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None


class LearningEvent(EventPayload):
    school_id: UUID
    recipient_ids: None = None
    resource_type: LearningResourceType
    resource_id: UUID
    student_id: UUID | None = None


EVENT_SCHEMAS = {
    "CLASS_ENROLLMENT_CHANGED": ClassChanged,
    "CLASS_ENROLLMENT_MODE_CHANGED": ClassChanged,
    "CLASS_TEACHERS_CHANGED": ClassChanged,
    "CLASS_STATUS_CHANGED": ClassChanged,
    "CLASS_STUDENT_TRANSFERRED": StudentTransferred,
    "HOMEROOM_STUDENT_TRANSFERRED": HomeroomTransferred,
    "SOCIAL_POST_CREATED": PostCreated,
    "SOCIAL_COMMENT_CREATED": CommentCreated,
    "CALENDAR_DEADLINE_CHANGED": DeadlineChanged,
    "CALENDAR_EVENT_CREATED": CalendarEventChanged,
    "CALENDAR_EVENT_UPDATED": CalendarEventChanged,
    "CALENDAR_EVENT_DELETED": CalendarEventChanged,
    **dict.fromkeys(LEARNING_RESOURCE_BY_EVENT, LearningEvent),
}


class InvalidEvent(ValueError):
    pass


def prepare_delivery(session: Session, event: OutboxEvent) -> tuple[list[UUID], dict]:
    if event.schema_version != OUTBOX_SCHEMA_VERSION:
        raise InvalidEvent("Unsupported envelope")
    payload = EVENT_SCHEMAS[event.event_type].model_validate(event.payload_json)
    aggregate_id = (
        payload.comment_id
        if isinstance(payload, CommentCreated)
        else payload.post_id
        if isinstance(payload, PostCreated)
        else payload.event_id
        if isinstance(payload, DeadlineChanged)
        else payload.calendar_event_id
        if isinstance(payload, CalendarEventChanged)
        else payload.resource_id
        if isinstance(payload, LearningEvent)
        else payload.class_id
    )
    if str(aggregate_id) != event.aggregate_id:
        raise InvalidEvent("Mismatched aggregate")
    version = getattr(payload, "version", None)
    if event.aggregate_version is not None and event.aggregate_version != version:
        raise InvalidEvent("Mismatched aggregate version")
    homeroom = isinstance(payload, HomeroomTransferred)
    audience = get_notification_audience(payload.class_id, session=session, homeroom=homeroom)
    if audience is None:
        # No usable source scope: retain the row for operator investigation/retry.
        raise InvalidEvent("Missing source scope")
    if payload.school_id is not None and payload.school_id != audience.school_id:
        raise InvalidEvent("Mismatched school")
    if isinstance(payload, LearningEvent):
        return prepare_learning_delivery(session, event, payload)
    candidates = set(
        payload.recipient_ids if payload.recipient_ids is not None else audience.recipient_ids
    )
    if isinstance(payload, (StudentTransferred, HomeroomTransferred)):
        candidates = {payload.student_id}
    elif isinstance(payload, CommentCreated):
        candidates &= {payload.post_author_id}
    if isinstance(payload, PostCreated):
        candidates.discard(payload.author_id)
        if get_visible_post_ref(payload.class_id, payload.post_id, session=session) is None:
            return [], {}
    elif isinstance(payload, CalendarEventChanged):
        candidates.discard(payload.actor_id)
        if (
            event.event_type != "CALENDAR_EVENT_DELETED"
            and get_visible_calendar_event_ref(
                payload.class_id, payload.calendar_event_id, session=session
            )
            is None
        ):
            return [], {}
    users = get_users_summaries(sorted(candidates), session=session)
    recipients = []
    for user_id in sorted(candidates):
        user = users.get(user_id)
        if user is None or user.school_id != audience.school_id:
            continue
        if (
            isinstance(payload, (PostCreated, DeadlineChanged, CalendarEventChanged))
            and not check_class_action(
                payload.class_id, user_id, ClassAccessAction.READ_CLASS_CONTENT, session=session
            ).allowed
        ):
            continue
        recipients.append(user_id)
    # No free text, credentials, external_ref or recipient lists in inbox payloads.
    safe = {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key, value in payload.model_dump(exclude_none=True).items()
        if key not in {"recipient_ids", "school_id", "post_author_id", "external_ref", "actor_id"}
    }
    if homeroom:
        safe["homeroom_id"] = safe.pop("class_id")
    safe.update(event_id=str(event.id), schema_version=event.schema_version)
    return recipients, safe


def prepare_learning_delivery(
    session: Session, event: OutboxEvent, payload: LearningEvent
) -> tuple[list[UUID], dict]:
    teacher_event = event.event_type == "GRADE_PROPOSAL_PENDING"
    class_student_event = event.event_type == "PRACTICE_PUBLISHED"
    expects_no_student = teacher_event or class_student_event
    if (
        payload.resource_type != LEARNING_RESOURCE_BY_EVENT[event.event_type]
        or payload.resource_type != event.aggregate_type
        or (payload.student_id is None) != expects_no_student
    ):
        raise InvalidEvent("Mismatched Learning resource or recipient")
    if teacher_event:
        candidates = set(get_class_lecturer_ids(payload.class_id, session=session))
    elif class_student_event:
        audience = get_notification_audience(payload.class_id, session=session)
        if audience is None:
            raise InvalidEvent("Missing source scope")
        if audience.school_id != payload.school_id:
            raise InvalidEvent("Mismatched school")
        lecturer_ids = set(get_class_lecturer_ids(payload.class_id, session=session))
        candidates = set(audience.recipient_ids) - lecturer_ids
    else:
        candidates = {payload.student_id}
    users = get_users_summaries(sorted(candidates), session=session)
    recipients = []
    for user_id, user in sorted(users.items()):
        if user.school_id != payload.school_id:
            continue
        allowed = (
            check_user_is_lecturer(user_id, session=session)
            if teacher_event
            else check_class_action(
                payload.class_id,
                user_id,
                ClassAccessAction.READ_CLASS_CONTENT
                if class_student_event
                else ClassAccessAction.READ_OWN_HISTORY,
                session=session,
            ).allowed
        )
        if allowed:
            recipients.append(user_id)
    safe = payload.model_dump(mode="json", include={"class_id", "resource_type", "resource_id"})
    safe.update(event_id=str(event.id), schema_version=event.schema_version)
    return recipients, safe


def dedup_key(event: OutboxEvent, recipient_id: UUID) -> str:
    # An event retry reuses its UUID; versioned producer replay is also deduped.
    version = event.aggregate_version or event.payload_json.get("version")
    identity = f"{event.aggregate_id}:{version}" if version is not None else str(event.id)
    return hashlib.sha256(
        f"v{event.schema_version}:{event.event_type}:{identity}:{recipient_id}".encode()
    ).hexdigest()
