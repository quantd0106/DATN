from datetime import UTC
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from app.common.db import ensure_utc, transaction, utcnow
from app.common.errors import AppError
from app.common.outbox import record_learning_notification, record_outbox_event
from app.integrations.ai_client import AiPort
from app.integrations.core_client import CurrentUser
from app.integrations.document_client import DocumentPort, GrantSnapshot
from app.integrations.document_client import optional_grants as _optional_grants
from app.modules.bank.grading_engine import (
    canonical_content_sha256,
    grade_question,
    mask_current_source,
    normalize_question_type,
    strip_answer_keys,
)
from app.modules.bank.models import (
    Activity,
    ActivityAudience,
    ActivityKind,
    ActivityVersion,
    DueOverride,
    QuestionItem,
    VersionStatus,
)
from app.modules.practice.models import PracticeAnswer, PracticeAttempt, PracticeAttemptStatus


def _bank_item_to_practice_question(item: QuestionItem) -> dict[str, Any]:
    body = item.body_json or {}
    key = item.key_json or {}
    base: dict[str, Any] = {
        "id": str(item.id),
        "type": item.type,
        "prompt": item.prompt,
        "explanation": item.explanation,
        "points": float(item.default_points),
        "topic": item.topic,
        "isReviewed": True,
        "isAiProposed": item.origin == "AI",
    }
    if item.source_ref_json:
        base["sourceRef"] = item.source_ref_json
    if item.type == "single_choice":
        base["options"] = body.get("options", [])
        base["correctOptionId"] = key.get("correct_option_id", "")
        if key.get("distractor_misconceptions"):
            base["distractorMisconceptions"] = key["distractor_misconceptions"]
    elif item.type == "multiple_choice":
        base["options"] = body.get("options", [])
        base["correctOptionIds"] = key.get("correct_option_ids", [])
    elif item.type == "true_false":
        truths = key.get("statement_truths") or {}
        base["statements"] = [
            {**stmt, "isCorrect": bool(truths.get(stmt["id"], False))}
            for stmt in body.get("statements", [])
        ]
    else:
        base["expectedAnswer"] = key.get("expected_answer", "")
        base["acceptedAnswers"] = key.get("accepted_answers", [])
        base["manualGradingRequired"] = bool(body.get("manual_grading_required", False))
    return base


def _sanitize_editor_question(q: dict[str, Any]) -> dict[str, Any]:
    qtype = normalize_question_type(str(q.get("type", "")))
    qid = str(q.get("id") or f"q-{uuid4().hex[:12]}")
    cleaned = dict(q)
    cleaned["id"] = qid
    cleaned["type"] = qtype
    cleaned["prompt"] = str(q.get("prompt") or "")
    cleaned["points"] = float(q.get("points") or 1.0)
    cleaned["isReviewed"] = bool(q.get("isReviewed", False))
    return cleaned


def _get_activity(
    session: Session, school_id: UUID, class_id: UUID, activity_id: UUID, kind: str | None = None
) -> Activity:
    stmt = select(Activity).where(
        Activity.id == activity_id,
        Activity.school_id == school_id,
        Activity.class_id == class_id,
    )
    if kind is not None:
        stmt = stmt.where(Activity.kind == kind)
    activity = session.scalar(stmt)
    if activity is None:
        raise AppError(404)
    return activity


def _get_version(session: Session, version_id: UUID, *, for_update: bool = False) -> ActivityVersion:
    stmt = select(ActivityVersion).where(ActivityVersion.id == version_id)
    if for_update:
        stmt = stmt.with_for_update()
    version = session.scalar(stmt)
    if version is None:
        raise AppError(404)
    return version


def _available_sources(grants: GrantSnapshot | None) -> list[dict[str, Any]]:
    if grants is None:
        return []
    return [
        {
            "id": str(g.document_id),
            "title": g.title or f"Tài liệu {str(g.document_id)[:8]}",
            "version": int(g.version_number or 1),
            "isReady": True,
            "documentVersionId": str(g.document_version_id),
            "sourceId": g.source_id,
        }
        for g in grants.grants
    ]


def _editor_snapshot(
    activity: Activity,
    version: ActivityVersion,
    *,
    class_name: str,
    grants: GrantSnapshot | None,
) -> dict[str, Any]:
    questions = list(version.content_snapshot_json or [])
    reviewed_count = sum(1 for q in questions if bool(q.get("isReviewed")))
    is_draft = version.status == VersionStatus.DRAFT
    can_publish = bool(is_draft and questions and reviewed_count == len(questions))
    return {
        "editor": {
            "practiceId": str(activity.id),
            "classId": str(activity.class_id),
            "className": class_name or str(activity.class_id),
            "title": activity.title,
            "topic": activity.topic,
            "versionLabel": f"Phiên bản {version.version_number} ({'Bản nháp' if is_draft else 'Đã công bố'})",
            "currentVersionNumber": version.version_number,
            "isDraft": is_draft,
            "questions": questions,
            "reviewedCount": reviewed_count,
            "totalQuestions": len(questions),
            "canPublish": can_publish,
            "availableClassSources": _available_sources(grants),
        },
        "revision": str(version.revision),
        "defaultPoints": 1,
        "questionTypes": ["single_choice", "multiple_choice", "true_false", "short_answer"],
    }


