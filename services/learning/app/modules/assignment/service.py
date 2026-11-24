from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.db import ensure_utc, utcnow, write_transaction
from app.common.errors import AppError
from app.common.outbox import record_learning_notification
from app.integrations.core_client import CurrentUser, TeachingRole
from app.integrations.document_client import GrantSnapshot
from app.modules.appeals import service as appeals
from app.modules.assignment.models import AssignmentSubmission, AssignmentUpload
from app.modules.assignment.schemas import (
    AssignmentPolicy,
    DueOverrideRequest,
    SaveDraftRequest,
    SubmitRequest,
)
from app.modules.bank import activities
from app.modules.bank.grading_engine import canonical_content_sha256, mask_current_source
from app.modules.bank.models import (
    Activity,
    ActivityAudience,
    ActivityKind,
    ActivityVersion,
    DueOverride,
    VersionStatus,
)
from app.modules.grading import service as grading
from app.storage.blob_store import BlobStore, validate_upload

KIND = ActivityKind.ASSIGNMENT
TARGET = "ASSIGNMENT_SUBMISSION"
UPLOAD_TTL = timedelta(hours=24)


def create(session: Session, school_id: UUID, class_id: UUID, actor: CurrentUser) -> dict:
    with write_transaction(session):
        act, ver = activities.create_activity(session, school_id, class_id, KIND, actor.id)
        return {"assignment_id": act.id, "revision": ver.revision}


def _editor(session: Session, act: Activity) -> ActivityVersion:
    return activities.get_version(session, act, act.draft_version_id or act.current_version_id)


def _snapshot(act: Activity, ver: ActivityVersion) -> dict:
    return {
        **ver.settings_json,
        "id": act.id,
        "assignment_id": act.id,
        "class_id": act.class_id,
        "version_id": ver.id,
        "revision": ver.revision,
        "version_number": ver.version_number,
        "deadline_version": ver.deadline_version,
        "status": ver.status,
    }


def _available_sources(grants: GrantSnapshot | None) -> list[dict]:
    if grants is None:
        return []
    return [
        {
            "id": str(grant.document_id),
            "document_id": str(grant.document_id),
            "document_version_id": str(grant.document_version_id),
            "source_id": grant.source_id,
            "title": grant.title or f"Tài liệu {str(grant.document_id)[:8]}",
            "version": int(grant.version_number or 1),
            "locator": "",
            "accessible": True,
        }
        for grant in grants.grants
    ]


def _resolve_granted_source(source: dict | None, grants: GrantSnapshot | None) -> dict | None:
    if not source:
        return None
    if grants is None:
        raise AppError(403, "SOURCE_ACCESS_DENIED")
    raw_doc_id = source.get("document_id") or source.get("documentId") or source.get("id")
    raw_ver = source.get("version_number") or source.get("versionNumber") or source.get("version")
    raw_ver_id = source.get("document_version_id") or source.get("documentVersionId")
    raw_source_id = source.get("source_id") or source.get("sourceId")
    if not raw_doc_id:
        raise AppError(403, "SOURCE_ACCESS_DENIED")
    for grant in grants.grants:
        if str(grant.document_id).lower() != str(raw_doc_id).lower():
            continue
        if raw_ver is not None and int(grant.version_number or 1) != int(raw_ver):
            continue
        if raw_ver_id is not None and str(grant.document_version_id).lower() != str(raw_ver_id).lower():
            continue
        if raw_source_id is not None and str(grant.source_id) != str(raw_source_id):
            continue
        return {
            "id": str(grant.document_id),
            "document_id": str(grant.document_id),
            "document_version_id": str(grant.document_version_id),
            "source_id": grant.source_id,
            "title": grant.title
            or str(source.get("title") or f"Tài liệu {str(grant.document_id)[:8]}"),
            "version": int(grant.version_number or 1),
            "locator": str(source.get("locator") or ""),
            "accessible": True,
        }
    raise AppError(403, "SOURCE_ACCESS_DENIED")


