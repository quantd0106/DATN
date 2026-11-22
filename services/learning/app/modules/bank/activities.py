from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import ensure_utc
from app.common.errors import AppError
from app.common.outbox import record_outbox_event
from app.modules.bank.models import Activity, ActivityVersion


def get_activity(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    activity_id: UUID,
    kind: str,
    *,
    lock: bool = False,
) -> Activity:
    query = select(Activity).where(
        Activity.id == activity_id,
        Activity.class_id == class_id,
        Activity.school_id == school_id,
        Activity.kind == kind,
    )
    if lock:
        query = query.with_for_update(key_share=True).execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        raise AppError(404)
    return row


def get_version(session: Session, activity: Activity, version_id: UUID | None) -> ActivityVersion:
    version = session.get(ActivityVersion, version_id) if version_id is not None else None
    if version is None or version.activity_id != activity.id:
        raise AppError(404)
    return version


def create_activity(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    kind: str,
    actor_id: UUID,
) -> tuple[Activity, ActivityVersion]:
    activity = Activity(school_id=school_id, class_id=class_id, kind=kind, created_by=actor_id)
    session.add(activity)
    session.flush()
    version = ActivityVersion(activity_id=activity.id, created_by=actor_id)
    session.add(version)
    session.flush()
    activity.draft_version_id = version.id
    return activity, version


def record_deadline(
    session: Session,
    activity: Activity,
    version: ActivityVersion,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    *,
    student_id: UUID | None = None,
) -> None:
    payload = {
        "school_id": str(activity.school_id),
        "class_id": str(activity.class_id),
        "external_ref": f"learning:activity:{activity.id}",
        "title": title[:200],
        "starts_at": ensure_utc(starts_at).isoformat(),
        "ends_at": ensure_utc(ends_at).isoformat(),
        "version": version.deadline_version,
    }
    event_type = "calendar.deadline.upsert"
    if student_id is not None:
        # The Core calendar projection is class-wide. Retain the scoped event in Learning.
        event_type = "assignment.deadline.extended"
        payload["student_id"] = str(student_id)
    record_outbox_event(
        session,
        event_type=event_type,
        aggregate_type="activity",
        aggregate_id=activity.id,
        aggregate_version=version.deadline_version,
        payload_json=payload,
    )


def record_publication(session: Session, activity: Activity, version: ActivityVersion) -> None:
    settings = version.settings_json
    record_outbox_event(
        session,
        event_type="activity.version.published",
        aggregate_type="activity",
        aggregate_id=activity.id,
        aggregate_version=version.version_number,
        payload_json={
            "school_id": str(activity.school_id),
            "class_id": str(activity.class_id),
            "activity_id": str(activity.id),
            "kind": activity.kind,
            "version_number": version.version_number,
            "due_at": settings.get("due_at"),
            "closes_at": settings.get("closes_at"),
            "deadline_version": version.deadline_version,
            "published_at": ensure_utc(version.published_at).isoformat(),
        },
    )