def list_class_activities(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    *,
    kind: str | None = None,
    pending_counts: dict[UUID, int] | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(Activity)
        .where(Activity.school_id == school_id, Activity.class_id == class_id)
        .order_by(desc(Activity.updated_at))
    )
    if kind:
        stmt = stmt.where(Activity.kind == kind)
    rows = list(session.scalars(stmt))
    items: list[dict[str, Any]] = []
    for act in rows:
        ver_id = act.draft_version_id or act.current_version_id
        ver = session.get(ActivityVersion, ver_id) if ver_id else None
        status = "PUBLISHED"
        if ver is not None:
            if ver.status == VersionStatus.PENDING_APPROVAL:
                status = "PENDING_APPROVAL"
            elif ver.status == VersionStatus.DRAFT and act.current_version_id is None:
                status = "DRAFT"
        due_label = ""
        if ver and isinstance(ver.settings_json, dict):
            due_label = str(ver.settings_json.get("due_at") or ver.settings_json.get("closes_at") or "")
        edit_href = (
            f"/teacher/classes/{class_id}/practice/{act.id}/edit"
            if act.kind == ActivityKind.PRACTICE
            else f"/teacher/classes/{class_id}/assessments/{act.id}/edit"
            if act.kind == ActivityKind.ASSESSMENT
            else f"/teacher/classes/{class_id}/assignments/{act.id}/edit"
        )
        pending_count = (
            int((pending_counts or {}).get(act.id, 0))
            if act.kind != ActivityKind.PRACTICE and act.current_version_id is not None
            else 0
        )
        items.append(
            {
                "id": str(act.id),
                "classId": str(act.class_id),
                "resourceId": str(act.id),
                "title": act.title or "Bản nháp chưa đặt tên",
                "type": act.kind,
                "topic": act.topic,
                "status": status,
                "dueOrTimeLabel": due_label or "Không giới hạn",
                "pendingGradingCount": pending_count,
                "canDeleteDraft": act.current_version_id is None and status == "DRAFT",
                "editHref": edit_href,
            }
        )
    return items


def delete_draft_activity(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    activity_id: UUID,
    has_attempts: bool = False,
    has_submissions: bool = False,
) -> None:
    with transaction(session):
        activity = session.scalar(
            select(Activity)
            .where(
                Activity.id == activity_id,
                Activity.class_id == class_id,
                Activity.school_id == school_id,
            )
            .with_for_update()
        )
        if activity is None:
            raise AppError(404)
        if activity.current_version_id is not None:
            raise AppError(409, "ACTIVITY_ALREADY_PUBLISHED")
        non_draft_version = session.scalar(
            select(ActivityVersion.id)
            .where(
                ActivityVersion.activity_id == activity_id,
                ActivityVersion.status != VersionStatus.DRAFT,
            )
            .limit(1)
        )
        if non_draft_version is not None:
            raise AppError(409, "ACTIVITY_ALREADY_PUBLISHED")
        has_practice_attempts = (
            session.scalar(
                select(PracticeAttempt.id).where(PracticeAttempt.activity_id == activity_id).limit(1)
            )
            is not None
        )
        if has_attempts or has_submissions or has_practice_attempts:
            raise AppError(409, "ACTIVITY_ALREADY_PUBLISHED")
        activity.draft_version_id = None
        session.flush()
        session.execute(delete(ActivityAudience).where(ActivityAudience.activity_id == activity_id))
        session.execute(delete(DueOverride).where(DueOverride.activity_id == activity_id))
        session.execute(delete(ActivityVersion).where(ActivityVersion.activity_id == activity_id))
        session.flush()
        session.delete(activity)


def list_question_bank(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    *,
    origin: str | None = None,
    qtype: str | None = None,
    topic: str | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(QuestionItem)
        .where(QuestionItem.school_id == school_id, QuestionItem.class_id == class_id)
        .order_by(desc(QuestionItem.created_at))
    )
    if origin:
        stmt = stmt.where(QuestionItem.origin == origin)
    if qtype:
        stmt = stmt.where(QuestionItem.type == normalize_question_type(qtype))
    if topic:
        stmt = stmt.where(QuestionItem.topic == topic)
    return [
        {
            "id": str(row.id),
            "type": row.type,
            "prompt": row.prompt,
            "topic": row.topic,
            "origin": row.origin,
            "ai_draft_id": str(row.ai_draft_id) if row.ai_draft_id else None,
            "reviewed_by": str(row.reviewed_by),
            "reviewed_at": ensure_utc(row.reviewed_at).isoformat(),
            "question": _bank_item_to_practice_question(row),
        }
        for row in session.scalars(stmt)
    ]