def save_draft(
    session: Session,
    scope: tuple,
    actor: CurrentUser,
    body: SaveDraftRequest,
    *,
    grants: GrantSnapshot | None = None,
) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = _editor(session, act)
        if ver.revision != body.expected_revision:
            raise AppError(409, "REVISION_CONFLICT")
        normalized_source = _resolve_granted_source(body.source, grants) if body.source else None
        previous = (
            activities.get_version(session, act, act.current_version_id)
            if act.current_version_id
            else None
        )
        if ver.status != VersionStatus.DRAFT:
            ver = ActivityVersion(
                activity_id=act.id,
                version_number=ver.version_number + 1,
                revision=ver.revision,
                created_by=actor.id,
                settings_json=ver.settings_json,
                content_snapshot_json=[],
                deadline_version=ver.deadline_version,
            )
            session.add(ver)
            session.flush()
            act.draft_version_id = ver.id

        ver.settings_json = {
            **ver.settings_json,
            **body.model_dump(mode="json", exclude={"expected_revision", "publish"}),
            "source": normalized_source,
        }
        ver.revision += 1
        if body.publish or previous is None:
            act.title, act.topic, act.instructions = body.title, body.topic, body.instructions
        if body.publish:
            now = utcnow()
            ver.content_sha256 = canonical_content_sha256(
                ver.content_snapshot_json, ver.settings_json
            )
            ver.status, ver.published_at, ver.published_by = VersionStatus.PUBLISHED, now, actor.id
            act.current_version_id, act.draft_version_id = ver.id, None
            if previous is not None:
                previous.status = VersionStatus.SUPERSEDED
                ver.deadline_version = previous.deadline_version
            class_due = effective_due(session, act, ver, None)
            if (
                previous is None
                or class_due != effective_due(session, act, previous, None)
                or body.title != previous.settings_json.get("title")
            ):
                ver.deadline_version += 1
                starts_at = now if now < class_due else class_due - timedelta(microseconds=1)
                activities.record_deadline(session, act, ver, body.title, starts_at, class_due)
            activities.record_publication(session, act, ver)
        return _snapshot(act, ver)


def set_recipients(
    session: Session, scope: tuple, actor: CurrentUser, expected: int, ids: list[UUID]
) -> None:
    if UUID(int=0) in ids:
        raise AppError(422)
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = _editor(session, act)
        if ver.revision != expected:
            raise AppError(409, "REVISION_CONFLICT")
        session.execute(delete(ActivityAudience).where(ActivityAudience.activity_id == act.id))
        session.add_all(
            ActivityAudience(activity_id=act.id, student_id=sid, assigned=True, updated_by=actor.id)
            for sid in set(ids)
        )
        # A defined empty audience differs from an assignment open to the whole class.
        if not ids:
            session.add(
                ActivityAudience(
                    activity_id=act.id,
                    student_id=UUID(int=0),
                    assigned=False,
                    updated_by=actor.id,
                )
            )
        ver.revision += 1
        if act.current_version_id and act.current_version_id != ver.id:
            current = activities.get_version(session, act, act.current_version_id)
            current.revision = ver.revision


def _in_audience(session: Session, act: Activity, ver: ActivityVersion, student_id: UUID) -> bool:
    row = session.get(ActivityAudience, (act.id, student_id))
    if row is not None:
        return row.assigned
    explicit = session.scalar(
        select(ActivityAudience.activity_id)
        .where(
            ActivityAudience.activity_id == act.id,
        )
        .limit(1)
    )
    return explicit is None and _policy(ver).new_enrollment == "AUTO"


