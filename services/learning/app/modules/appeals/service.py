from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.db import ensure_utc, utcnow, write_transaction
from app.common.errors import AppError
from app.common.outbox import record_learning_notification
from app.integrations.core_client import CurrentUser
from app.modules.appeals.models import Appeal, AppealStatus
from app.modules.appeals.schemas import AppealRequest, ResolveRequest
from app.modules.grading import service as grading


def appeal_view(row: Appeal) -> dict:
    return {
        "id": row.id,
        "class_id": row.class_id,
        "student_id": row.student_id,
        "grade_id": row.grade_id,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "question_id": row.question_id,
        "reason": row.reason,
        "status": row.status,
        "created_at": ensure_utc(row.created_at),
        "resolution_note": row.resolution_note,
        "resolved_by": row.resolved_by,
        "resolved_at": ensure_utc(row.resolved_at) if row.resolved_at else None,
    }


def class_appeals(session: Session, school_id: UUID, class_id: UUID) -> list[dict]:
    rows = session.scalars(
        select(Appeal)
        .where(Appeal.school_id == school_id, Appeal.class_id == class_id)
        .order_by(Appeal.created_at.desc(), Appeal.id)
    )
    return [appeal_view(row) for row in rows]


def _target_appeals(session: Session, grade) -> list[Appeal]:
    return list(
        session.scalars(
            select(Appeal)
            .where(
                Appeal.school_id == grade.school_id,
                Appeal.class_id == grade.class_id,
                Appeal.grade_id == grade.id,
                Appeal.student_id == grade.student_id,
            )
            .order_by(Appeal.created_at.desc(), Appeal.id)
        )
    )


def grade_appeals(session: Session, grade) -> list[dict]:
    return [appeal_view(row) for row in _target_appeals(session, grade)]


def assignment_appeals(
    session: Session, school_id: UUID, class_id: UUID, student_id: UUID, submission_ids: list[UUID]
) -> list[dict]:
    rows = session.scalars(
        select(Appeal)
        .where(
            Appeal.school_id == school_id,
            Appeal.class_id == class_id,
            Appeal.student_id == student_id,
            Appeal.target_type == "ASSIGNMENT_SUBMISSION",
            Appeal.target_id.in_(submission_ids),
        )
        .order_by(Appeal.created_at.desc(), Appeal.id)
    )
    return [appeal_view(row) for row in rows]


def within_window(session: Session, grade, default_days: int) -> bool:
    if not grade.is_released or grade.released_at is None:
        return False
    settings = grading.target_version(session, grade).settings_json
    days = (
        settings.get("appeal_window_days")
        or settings.get("policy", {}).get("appeal_window_days")
        or default_days
    )
    released_at = ensure_utc(grade.released_at)
    return released_at <= utcnow() <= released_at + timedelta(days=days)


def summary(session: Session, grade, default_days: int) -> dict:
    rows = _target_appeals(session, grade)
    allowed = within_window(session, grade, default_days)
    return {"can_appeal": allowed, "appeals": [appeal_view(row) for row in rows]}


def create(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    student_id: UUID,
    body: AppealRequest,
    default_days: int,
) -> dict:
    with write_transaction(session):
        grade = grading.find_target_grade(session, body.target_type, body.target_id)
        if grade is None or (grade.school_id, grade.class_id, grade.student_id) != (
            school_id,
            class_id,
            student_id,
        ):
            raise AppError(404)
        grade = grading.scoped_grade(session, grade.id, school_id, lock=True)
        if not grade.is_released:
            raise AppError(404)
        if not within_window(session, grade, default_days):
            raise AppError(403, "APPEAL_WINDOW_EXPIRED")
        if body.question_id is not None and not any(
            q["id"] == body.question_id
            for q in grading.target_version(session, grade).content_snapshot_json
        ):
            raise AppError(404)
        if any(
            row.question_id == body.question_id and row.status != AppealStatus.RESOLVED
            for row in _target_appeals(session, grade)
        ):
            raise AppError(409)
        row = Appeal(
            school_id=school_id,
            class_id=class_id,
            grade_id=grade.id,
            student_id=student_id,
            target_type=body.target_type,
            target_id=body.target_id,
            question_id=body.question_id,
            reason=body.reason,
        )
        session.add(row)
        session.flush()
        return appeal_view(row)


def _locked_appeal(session: Session, school_id: UUID, class_id: UUID, appeal_id: UUID) -> Appeal:
    row = session.scalar(
        select(Appeal)
        .where(
            Appeal.school_id == school_id,
            Appeal.class_id == class_id,
            Appeal.id == appeal_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AppError(404)
    return row


def mark_proposed(session: Session, grade, appeal_id: UUID) -> None:
    row = _locked_appeal(session, grade.school_id, grade.class_id, appeal_id)
    if row.grade_id != grade.id:
        raise AppError(404)
    if row.status == AppealStatus.RESOLVED:
        raise AppError(409)
    row.status = AppealStatus.PROPOSED


def resolve(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    appeal_id: UUID,
    actor: CurrentUser,
    body: ResolveRequest,
    correlation_id: str,
) -> dict:
    with write_transaction(session):
        row = _locked_appeal(session, school_id, class_id, appeal_id)
        if row.status == AppealStatus.RESOLVED:
            raise AppError(409)
        grading.validate_appeal_resolution(session, row.id, row.grade_id, body.linked_revision_id)
        row.status = AppealStatus.RESOLVED
        row.resolution_note, row.resolved_by, row.resolved_at = (
            body.resolution_note,
            actor.id,
            utcnow(),
        )
        record_audit_event(
            session,
            school_id=school_id,
            class_id=class_id,
            actor_id=actor.id,
            actor_roles=actor.roles,
            correlation_id=correlation_id,
            action="APPEAL_RESOLVED",
            target_type="appeal",
            target_id=row.id,
            metadata={"linked_revision_id": str(body.linked_revision_id)}
            if body.linked_revision_id
            else {},
        )
        record_learning_notification(
            session,
            event_type="APPEAL_RESOLVED",
            school_id=school_id,
            class_id=class_id,
            resource_type="appeal",
            resource_id=row.id,
            student_id=row.student_id,
        )
        return appeal_view(row)