def create_practice(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    actor: CurrentUser,
    title: str,
    topic: str,
) -> dict[str, Any]:
    with transaction(session):
        activity = Activity(
            school_id=school_id,
            class_id=class_id,
            kind=ActivityKind.PRACTICE,
            title=title.strip(),
            topic=topic.strip(),
            created_by=actor.id,
        )
        session.add(activity)
        session.flush()
        version = ActivityVersion(
            activity_id=activity.id,
            version_number=1,
            status=VersionStatus.DRAFT,
            revision=1,
            content_snapshot_json=[],
            settings_json={},
            created_by=actor.id,
        )
        session.add(version)
        session.flush()
        activity.draft_version_id = version.id
    return {
        "practice_id": str(activity.id),
        "version_id": str(version.id),
        "revision": 1,
        "edit_href": f"/teacher/classes/{class_id}/practice/{activity.id}/edit",
    }


async def load_practice_editor(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    class_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
    ver_id = activity.draft_version_id or activity.current_version_id
    if ver_id is None:
        raise AppError(404)
    version = _get_version(session, ver_id)
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    return _editor_snapshot(activity, version, class_name=class_name, grants=grants)


async def save_practice_draft(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    expected_revision: int,
    title: str | None,
    topic: str | None,
    questions: list[dict[str, Any]],
    class_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.draft_version_id is None:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        version = _get_version(session, activity.draft_version_id, for_update=True)
        if version.status != VersionStatus.DRAFT:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        if version.revision != expected_revision:
            raise AppError(409, "REVISION_CONFLICT")
        if title is not None:
            activity.title = title.strip()
        if topic is not None:
            activity.topic = topic.strip()
        cleaned = [_sanitize_editor_question(q) for q in questions]
        previous = {q["id"]: q for q in version.content_snapshot_json}
        if len({q["id"] for q in cleaned}) != len(cleaned):
            raise AppError(422)
        for question in cleaned:
            old = previous.get(question["id"], {})
            question["isReviewed"] = old.get("isReviewed", False) and (
                {k: v for k, v in question.items() if k != "isReviewed"}
                == {k: v for k, v in old.items() if k != "isReviewed"}
            )
        version.content_snapshot_json = cleaned
        version.revision += 1
        version.updated_at = utcnow()
        activity.updated_at = utcnow()
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    snap = _editor_snapshot(activity, version, class_name=class_name, grants=grants)
    snap["saved_at"] = utcnow().isoformat()
    snap["reviewed_count"] = snap["editor"]["reviewedCount"]
    snap["can_publish"] = snap["editor"]["canPublish"]
    return snap


async def attach_bank_items(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    expected_revision: int,
    learning_item_ids: list[UUID],
    class_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.draft_version_id is None:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        version = _get_version(session, activity.draft_version_id, for_update=True)
        if version.status != VersionStatus.DRAFT:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        if version.revision != expected_revision:
            raise AppError(409, "REVISION_CONFLICT")
        items = list(
            session.scalars(
                select(QuestionItem).where(
                    QuestionItem.id.in_(learning_item_ids),
                    QuestionItem.school_id == school_id,
                    QuestionItem.class_id == class_id,
                )
            )
        )
        if len(items) != len(set(learning_item_ids)):
            raise AppError(404)
        by_id = {item.id: item for item in items}
        current = list(version.content_snapshot_json or [])
        existing_ids = {str(q.get("id")) for q in current}
        for item_id in learning_item_ids:
            if str(item_id) not in existing_ids:
                current.append(_bank_item_to_practice_question(by_id[item_id]))
                existing_ids.add(str(item_id))
        version.content_snapshot_json = current
        version.revision += 1
        version.updated_at = utcnow()
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    return _editor_snapshot(activity, version, class_name=class_name, grants=grants)


async def review_practice_question(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    question_id: str,
    expected_revision: int,
    approved: bool,
    class_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.draft_version_id is None:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        version = _get_version(session, activity.draft_version_id, for_update=True)
        if version.status != VersionStatus.DRAFT:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        if version.revision != expected_revision:
            raise AppError(409, "REVISION_CONFLICT")
        found = False
        updated_questions = []
        for q in list(version.content_snapshot_json or []):
            if str(q.get("id")) == question_id:
                found = True
                if approved and not str(q.get("prompt") or "").strip():
                    raise AppError(422)
                updated_questions.append({**q, "isReviewed": approved})
            else:
                updated_questions.append(q)
        if not found:
            raise AppError(404)
        version.content_snapshot_json = updated_questions
        version.revision += 1
        version.updated_at = utcnow()
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    snap = _editor_snapshot(activity, version, class_name=class_name, grants=grants)
    snap["reviewed_count"] = snap["editor"]["reviewedCount"]
    snap["can_publish"] = snap["editor"]["canPublish"]
    return snap


def publish_practice(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    expected_revision: int,
    actor: CurrentUser,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.draft_version_id is None:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        version = _get_version(session, activity.draft_version_id, for_update=True)
        if version.status != VersionStatus.DRAFT:
            raise AppError(409, "ASSESSMENT_ALREADY_PUBLISHED")
        if version.revision != expected_revision:
            raise AppError(409, "REVISION_CONFLICT")
        questions = list(version.content_snapshot_json or [])
        if not questions:
            raise AppError(422, "NO_QUESTIONS")
        # Rule D1: 100% of questions must be reviewed by teacher before publishing.
        if any(not bool(q.get("isReviewed")) for q in questions):
            raise AppError(422, "UNREVIEWED_QUESTIONS")
        now = utcnow()
        if activity.current_version_id is not None:
            prev = _get_version(session, activity.current_version_id, for_update=True)
            prev.status = VersionStatus.SUPERSEDED
        version.status = VersionStatus.PUBLISHED
        version.content_sha256 = canonical_content_sha256(questions, version.settings_json or {})
        version.published_by = actor.id
        version.published_at = now
        version.revision += 1
        activity.current_version_id = version.id
        activity.draft_version_id = None
        activity.updated_at = now
        record_outbox_event(
            session,
            event_type="activity.version.published",
            aggregate_type="activity",
            aggregate_id=activity.id,
            aggregate_version=version.version_number,
            payload_json={
                "school_id": str(school_id),
                "class_id": str(class_id),
                "activity_id": str(activity.id),
                "kind": ActivityKind.PRACTICE,
                "version_number": version.version_number,
                "published_at": now.isoformat(),
            },
        )
        record_learning_notification(
            session,
            event_type="PRACTICE_PUBLISHED",
            school_id=school_id,
            class_id=class_id,
            resource_type="practice",
            resource_id=activity.id,
        )
    return {
        "success": True,
        "practice_id": str(activity.id),
        "published_version_id": str(version.id),
        "version_number": version.version_number,
    }


def create_next_practice_version(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    actor: CurrentUser,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.draft_version_id is not None:
            existing = _get_version(session, activity.draft_version_id)
            return {
                "version_id": str(existing.id),
                "version_number": existing.version_number,
                "revision": existing.revision,
            }
        if activity.current_version_id is None:
            raise AppError(404)
        current = _get_version(session, activity.current_version_id)
        new_ver = ActivityVersion(
            activity_id=activity.id,
            version_number=current.version_number + 1,
            status=VersionStatus.DRAFT,
            revision=1,
            content_snapshot_json=list(current.content_snapshot_json or []),
            settings_json=dict(current.settings_json or {}),
            created_by=actor.id,
        )
        session.add(new_ver)
        session.flush()
        activity.draft_version_id = new_ver.id
    return {
        "version_id": str(new_ver.id),
        "version_number": new_ver.version_number,
        "revision": 1,
    }


def list_student_practices(
    session: Session, *, school_id: UUID, class_id: UUID, student_id: UUID
) -> dict[str, Any]:
    activities = list(
        session.scalars(
            select(Activity)
            .where(
                Activity.school_id == school_id,
                Activity.class_id == class_id,
                Activity.kind == ActivityKind.PRACTICE,
                Activity.current_version_id.is_not(None),
            )
            .order_by(desc(Activity.updated_at))
        )
    )
    attempts = list(
        session.scalars(
            select(PracticeAttempt)
            .where(
                PracticeAttempt.school_id == school_id,
                PracticeAttempt.class_id == class_id,
                PracticeAttempt.student_id == student_id,
            )
            .order_by(desc(PracticeAttempt.started_at))
        )
    )
    attempts_by_act: dict[UUID, list[PracticeAttempt]] = {}
    for att in attempts:
        attempts_by_act.setdefault(att.activity_id, []).append(att)

    items = []
    history_all = []
    for act in activities:
        ver = session.get(ActivityVersion, act.current_version_id)
        if ver is None or ver.status != VersionStatus.PUBLISHED:
            continue
        act_attempts = attempts_by_act.get(act.id, [])
        active = next((a for a in act_attempts if a.status == PracticeAttemptStatus.IN_PROGRESS), None)
        completed = [a for a in act_attempts if a.status == PracticeAttemptStatus.COMPLETED]
        latest_done = completed[0] if completed else None
        status = "IN_PROGRESS" if active else ("COMPLETED" if latest_done else "NOT_STARTED")
        act_history = []
        for done in completed:
            dur = 0
            if done.completed_at:
                dur = max(
                    1,
                    int((ensure_utc(done.completed_at) - ensure_utc(done.started_at)).total_seconds() // 60),
                )
            h_item = {
                "attemptId": str(done.id),
                "practiceId": str(act.id),
                "practiceTitle": act.title,
                "attemptNumber": done.attempt_number,
                "completedAt": ensure_utc(done.completed_at or done.last_saved_at).isoformat(),
                "score": {
                    "correct": done.correct_count,
                    "total": done.total_count,
                    "percentage": float(done.score_percentage or 0),
                },
                "durationMinutes": dur,
                "resultHref": f"/student/classes/{class_id}/practice/{act.id}/attempts/{done.id}/result",
            }
            act_history.append(h_item)
            history_all.append(h_item)

        active_ver = (
            session.get(ActivityVersion, active.activity_version_id) if active is not None else None
        )
        items.append(
            {
                "id": str(act.id),
                "classId": str(class_id),
                "title": act.title,
                "topic": act.topic,
                "questionCount": len(ver.content_snapshot_json or []),
                "status": status,
                "publishedVersionId": str(ver.id),
                "publishedVersionNumber": ver.version_number,
                "lastCompletedAt": (
                    ensure_utc(latest_done.completed_at).isoformat()
                    if latest_done and latest_done.completed_at
                    else None
                ),
                "lastScore": (
                    {
                        "correct": latest_done.correct_count,
                        "total": latest_done.total_count,
                        "percentage": float(latest_done.score_percentage or 0),
                    }
                    if latest_done
                    else None
                ),
                "activeAttemptId": str(active.id) if active else None,
                "latestResultAttemptId": str(latest_done.id) if latest_done else None,
                "currentQuestionIndex": active.current_question_index if active else 0,
                "totalAttempts": len(act_attempts),
                "history": act_history,
                "activeAttemptVersionNumber": active_ver.version_number if active_ver else None,
                "activeAttemptQuestionCount": (
                    len(active_ver.content_snapshot_json or []) if active_ver else None
                ),
            }
        )
    return {
        "classId": str(class_id),
        "activities": items,
        "completedHistoryCount": len(history_all),
        "history": history_all,
    }


def start_practice_attempt(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    student_id: UUID,
    intent: str,
) -> dict[str, Any]:
    with transaction(session):
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        if activity.current_version_id is None:
            raise AppError(404)
        version = _get_version(session, activity.current_version_id)
        if version.status != VersionStatus.PUBLISHED:
            raise AppError(404)
        existing = session.scalar(
            select(PracticeAttempt)
            .where(
                PracticeAttempt.activity_id == practice_id,
                PracticeAttempt.student_id == student_id,
                PracticeAttempt.status == PracticeAttemptStatus.IN_PROGRESS,
            )
            .order_by(desc(PracticeAttempt.attempt_number))
        )
        if existing is not None and intent in {"start", "resume"}:
            pinned_ver = _get_version(session, existing.activity_version_id)
            return {
                "attempt_id": str(existing.id),
                "published_version_id": str(pinned_ver.id),
                "published_version_number": pinned_ver.version_number,
                "href": f"/student/classes/{class_id}/practice/{practice_id}/attempts/{existing.id}",
            }
        last_no = (
            session.scalar(
                select(PracticeAttempt.attempt_number)
                .where(
                    PracticeAttempt.activity_id == practice_id,
                    PracticeAttempt.student_id == student_id,
                )
                .order_by(desc(PracticeAttempt.attempt_number))
                .limit(1)
            )
            or 0
        )
        now = utcnow()
        attempt = PracticeAttempt(
            school_id=school_id,
            class_id=class_id,
            activity_id=practice_id,
            activity_version_id=version.id,
            student_id=student_id,
            attempt_number=last_no + 1,
            status=PracticeAttemptStatus.IN_PROGRESS,
            current_question_index=0,
            started_at=now,
            last_saved_at=now,
            total_count=len(version.content_snapshot_json or []),
        )
        session.add(attempt)
        session.flush()
    return {
        "attempt_id": str(attempt.id),
        "published_version_id": str(version.id),
        "published_version_number": version.version_number,
        "href": f"/student/classes/{class_id}/practice/{practice_id}/attempts/{attempt.id}",
    }


async def get_practice_attempt_detail(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
    class_name: str,
    primary_teacher_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    attempt = session.scalar(
        select(PracticeAttempt).where(
            PracticeAttempt.id == attempt_id,
            PracticeAttempt.activity_id == practice_id,
            PracticeAttempt.class_id == class_id,
            PracticeAttempt.school_id == school_id,
        )
    )
    if attempt is None or attempt.student_id != student_id:
        raise AppError(404)
    activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
    version = _get_version(session, attempt.activity_version_id)
    answers = list(
        session.scalars(select(PracticeAnswer).where(PracticeAnswer.attempt_id == attempt.id))
    )
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    public_questions = []
    for raw_q in version.content_snapshot_json or []:
        pub = strip_answer_keys(raw_q)
        if pub.get("sourceRef"):
            pub["sourceRef"] = mask_current_source(pub["sourceRef"], grants)
        public_questions.append(pub)

    saved_answers = {ans.question_id: ans.answer_json for ans in answers}
    checked_feedbacks: dict[str, Any] = {}
    for ans in answers:
        if ans.is_checked and ans.feedback_json:
            fb = dict(ans.feedback_json)
            if fb.get("sourceRef"):
                fb["sourceRef"] = mask_current_source(fb["sourceRef"], grants)
            checked_feedbacks[ans.question_id] = fb

    return {
        "attemptId": str(attempt.id),
        "practiceId": str(practice_id),
        "classId": str(class_id),
        "ownerUserId": str(attempt.student_id),
        "publishedVersionId": str(version.id),
        "publishedVersionNumber": version.version_number,
        "className": class_name or str(class_id),
        "title": activity.title,
        "topic": activity.topic,
        "primaryTeacherName": primary_teacher_name or "Giáo viên phụ trách",
        "questions": public_questions,
        "savedAnswers": saved_answers,
        "checkedFeedbacks": checked_feedbacks,
        "lastSavedAt": ensure_utc(attempt.last_saved_at).isoformat(),
        "startedAt": ensure_utc(attempt.started_at).isoformat(),
        "currentQuestionIndex": attempt.current_question_index,
        "isCompleted": attempt.status == PracticeAttemptStatus.COMPLETED,
        "completedAt": (
            ensure_utc(attempt.completed_at).isoformat() if attempt.completed_at else None
        ),
    }


def save_practice_attempt_draft(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
    answers: dict[str, dict[str, Any]],
    current_question_index: int | None,
) -> dict[str, Any]:
    with transaction(session):
        attempt = session.scalar(
            select(PracticeAttempt)
            .where(
                PracticeAttempt.id == attempt_id,
                PracticeAttempt.activity_id == practice_id,
                PracticeAttempt.class_id == class_id,
                PracticeAttempt.school_id == school_id,
            )
            .with_for_update()
        )
        if attempt is None or attempt.student_id != student_id:
            raise AppError(404)
        if attempt.status != PracticeAttemptStatus.IN_PROGRESS:
            raise AppError(409)
        version = _get_version(session, attempt.activity_version_id)
        valid_qids = {str(q["id"]) for q in (version.content_snapshot_json or [])}
        now = utcnow()
        for qid, ans_payload in answers.items():
            if qid not in valid_qids:
                raise AppError(422)
            row = session.scalar(
                select(PracticeAnswer).where(
                    PracticeAnswer.attempt_id == attempt.id,
                    PracticeAnswer.question_id == qid,
                )
            )
            if row is None:
                session.add(
                    PracticeAnswer(
                        attempt_id=attempt.id,
                        question_id=qid,
                        answer_json=ans_payload,
                        updated_at=now,
                    )
                )
            else:
                row.answer_json = ans_payload
                row.updated_at = now
        if current_question_index is not None:
            attempt.current_question_index = current_question_index
        attempt.last_saved_at = now
    return {"saved_at": now.isoformat()}


async def check_practice_answer(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
    question_id: str,
    answer: dict[str, Any],
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    with transaction(session):
        attempt = session.scalar(
            select(PracticeAttempt)
            .where(
                PracticeAttempt.id == attempt_id,
                PracticeAttempt.activity_id == practice_id,
                PracticeAttempt.class_id == class_id,
                PracticeAttempt.school_id == school_id,
            )
            .with_for_update()
        )
        if attempt is None or attempt.student_id != student_id:
            raise AppError(404)
        if attempt.status != PracticeAttemptStatus.IN_PROGRESS:
            raise AppError(409)
        version = _get_version(session, attempt.activity_version_id)
        question = next(
            (q for q in (version.content_snapshot_json or []) if str(q["id"]) == question_id),
            None,
        )
        if question is None:
            raise AppError(404)
        outcome = grade_question(
            question,
            answer,
            scoring_rules=(version.settings_json or {}).get("scoring_rules"),
            official=False,
        )
        feedback: dict[str, Any] = {
            "questionId": question_id,
            "isCorrect": outcome.is_correct,
            "feedbackTitle": "Chính xác!" if outcome.is_correct else "Chưa chính xác",
            "explanation": str(question.get("explanation") or "")
            or (
                f"Đáp án đúng: {outcome.correct_answer_text}"
                if outcome.correct_answer_text
                else "Hãy đối chiếu lại nội dung bài học."
            ),
            "sourceRef": question.get("sourceRef"),
            "sourceReference": question.get("sourceReference"),
        }
        now = utcnow()
        row = session.scalar(
            select(PracticeAnswer).where(
                PracticeAnswer.attempt_id == attempt.id,
                PracticeAnswer.question_id == question_id,
            )
        )
        if row is None:
            session.add(
                PracticeAnswer(
                    attempt_id=attempt.id,
                    question_id=question_id,
                    answer_json=answer,
                    is_checked=True,
                    is_correct=outcome.is_correct,
                    feedback_json=feedback,
                    updated_at=now,
                )
            )
        else:
            row.answer_json = answer
            row.is_checked = True
            row.is_correct = outcome.is_correct
            row.feedback_json = feedback
            row.updated_at = now
        attempt.last_saved_at = now

    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    if feedback.get("sourceRef"):
        feedback["sourceRef"] = mask_current_source(feedback["sourceRef"], grants)
    return feedback


async def explain_practice_mistake(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
    question_id: str,
    grade_level: int | None,
    ai: AiPort,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    attempt = session.scalar(
        select(PracticeAttempt).where(
            PracticeAttempt.id == attempt_id,
            PracticeAttempt.activity_id == practice_id,
            PracticeAttempt.class_id == class_id,
            PracticeAttempt.school_id == school_id,
        )
    )
    if attempt is None or attempt.student_id != student_id:
        raise AppError(404)
    version = _get_version(session, attempt.activity_version_id)
    question = next(
        (q for q in (version.content_snapshot_json or []) if str(q["id"]) == question_id),
        None,
    )
    if question is None:
        raise AppError(404)
    ans = session.scalar(
        select(PracticeAnswer).where(
            PracticeAnswer.attempt_id == attempt.id,
            PracticeAnswer.question_id == question_id,
        )
    )
    if ans is None or not ans.is_checked or ans.is_correct:
        raise AppError(409)
    outcome = grade_question(question, ans.answer_json, official=False)
    misconception = ""
    if question.get("type") == "single_choice":
        chosen_id = str((ans.answer_json or {}).get("singleChoiceValue") or "")
        misconception = str((question.get("distractorMisconceptions") or {}).get(chosen_id) or "")
    pinned_evidence = []
    explanation = str(question.get("explanation") or "")
    fallback = {"explanation": explanation, "citations": [], "fallback": True}
    if question.get("sourceRef") and isinstance(question["sourceRef"], dict):
        grants = await _optional_grants(document, class_id, school_id, correlation_id)
        source = mask_current_source(question["sourceRef"], grants)
        if not source or not source["accessible"]:
            return fallback
        pinned_evidence = [question["sourceRef"]]
    try:
        result = await ai.explain_mistake(
            school_id=school_id,
            class_id=class_id,
            grade_level=grade_level,
            question=str(question.get("prompt") or ""),
            student_choice=outcome.student_answer_text,
            correct_answer=outcome.correct_answer_text,
            misconception=misconception,
            approved_explanation=explanation,
            pinned_evidence=pinned_evidence,
            correlation_id=correlation_id,
        )
    except AppError as exc:
        if exc.status_code == 503:
            return fallback
        raise
    return {
        "explanation": result.explanation or explanation,
        "citations": result.evidence,
        "fallback": result.fallback,
    }


def finish_practice_attempt(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
) -> dict[str, Any]:
    with transaction(session):
        attempt = session.scalar(
            select(PracticeAttempt)
            .where(
                PracticeAttempt.id == attempt_id,
                PracticeAttempt.activity_id == practice_id,
                PracticeAttempt.class_id == class_id,
                PracticeAttempt.school_id == school_id,
            )
            .with_for_update()
        )
        if attempt is None or attempt.student_id != student_id:
            raise AppError(404)
        activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
        version = _get_version(session, attempt.activity_version_id)
        if attempt.status == PracticeAttemptStatus.COMPLETED:
            return {
                "attempt_id": str(attempt.id),
                "result_href": f"/student/classes/{class_id}/practice/{practice_id}/attempts/{attempt.id}/result",
                "completed_at": ensure_utc(attempt.completed_at or attempt.last_saved_at).isoformat(),
                "score": {
                    "correct": attempt.correct_count,
                    "total": attempt.total_count,
                    "percentage": float(attempt.score_percentage or 0),
                },
            }
        questions = list(version.content_snapshot_json or [])
        answers_map = {
            row.question_id: row
            for row in session.scalars(
                select(PracticeAnswer).where(PracticeAnswer.attempt_id == attempt.id)
            )
        }
        correct_count = 0
        for q in questions:
            qid = str(q["id"])
            row = answers_map.get(qid)
            if row is None:
                continue
            outcome = grade_question(
                q,
                row.answer_json,
                scoring_rules=(version.settings_json or {}).get("scoring_rules"),
                official=False,
            )
            row.is_checked = True
            row.is_correct = outcome.is_correct
            if outcome.is_correct:
                correct_count += 1
        total = len(questions)
        pct = round((correct_count / total) * 100.0, 2) if total > 0 else 0.0
        now = utcnow()
        attempt.status = PracticeAttemptStatus.COMPLETED
        attempt.completed_at = now
        attempt.last_saved_at = now
        attempt.correct_count = correct_count
        attempt.total_count = total
        attempt.score_percentage = pct
        # Rule D8: Practice emits its own evidence event and NEVER writes to learning.grades.
        record_outbox_event(
            session,
            event_type="practice.attempt.completed",
            aggregate_type="practice_attempt",
            aggregate_id=attempt.id,
            aggregate_version=1,
            payload_json={
                "school_id": str(school_id),
                "class_id": str(class_id),
                "student_id": str(student_id),
                "activity_id": str(activity.id),
                "activity_version_id": str(version.id),
                "attempt_id": str(attempt.id),
                "topic": activity.topic,
                "correct_count": correct_count,
                "total_count": total,
                "score_percentage": pct,
                "completed_at": now.isoformat(),
            },
        )
    return {
        "attempt_id": str(attempt.id),
        "result_href": f"/student/classes/{class_id}/practice/{practice_id}/attempts/{attempt.id}/result",
        "completed_at": now.isoformat(),
        "score": {"correct": correct_count, "total": total, "percentage": pct},
    }


async def get_practice_result_detail(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    student_id: UUID,
    class_name: str,
    document: DocumentPort,
    correlation_id: str,
) -> dict[str, Any]:
    attempt = session.scalar(
        select(PracticeAttempt).where(
            PracticeAttempt.id == attempt_id,
            PracticeAttempt.activity_id == practice_id,
            PracticeAttempt.class_id == class_id,
            PracticeAttempt.school_id == school_id,
        )
    )
    if attempt is None or attempt.student_id != student_id:
        raise AppError(404)
    if attempt.status != PracticeAttemptStatus.COMPLETED:
        raise AppError(409)
    activity = _get_activity(session, school_id, class_id, practice_id, ActivityKind.PRACTICE)
    version = _get_version(session, attempt.activity_version_id)
    answers_map = {
        row.question_id: row
        for row in session.scalars(
            select(PracticeAnswer).where(PracticeAnswer.attempt_id == attempt.id)
        )
    }
    grants = await _optional_grants(document, class_id, school_id, correlation_id)
    result_items = []
    correct_cnt = incorrect_cnt = unanswered_cnt = 0
    for idx, q in enumerate(version.content_snapshot_json or [], 1):
        qid = str(q["id"])
        row = answers_map.get(qid)
        outcome = grade_question(
            q,
            row.answer_json if row else None,
            scoring_rules=(version.settings_json or {}).get("scoring_rules"),
            official=False,
        )
        if row is None or not outcome.student_answer_text:
            unanswered_cnt += 1
        elif outcome.is_correct:
            correct_cnt += 1
        else:
            incorrect_cnt += 1
        sref = mask_current_source(q.get("sourceRef"), grants)
        result_items.append(
            {
                "id": qid,
                "questionNumber": idx,
                "prompt": str(q.get("prompt") or ""),
                "type": normalize_question_type(str(q["type"])),
                "studentAnswerText": outcome.student_answer_text,
                "correctAnswerText": outcome.correct_answer_text,
                "isCorrect": outcome.is_correct,
                "explanation": str(q.get("explanation") or ""),
                "sourceReference": q.get("sourceReference"),
                "sourceRef": sref,
            }
        )
    completed_at = ensure_utc(attempt.completed_at or attempt.last_saved_at)
    started_at = ensure_utc(attempt.started_at)
    duration_min = max(1, int((completed_at - started_at).total_seconds() // 60))
    total = len(result_items)
    pct = round((correct_cnt / total) * 100.0, 2) if total > 0 else 0.0
    return {
        "attemptId": str(attempt.id),
        "practiceId": str(practice_id),
        "classId": str(class_id),
        "ownerUserId": str(attempt.student_id),
        "publishedVersionId": str(version.id),
        "publishedVersionNumber": version.version_number,
        "className": class_name or str(class_id),
        "title": activity.title,
        "attemptNumber": attempt.attempt_number,
        "completedAt": completed_at.astimezone(UTC).isoformat(),
        "startedAt": started_at.astimezone(UTC).isoformat(),
        "durationMinutes": duration_min,
        "score": {"correct": correct_cnt, "total": total, "percentage": pct},
        "correctCount": correct_cnt,
        "incorrectCount": incorrect_cnt,
        "unansweredCount": unanswered_cnt,
        "questions": result_items,
    }