def read_recipients(session: Session, scope: tuple[UUID, UUID, UUID]) -> dict:
    act = activities.get_activity(session, *scope, KIND)
    ver = _editor(session, act)
    rows = list(
        session.scalars(
            select(ActivityAudience)
            .where(ActivityAudience.activity_id == act.id)
            .order_by(ActivityAudience.student_id)
        )
    )
    return {
        "assignment_id": act.id,
        "class_id": act.class_id,
        "revision": ver.revision,
        "audience": [
            {"user_id": row.student_id, "assigned": row.assigned}
            for row in rows
            if row.student_id.int != 0
        ],
        "default_assigned": not rows and _policy(ver).new_enrollment == "AUTO",
    }


def _policy(ver: ActivityVersion) -> AssignmentPolicy:
    return AssignmentPolicy.model_validate(ver.settings_json.get("policy", {}))


def effective_due(
    session: Session, act: Activity, ver: ActivityVersion, student_id: UUID | None
) -> datetime:
    overrides = session.scalars(
        select(DueOverride.new_due_at).where(
            DueOverride.activity_id == act.id,
            or_(DueOverride.student_id.is_(None), DueOverride.student_id == student_id),
        )
    )
    original = ensure_utc(datetime.fromisoformat(ver.settings_json["due_at"]))
    return max((original, *(ensure_utc(value) for value in overrides)))


def extend_due(
    session: Session, scope: tuple, actor: CurrentUser, body: DueOverrideRequest
) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.current_version_id)
        now = utcnow()
        if body.new_due_at <= max(now, effective_due(session, act, ver, body.student_id)):
            raise AppError(422)
        if body.student_id and not _in_audience(session, act, ver, body.student_id):
            raise AppError(403, "NOT_IN_AUDIENCE")
        row = DueOverride(
            activity_id=act.id,
            student_id=body.student_id,
            new_due_at=body.new_due_at,
            reason=body.reason,
            created_by=actor.id,
            applies_to_future_only=True,
        )
        session.add(row)
        ver.deadline_version += 1
        activities.record_deadline(
            session,
            act,
            ver,
            act.title,
            now,
            body.new_due_at,
            student_id=body.student_id,
        )
        return {
            "id": row.id,
            "assignment_id": act.id,
            "student_id": body.student_id,
            "new_due_at": body.new_due_at,
            "deadline_version": ver.deadline_version,
            "applies_to_future_only": True,
        }


def _submissions(
    session: Session, activity_id: UUID, student_id: UUID
) -> list[AssignmentSubmission]:
    return list(
        session.scalars(
            select(AssignmentSubmission)
            .where(
                AssignmentSubmission.activity_id == activity_id,
                AssignmentSubmission.student_id == student_id,
            )
            .order_by(AssignmentSubmission.submission_number)
        )
    )


def _submission_window(
    session: Session,
    act: Activity,
    ver: ActivityVersion,
    student_id: UUID,
    existing: list[AssignmentSubmission],
    now: datetime,
) -> bool:
    if not _in_audience(session, act, ver, student_id):
        raise AppError(403, "NOT_IN_AUDIENCE")
    policy = _policy(ver)
    if len(existing) >= policy.max_versions:
        raise AppError(409, "SUBMISSION_LIMIT_REACHED")
    if existing and policy.resubmit_until is not None and now > policy.resubmit_until:
        raise AppError(409, "RESUBMISSION_CLOSED")
    late = now > effective_due(session, act, ver, student_id)
    if late and (
        not policy.allow_late or (policy.late_until is not None and now > policy.late_until)
    ):
        raise AppError(409, "LATE_SUBMISSION_NOT_ALLOWED")
    return late


def _student_snapshot(
    session: Session,
    act: Activity,
    ver: ActivityVersion,
    student_id: UUID,
    grants: GrantSnapshot | None,
) -> dict:
    view = _snapshot(act, ver)
    view["due_at"] = effective_due(session, act, ver, student_id)
    if view.get("source"):
        view["source"] = mask_current_source(view["source"], grants)
    return view


def list_assignments(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    student_id: UUID | None,
    grants: GrantSnapshot | None,
) -> dict:
    query = select(Activity).where(
        Activity.school_id == school_id,
        Activity.class_id == class_id,
        Activity.kind == KIND,
    )
    if student_id is not None:
        query = query.where(Activity.current_version_id.is_not(None))
    items = []
    for act in session.scalars(query.order_by(Activity.created_at, Activity.id)):
        if student_id is None:
            items.append(_snapshot(act, _editor(session, act)))
            continue
        ver = activities.get_version(session, act, act.current_version_id)
        if _in_audience(session, act, ver, student_id):
            items.append(_student_snapshot(session, act, ver, student_id, grants))
    return {"items": items}


def detail(
    session: Session,
    scope: tuple,
    student_id: UUID | None,
    grants: GrantSnapshot | None,
    *,
    active: bool,
) -> dict:
    act = activities.get_activity(session, *scope, KIND)
    if student_id is None:
        return {
            "assignment": _snapshot(act, _editor(session, act)),
            "available_sources": _available_sources(grants),
            "submissions": [],
            "can_submit": False,
        }
    ver = activities.get_version(session, act, act.current_version_id)
    if not _in_audience(session, act, ver, student_id):
        raise AppError(403, "NOT_IN_AUDIENCE")
    submissions = _submissions(session, act.id, student_id)
    blocked = None
    try:
        _submission_window(session, act, ver, student_id, submissions, utcnow())
    except AppError as exc:
        blocked = exc.detail
    return {
        "assignment": _student_snapshot(session, act, ver, student_id, grants),
        "submissions": [submission_view(session, row) for row in submissions],
        "can_submit": active and blocked is None,
        "submit_blocked_reason": blocked,
    }


def _upload_limit(policy: AssignmentPolicy, max_bytes: int) -> int:
    return min(policy.max_bytes or max_bytes, max_bytes)


def upload(
    session: Session,
    scope: tuple,
    actor: CurrentUser,
    store: BlobStore,
    *,
    expected: int,
    file_name: str,
    mime_type: str,
    content: bytes,
    max_bytes: int,
) -> dict:
    stored = None
    try:
        with write_transaction(session):
            act = activities.get_activity(session, *scope, KIND, lock=True)
            ver = activities.get_version(session, act, act.current_version_id)
            if ver.revision != expected:
                raise AppError(409, "REVISION_CONFLICT")
            now = utcnow()
            _submission_window(
                session, act, ver, actor.id, _submissions(session, act.id, actor.id), now
            )
            policy = _policy(ver)
            stored = store.put(
                file_name=file_name,
                mime_type=mime_type,
                content=content,
                max_bytes=_upload_limit(policy, max_bytes),
                accepted_types=policy.accepted_types,
            )
            row = AssignmentUpload(
                school_id=act.school_id,
                class_id=act.class_id,
                activity_id=act.id,
                student_id=actor.id,
                blob_ref=stored.blob_ref,
                file_name=stored.file_name,
                mime_type=stored.mime_type,
                file_size_bytes=stored.file_size_bytes,
                file_sha256=stored.file_sha256,
                expires_at=now + UPLOAD_TTL,
                created_at=now,
            )
            session.add(row)
            session.flush()
            return {
                "upload_id": row.id,
                "id": row.id,
                "assignment_id": act.id,
                "file": _file_view(row, row.id),
                "acknowledged_at": now,
                "expires_at": row.expires_at,
                "preview_only": True,
            }
    except Exception:
        if stored is not None:
            store.delete(stored.blob_ref)
        raise


def _file_view(row, upload_id: UUID) -> dict:
    return {
        "id": upload_id,
        "name": row.file_name,
        "type": row.mime_type,
        "size": row.file_size_bytes,
    }


def submission_view(session: Session, row: AssignmentSubmission) -> dict:
    grade = grading.find_target_grade(session, TARGET, row.id)
    return {
        "id": row.id,
        "submission_id": row.id,
        "assignment_id": row.activity_id,
        "class_id": row.class_id,
        "result_id": row.id,
        "student_id": row.student_id,
        "student_name": row.student_name,
        "number": row.submission_number,
        "submission_number": row.submission_number,
        "assignment_revision": row.assignment_revision,
        "operation_id": row.operation_id,
        "submitted_at": ensure_utc(row.submitted_at),
        "file": _file_view(row, row.upload_id),
        "note": row.note,
        "is_late": row.is_late,
        "is_graded_version": row.is_graded_version,
        "grade": grading.grade_view(grade, grading.effective_revision(session, grade))
        if grade
        else None,
        "grade_history": grading.approved_history(session, grade) if grade else [],
    }


def _by_operation(session: Session, operation_id: UUID):
    return session.scalar(
        select(AssignmentSubmission).where(AssignmentSubmission.operation_id == operation_id)
    )


def submit(
    session: Session,
    scope: tuple,
    actor: CurrentUser,
    body: SubmitRequest,
    store: BlobStore,
    max_bytes: int,
) -> dict:
    try:
        with write_transaction(session):
            act = activities.get_activity(session, *scope, KIND, lock=True)
            replay = _by_operation(session, body.operation_id)
            if replay is not None:
                if (
                    replay.activity_id,
                    replay.student_id,
                    replay.upload_id,
                    replay.assignment_revision,
                    replay.note,
                ) != (act.id, actor.id, body.upload_id, body.expected_revision, body.note):
                    raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
                return submission_view(session, replay)

            ver = activities.get_version(session, act, act.current_version_id)
            if ver.revision != body.expected_revision:
                raise AppError(409, "REVISION_CONFLICT")
            now = utcnow()
            existing = _submissions(session, act.id, actor.id)
            late = _submission_window(session, act, ver, actor.id, existing, now)
            up = session.scalar(
                select(AssignmentUpload)
                .where(
                    AssignmentUpload.id == body.upload_id,
                    AssignmentUpload.activity_id == act.id,
                    AssignmentUpload.school_id == act.school_id,
                    AssignmentUpload.class_id == act.class_id,
                    AssignmentUpload.student_id == actor.id,
                )
                .with_for_update()
            )
            if up is None:
                raise AppError(404)
            if ensure_utc(up.expires_at) <= now or any(row.upload_id == up.id for row in existing):
                raise AppError(409)
            policy = _policy(ver)
            content = store.get(up.blob_ref)
            if sha256(content).hexdigest() != up.file_sha256:
                raise AppError(409, "INVALID_UPLOAD_FILE")
            validate_upload(
                file_name=up.file_name,
                mime_type=up.mime_type,
                content=content,
                max_bytes=_upload_limit(policy, max_bytes),
                accepted_types=policy.accepted_types,
            )
            latest = policy.graded_version == "LATEST"
            for row in existing:
                row.is_graded_version = not latest and row.submission_number == 1
            row = AssignmentSubmission(
                school_id=act.school_id,
                class_id=act.class_id,
                activity_id=act.id,
                activity_version_id=ver.id,
                student_id=actor.id,
                student_name=actor.display_name or "",
                submission_number=len(existing) + 1,
                assignment_revision=ver.revision,
                upload_id=up.id,
                blob_ref=up.blob_ref,
                file_name=up.file_name,
                mime_type=up.mime_type,
                file_size_bytes=up.file_size_bytes,
                note=body.note,
                operation_id=body.operation_id,
                is_late=late,
                is_graded_version=latest or not existing,
                submitted_at=now,
            )
            session.add(row)
            session.flush()
            return submission_view(session, row)
    except IntegrityError:
        if _by_operation(session, body.operation_id) is not None:
            raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH") from None
        raise


def reconcile(session: Session, scope: tuple, student_id: UUID, operation_id: UUID) -> dict | None:
    act = activities.get_activity(session, *scope, KIND)
    row = _by_operation(session, operation_id)
    if row is None or (row.activity_id, row.student_id) != (act.id, student_id):
        return None
    return submission_view(session, row)


def find_submission(
    session: Session,
    school_id: UUID,
    submission_id: UUID,
    *,
    lock: bool = False,
) -> AssignmentSubmission:
    query = select(AssignmentSubmission).where(
        AssignmentSubmission.id == submission_id,
        AssignmentSubmission.school_id == school_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = session.scalar(query)
    if row is None:
        raise AppError(404)
    return row


def submission_version(session: Session, row: AssignmentSubmission) -> ActivityVersion:
    act = activities.get_activity(session, row.school_id, row.class_id, row.activity_id, KIND)
    return activities.get_version(session, act, row.activity_version_id)


def read_grading(
    session: Session,
    scope: tuple[UUID, UUID, UUID],
    submission_id: UUID,
    teaching_role: TeachingRole | None,
) -> dict:
    school_id, class_id, assignment_id = scope
    row = find_submission(session, school_id, submission_id)
    if (row.class_id, row.activity_id) != (class_id, assignment_id):
        raise AppError(404)
    act = activities.get_activity(session, *scope, KIND)
    ver = activities.get_version(session, act, row.activity_version_id)
    submissions = [
        submission_view(session, item) for item in _submissions(session, act.id, row.student_id)
    ]
    return {
        "assignment": _snapshot(act, ver),
        "submission": next(view for view in submissions if view["id"] == row.id),
        "submissions": submissions,
        "appeals": appeals.assignment_appeals(
            session, school_id, class_id, row.student_id, [view["id"] for view in submissions]
        ),
        "teaching_role": teaching_role,
    }


def class_submissions(
    session: Session, school_id: UUID, class_id: UUID
) -> list[AssignmentSubmission]:
    return list(
        session.scalars(
            select(AssignmentSubmission).where(
                AssignmentSubmission.school_id == school_id,
                AssignmentSubmission.class_id == class_id,
            )
        )
    )


def activity_has_submissions(session: Session, activity_id: UUID) -> bool:
    return (
        session.scalar(
            select(AssignmentSubmission.id)
            .where(AssignmentSubmission.activity_id == activity_id)
            .limit(1)
        )
        is not None
    )


def grade_submission(
    session: Session, school_id: UUID, submission_id: UUID, actor: CurrentUser, body
) -> dict:
    with write_transaction(session):
        row = find_submission(session, school_id, submission_id, lock=True)
        if body.expected_grade_version != 0:
            raise AppError(409, "GRADE_VERSION_CONFLICT")
        policy = _policy(submission_version(session, row))
        grade = grading.record_initial_grade(
            session,
            school_id=school_id,
            class_id=row.class_id,
            activity_id=row.activity_id,
            student_id=row.student_id,
            target_type=TARGET,
            target_id=row.id,
            scores=None,
            total_score=body.score,
            max_score=policy.max_score,
            feedback=body.feedback,
            actor_id=actor.id,
            actor_name=actor.display_name or "",
            teacher=True,
        )
        record_learning_notification(
            session,
            event_type="ASSIGNMENT_GRADED",
            school_id=row.school_id,
            class_id=row.class_id,
            resource_type="assignment_submission",
            resource_id=row.id,
            student_id=row.student_id,
        )
        return grading.grade_view(grade, grading.effective_revision(session, grade))


def discard_expired_uploads(session: Session, store: BlobStore) -> int:
    attached = select(AssignmentSubmission.id).where(
        AssignmentSubmission.upload_id == AssignmentUpload.id
    )
    query = (
        select(AssignmentUpload)
        .where(
            AssignmentUpload.expires_at <= utcnow(),
            ~attached.exists(),
        )
        .order_by(AssignmentUpload.id)
        .limit(100)
        .with_for_update(skip_locked=True)
    )
    with write_transaction(session):
        rows = list(session.scalars(query))
        discarded = 0
        for row in rows:
            # A submission may have committed after the sweep query took its snapshot.
            if session.scalar(attached.where(AssignmentUpload.id == row.id).limit(1)) is not None:
                continue
            store.delete(row.blob_ref)
            session.delete(row)
            discarded += 1
        return discarded
