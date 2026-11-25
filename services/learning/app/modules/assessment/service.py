from datetime import datetime, timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.db import ensure_utc, utcnow, write_transaction
from app.common.errors import AppError, AttemptClosedError
from app.integrations.core_client import CurrentUser
from app.integrations.document_client import GrantSnapshot
from app.modules.appeals import service as appeals
from app.modules.assessment.models import AssessmentAttempt, AssessmentAttemptStatus, AttemptAnswer
from app.modules.assessment.schemas import (
    AssessmentReceipt,
    AssessmentSettings,
    SaveAnswersRequest,
    SaveDraftRequest,
)
from app.modules.bank import activities
from app.modules.bank.grading_engine import (
    canonical_content_sha256,
    grade_question,
    mask_current_source,
    strip_answer_keys,
)
from app.modules.bank.models import Activity, ActivityKind, ActivityVersion, VersionStatus
from app.modules.grading import service as grading

KIND = ActivityKind.ASSESSMENT
TARGET = "ASSESSMENT_ATTEMPT"
IN_PROGRESS = AssessmentAttemptStatus.IN_PROGRESS


def create(session: Session, school_id: UUID, class_id: UUID, actor: CurrentUser) -> dict:
    with write_transaction(session):
        activity, version = activities.create_activity(session, school_id, class_id, KIND, actor.id)
        return {
            "assessment_id": activity.id,
            "version_id": version.id,
            "revision": version.revision,
        }


def _attempt_count(session: Session, activity_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AssessmentAttempt)
            .where(AssessmentAttempt.activity_id == activity_id)
        )
        or 0
    )


def activity_has_attempts(session: Session, activity_id: UUID) -> bool:
    return _attempt_count(session, activity_id) > 0


def draft_view(
    activity: Activity, version: ActivityVersion, *, session: Session | None = None
) -> dict:
    attempt_count = _attempt_count(session, activity.id) if session is not None else 0
    scoring_adjustments = (
        grading.list_version_adjustments(session, version.id)
        if session is not None and version.status != VersionStatus.DRAFT
        else []
    )
    return {
        "class_id": activity.class_id,
        "assessment_id": activity.id,
        "version_id": version.id,
        "revision": version.revision,
        "version_number": version.version_number,
        "settings": version.settings_json,
        "questions": version.content_snapshot_json,
        "status": version.status,
        "rejection_reason": version.rejection_reason,
        "attempt_count": attempt_count,
        "can_create_version": bool(
            version.status == VersionStatus.PUBLISHED
            and activity.draft_version_id is None
            and attempt_count == 0
        ),
        "scoring_adjustments": scoring_adjustments,
    }


def get_draft(session: Session, scope: tuple) -> dict:
    act = activities.get_activity(session, *scope, KIND)
    ver = activities.get_version(session, act, act.draft_version_id or act.current_version_id)
    return draft_view(act, ver, session=session)


def _check_revision(version: ActivityVersion, expected: int, status: VersionStatus) -> None:
    if version.status != status or version.revision != expected:
        raise AppError(409, "REVISION_CONFLICT")


def save_draft(session: Session, scope: tuple, body: SaveDraftRequest) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.draft_version_id or act.current_version_id)
        _check_revision(ver, body.expected_revision, VersionStatus.DRAFT)
        previous = {q["id"]: q for q in ver.content_snapshot_json}
        questions = []
        for question in body.questions:
            row = question.model_dump(mode="json")
            old = previous.get(question.id, {})
            row["reviewed"] = bool(old.get("reviewed")) and all(
                value == old.get(key) for key, value in row.items() if key != "reviewed"
            )
            questions.append(row)
        ver.content_snapshot_json = questions
        ver.settings_json = body.settings.model_dump(mode="json")
        if act.current_version_id is None:
            act.title, act.topic, act.code = (
                body.settings.title,
                body.settings.topic,
                body.settings.code,
            )
        ver.revision += 1
        return draft_view(act, ver, session=session)


def review(session: Session, scope: tuple, expected: int, question_id: str) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.draft_version_id or act.current_version_id)
        _check_revision(ver, expected, VersionStatus.DRAFT)
        question = next((q for q in ver.content_snapshot_json if q["id"] == question_id), None)
        if question is None:
            raise AppError(404)
        validate_question_key(question)
        ver.content_snapshot_json = [
            {**q, "reviewed": True} if q["id"] == question_id else q
            for q in ver.content_snapshot_json
        ]
        ver.revision += 1
        return draft_view(act, ver, session=session)


def validate_question_key(question: dict) -> None:
    qtype, key = question["type"], question.get("key")
    option_ids = {option["id"] for option in question.get("options", [])}
    if qtype == "single_choice":
        if isinstance(key, list):
            key = key[0] if len(key) == 1 else None
        valid = len(option_ids) >= 2 and isinstance(key, str) and key in option_ids
    elif qtype == "multiple_choice":
        valid = (
            len(option_ids) >= 2
            and isinstance(key, list)
            and len(key) >= 2
            and all(isinstance(value, str) for value in key)
            and len(set(key)) == len(key)
            and set(key) <= option_ids
        )
    elif qtype in {"true_false", "true_false_4"}:
        statements = {stmt["id"] for stmt in question["statements"]}
        valid = (
            bool(statements)
            and isinstance(key, dict)
            and key.keys() == statements
            and all(isinstance(value, bool) for value in key.values())
        )
    else:
        valid = bool(question.get("manual_grading_required")) or (
            isinstance(key, str) and bool(key)
        )
    if not valid:
        raise AppError(422)


def propose(session: Session, scope: tuple, actor: CurrentUser, expected: int) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.draft_version_id or act.current_version_id)
        _check_revision(ver, expected, VersionStatus.DRAFT)
        if not ver.content_snapshot_json:
            raise AppError(422, "NO_QUESTIONS")
        if not all(q.get("reviewed") for q in ver.content_snapshot_json):
            raise AppError(422, "UNREVIEWED_QUESTIONS")
        try:
            AssessmentSettings.model_validate(ver.settings_json or {})
        except ValidationError as exc:
            raise AppError(422, "INCOMPLETE_ASSESSMENT_SETTINGS") from exc
        ver.status = VersionStatus.PENDING_APPROVAL
        ver.proposed_by, ver.proposed_at = actor.id, utcnow()
        ver.rejection_reason = None
        ver.revision += 1
        return draft_view(act, ver, session=session)


def decide(session: Session, scope: tuple, actor: CurrentUser, body) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.draft_version_id or act.current_version_id)
        _check_revision(ver, body.expected_revision, VersionStatus.PENDING_APPROVAL)
        ver.revision += 1
        if body.decision == "REJECT":
            ver.status = VersionStatus.DRAFT
            ver.rejection_reason = body.reason
            return draft_view(act, ver, session=session)

        previous = (
            activities.get_version(session, act, act.current_version_id)
            if act.current_version_id
            else None
        )
        if previous:
            previous.status = VersionStatus.SUPERSEDED
        settings = ver.settings_json
        now = utcnow()
        ver.content_sha256 = canonical_content_sha256(ver.content_snapshot_json, settings)
        ver.status = VersionStatus.PUBLISHED
        ver.approved_by = ver.published_by = actor.id
        ver.approved_at = ver.published_at = now
        ver.deadline_version = previous.deadline_version + 1 if previous else 1
        act.current_version_id, act.draft_version_id = ver.id, None
        act.title, act.topic, act.code = (
            settings["title"],
            settings.get("topic", ""),
            settings.get("code"),
        )
        activities.record_publication(session, act, ver)
        activities.record_deadline(
            session,
            act,
            ver,
            act.title,
            parse_time(settings["opens_at"]),
            parse_time(settings["closes_at"]),
        )
        return draft_view(act, ver, session=session)


def next_version(session: Session, scope: tuple, actor: CurrentUser) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        if act.current_version_id is None or act.draft_version_id is not None:
            raise AppError(409)
        if _attempt_count(session, act.id) > 0:
            raise AppError(409, "ASSESSMENT_HAS_ATTEMPTS")
        current = activities.get_version(session, act, act.current_version_id)
        ver = ActivityVersion(
            activity_id=act.id,
            version_number=current.version_number + 1,
            settings_json=current.settings_json,
            content_snapshot_json=current.content_snapshot_json,
            created_by=actor.id,
        )
        session.add(ver)
        session.flush()
        act.draft_version_id = ver.id
        return draft_view(act, ver, session=session)


def parse_time(value: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(value))


def _student_attempts(session: Session, act: Activity, student_id: UUID):
    return list(
        session.scalars(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.activity_id == act.id,
                AssessmentAttempt.student_id == student_id,
                AssessmentAttempt.school_id == act.school_id,
                AssessmentAttempt.class_id == act.class_id,
            )
            .order_by(AssessmentAttempt.attempt_number)
        )
    )


def _summary(session: Session, act: Activity, student_id: UUID, now: datetime) -> dict:
    ver = activities.get_version(session, act, act.current_version_id)
    settings = ver.settings_json
    attempts = _student_attempts(session, act, student_id)
    active = next(
        (a for a in attempts if a.status == IN_PROGRESS and ensure_utc(a.ends_at) > now), None
    )
    completed = next((a for a in reversed(attempts) if a.receipt_json), None)
    status = (
        "UPCOMING"
        if now < parse_time(settings["opens_at"])
        else ("CLOSED" if now >= parse_time(settings["closes_at"]) else "OPEN")
    )
    return {
        **settings,
        "id": act.id,
        "version_id": ver.id,
        "version_number": ver.version_number,
        "question_count": len(ver.content_snapshot_json),
        "total_points": round(sum(q["points"] for q in ver.content_snapshot_json), 2),
        "status": status,
        "used_attempts": len(attempts),
        "active_attempt_id": active.id if active else None,
        "completed_attempt_id": completed.id if completed else None,
        "can_start": status == "OPEN"
        and active is None
        and len(attempts) < settings["max_attempts"],
    }


def list_published(session: Session, school_id: UUID, class_id: UUID, student_id: UUID) -> dict:
    with write_transaction(session):
        lazy_close_expired_attempts(session, student_id=student_id, school_id=school_id)
        now = utcnow()
        rows = session.scalars(
            select(Activity)
            .where(
                Activity.school_id == school_id,
                Activity.class_id == class_id,
                Activity.kind == KIND,
                Activity.current_version_id.is_not(None),
            )
            .order_by(Activity.created_at, Activity.id)
        )
        return {
            "items": [_summary(session, act, student_id, now) for act in rows],
            "server_now": now,
        }


def published_detail(session: Session, scope: tuple, student_id: UUID) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND)
        lazy_close_expired_attempts(session, student_id=student_id, school_id=act.school_id)
        now = utcnow()
        return {"assessment": _summary(session, act, student_id, now), "server_now": now}


def start(session: Session, scope: tuple, actor: CurrentUser) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, KIND, lock=True)
        ver = activities.get_version(session, act, act.current_version_id)
        settings = ver.settings_json
        closes_at = parse_time(settings["closes_at"])
        attempts = list(
            session.scalars(
                select(AssessmentAttempt)
                .where(
                    AssessmentAttempt.activity_id == act.id,
                    AssessmentAttempt.student_id == actor.id,
                )
                .order_by(AssessmentAttempt.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        now = utcnow()
        for attempt in attempts:
            close_when_expired(session, attempt, now)
        if any(a.status == IN_PROGRESS for a in attempts):
            failure = AppError(409)
        elif not parse_time(settings["opens_at"]) <= now < closes_at:
            failure = AppError(409, "ASSESSMENT_NOT_OPEN")
        elif len(attempts) >= settings["max_attempts"]:
            failure = AppError(409, "ATTEMPT_LIMIT_REACHED")
        else:
            attempt = AssessmentAttempt(
                school_id=act.school_id,
                class_id=act.class_id,
                activity_id=act.id,
                activity_version_id=ver.id,
                student_id=actor.id,
                student_name=actor.display_name or "",
                attempt_number=len(attempts) + 1,
                started_at=now,
                last_saved_at=now,
                ends_at=min(now + timedelta(minutes=settings["duration_minutes"]), closes_at),
            )
            session.add(attempt)
            session.flush()
            return {
                "attempt_id": attempt.id,
                "version_id": ver.id,
                "ends_at": attempt.ends_at,
                "server_now": now,
            }
    raise failure


def find_attempt(
    session: Session,
    school_id: UUID,
    attempt_id: UUID,
    *,
    class_id: UUID | None = None,
    activity_id: UUID | None = None,
    student_id: UUID | None = None,
    lock: bool = False,
) -> AssessmentAttempt:
    query = select(AssessmentAttempt).where(
        AssessmentAttempt.id == attempt_id,
        AssessmentAttempt.school_id == school_id,
    )
    if class_id is not None:
        query = query.where(AssessmentAttempt.class_id == class_id)
    if activity_id is not None:
        query = query.where(AssessmentAttempt.activity_id == activity_id)
    if student_id is not None:
        query = query.where(AssessmentAttempt.student_id == student_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    attempt = session.scalar(query)
    if attempt is None:
        raise AppError(404)
    return attempt


def attempt_version(session: Session, attempt: AssessmentAttempt) -> ActivityVersion:
    act = activities.get_activity(
        session, attempt.school_id, attempt.class_id, attempt.activity_id, KIND
    )
    return activities.get_version(session, act, attempt.activity_version_id)


def submitted_attempts(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    *,
    activity_id: UUID | None = None,
    version_id: UUID | None = None,
    lock: bool = False,
) -> list[AssessmentAttempt]:
    query = select(AssessmentAttempt).where(
        AssessmentAttempt.school_id == school_id,
        AssessmentAttempt.class_id == class_id,
        AssessmentAttempt.status != IN_PROGRESS,
    )
    if activity_id is not None:
        query = query.where(AssessmentAttempt.activity_id == activity_id)
    if version_id is not None:
        query = query.where(AssessmentAttempt.activity_version_id == version_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return list(session.scalars(query.order_by(AssessmentAttempt.id)))


def lock_activity_attempts(session: Session, act: Activity, *, version_id: UUID | None = None):
    query = select(AssessmentAttempt).where(
        AssessmentAttempt.activity_id == act.id,
        AssessmentAttempt.school_id == act.school_id,
        AssessmentAttempt.class_id == act.class_id,
    )
    if version_id is not None:
        query = query.where(AssessmentAttempt.activity_version_id == version_id)
    return list(
        session.scalars(
            query.order_by(AssessmentAttempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def attempts_for_scoring(session: Session, activity_id: UUID, student_id: UUID):
    return session.scalars(
        select(AssessmentAttempt)
        .where(
            AssessmentAttempt.activity_id == activity_id,
            AssessmentAttempt.student_id == student_id,
        )
        .order_by(AssessmentAttempt.attempt_number)
    )


def saved_answers(session: Session, attempt_id: UUID) -> dict[str, AttemptAnswer]:
    return {
        row.question_id: row
        for row in session.scalars(
            select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt_id)
        )
    }


def score_question(question: dict, answer: dict | None, settings: dict):
    return grade_question(
        {
            **question,
            "manualGradingRequired": question.get(
                "manual_grading_required", question.get("manualGradingRequired", False)
            ),
            "acceptedAnswers": question.get(
                "accepted_answers", question.get("acceptedAnswers", [])
            ),
        },
        answer,
        scoring_rules=settings.get("scoring_rules"),
        official=True,
    )


def _close(
    session: Session, attempt: AssessmentAttempt, now: datetime, *, operation_id: UUID | None = None
) -> None:
    version = attempt_version(session, attempt)
    answers = saved_answers(session, attempt.id)
    questions = version.content_snapshot_json
    scores = {}
    for question in questions:
        qid = question["id"]
        row = answers.get(qid)
        scores[qid] = score_question(
            question, row.answer_json if row else None, version.settings_json
        ).score
    scores, maximum = grading.apply_scoring_adjustments(session, version, answers, scores)
    manual = operation_id is not None
    attempt.status = (
        AssessmentAttemptStatus.SUBMITTED if manual else AssessmentAttemptStatus.AUTO_SUBMITTED
    )
    attempt.submitted_at = now if manual else min(now, ensure_utc(attempt.ends_at))
    attempt.submit_operation_id = operation_id
    attempt.submit_reason = "MANUAL" if manual else "TIMEOUT"
    attempt.receipt_json = AssessmentReceipt(
        id=attempt.id,
        class_id=attempt.class_id,
        assessment_id=attempt.activity_id,
        attempt_id=attempt.id,
        version_id=version.id,
        version_number=version.version_number,
        reason=attempt.submit_reason,
        submitted_at=attempt.submitted_at,
        saved_answer_count=len(answers),
        total_question_count=len(questions),
        saved_revision=attempt.saved_revision,
    ).model_dump(mode="json")
    grading.record_initial_grade(
        session,
        school_id=attempt.school_id,
        class_id=attempt.class_id,
        activity_id=attempt.activity_id,
        student_id=attempt.student_id,
        target_type=TARGET,
        target_id=attempt.id,
        scores=scores,
        total_score=round(sum(score or 0 for score in scores.values()), 2),
        max_score=maximum,
        actor_id=attempt.student_id,
    )


def close_when_expired(
    session: Session,
    attempt: AssessmentAttempt,
    now: datetime,
    *,
    transferred: bool = False,
) -> None:
    if attempt.status == IN_PROGRESS and (transferred or ensure_utc(attempt.ends_at) <= now):
        _close(session, attempt, now)


def lazy_close_expired_attempts(
    session: Session,
    *,
    student_id: UUID | None = None,
    school_id: UUID | None = None,
) -> int:
    return _expire(session, student_id=student_id, school_id=school_id)


def sweep_expired_attempts(session: Session) -> int:
    return _expire(session, skip_locked=True)


def _expire(session: Session, *, student_id=None, school_id=None, skip_locked: bool = False) -> int:
    now = utcnow()
    query = select(AssessmentAttempt).where(
        AssessmentAttempt.status == IN_PROGRESS,
        AssessmentAttempt.ends_at <= now,
    )
    if student_id is not None:
        query = query.where(AssessmentAttempt.student_id == student_id)
    if school_id is not None:
        query = query.where(AssessmentAttempt.school_id == school_id)
    query = (
        query.order_by(AssessmentAttempt.id)
        .with_for_update(
            skip_locked=skip_locked,
        )
        .execution_options(populate_existing=True)
    )
    expired = list(session.scalars(query))
    for attempt in expired:
        _close(session, attempt, now)
    return len(expired)


def active_lock(session: Session, student_id: UUID, school_id: UUID) -> bool:
    with write_transaction(session):
        lazy_close_expired_attempts(session, student_id=student_id, school_id=school_id)
        return (
            session.scalar(
                select(AssessmentAttempt.id)
                .where(
                    AssessmentAttempt.school_id == school_id,
                    AssessmentAttempt.student_id == student_id,
                    AssessmentAttempt.status == IN_PROGRESS,
                    AssessmentAttempt.ends_at > utcnow(),
                )
                .limit(1)
            )
            is not None
        )


def _public_question(question: dict, grants: GrantSnapshot | None) -> dict:
    public = strip_answer_keys(question)
    for field in ("source", "sourceRef"):
        if public.get(field):
            public[field] = mask_current_source(public[field], grants)
    return public


def _attempt_view(
    session: Session, attempt: AssessmentAttempt, grants: GrantSnapshot | None
) -> dict:
    version = attempt_version(session, attempt)
    answers = saved_answers(session, attempt.id)
    return {
        "class_id": attempt.class_id,
        "assessment_id": attempt.activity_id,
        "attempt_id": attempt.id,
        "owner_id": attempt.student_id,
        "title": version.settings_json["title"],
        "code": version.settings_json.get("code", ""),
        "version_id": version.id,
        "version_number": version.version_number,
        "attempt_number": attempt.attempt_number,
        "questions": [_public_question(q, grants) for q in version.content_snapshot_json],
        "saved_answers": {k: v.answer_json for k, v in answers.items()},
        "saved_revision": attempt.saved_revision,
        "saved_at": ensure_utc(attempt.last_saved_at),
        "started_at": ensure_utc(attempt.started_at),
        "ends_at": ensure_utc(attempt.ends_at),
        "server_now": utcnow(),
        "status": attempt.status,
        "receipt": attempt.receipt_json,
    }


def _owned_attempt(session: Session, scope: tuple, attempt_id: UUID, student_id: UUID):
    school_id, class_id, activity_id = scope
    return find_attempt(
        session,
        school_id,
        attempt_id,
        class_id=class_id,
        activity_id=activity_id,
        student_id=student_id,
        lock=True,
    )


def read_attempt(
    session: Session,
    scope: tuple,
    attempt_id: UUID,
    student_id: UUID,
    grants: GrantSnapshot | None,
    *,
    reconcile: bool = False,
    transferred: bool = False,
) -> dict:
    with write_transaction(session):
        attempt = _owned_attempt(session, scope, attempt_id, student_id)
        close_when_expired(session, attempt, utcnow(), transferred=transferred)
        if reconcile and attempt.receipt_json:
            return {"state": "CONFIRMED", "receipt": attempt.receipt_json}
        view = _attempt_view(session, attempt, grants)
        return {"state": "IN_PROGRESS", "attempt": view} if reconcile else view


def _answer_value_is_valid(question: dict, value) -> bool:
    qtype = question["type"]
    if qtype == "short_answer":
        return isinstance(value, str)
    if qtype in {"true_false", "true_false_4"}:
        return isinstance(value, dict) and value.keys() <= {s["id"] for s in question["statements"]}
    options = {o["id"] for o in question["options"]}
    if qtype == "single_choice":
        return isinstance(value, str) and (not value or value in options)
    return isinstance(value, list) and len(value) == len(set(value)) and set(value) <= options


def save_answers(
    session: Session,
    scope: tuple,
    attempt_id: UUID,
    student_id: UUID,
    body: SaveAnswersRequest,
    grants: GrantSnapshot | None,
    *,
    transferred: bool = False,
) -> dict:
    with write_transaction(session):
        attempt = _owned_attempt(session, scope, attempt_id, student_id)
        now = utcnow()
        close_when_expired(session, attempt, now, transferred=transferred)
        if attempt.status == IN_PROGRESS:
            if attempt.saved_revision != body.expected_revision:
                raise AppError(409, "REVISION_CONFLICT")
            version = attempt_version(session, attempt)
            questions = {q["id"]: q for q in version.content_snapshot_json}
            current = saved_answers(session, attempt.id)
            attempt.saved_revision += 1
            attempt.last_saved_at = now
            for qid, answer in body.answers.items():
                if (
                    qid not in questions
                    or answer.question_id not in (None, qid)
                    or not _answer_value_is_valid(questions[qid], answer.value)
                ):
                    raise AppError(422)
                row = current.get(qid)
                if row is None:
                    row = AttemptAnswer(attempt_id=attempt.id, question_id=qid)
                    session.add(row)
                row.answer_json = {"question_id": qid, "value": answer.value}
                row.saved_revision = attempt.saved_revision
            session.flush()
            return _attempt_view(session, attempt, grants)
        receipt = attempt.receipt_json
    if receipt:
        raise AttemptClosedError(receipt)
    raise AppError(409)


def submit(
    session: Session,
    scope: tuple,
    attempt_id: UUID,
    student_id: UUID,
    body,
    *,
    transferred: bool = False,
) -> dict:
    try:
        with write_transaction(session):
            attempt = _owned_attempt(session, scope, attempt_id, student_id)
            now = utcnow()
            close_when_expired(session, attempt, now, transferred=transferred)
            if attempt.receipt_json is None:
                if attempt.saved_revision != body.expected_revision:
                    raise AppError(409, "REVISION_CONFLICT")
                _close(session, attempt, now, operation_id=body.operation_id)
            elif (
                attempt.submit_operation_id == body.operation_id
                and attempt.saved_revision != body.expected_revision
            ):
                raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
            return {"state": "CONFIRMED", "receipt": attempt.receipt_json}
    except IntegrityError:
        if (
            session.scalar(
                select(AssessmentAttempt.id).where(
                    AssessmentAttempt.submit_operation_id == body.operation_id,
                )
            )
            is not None
        ):
            raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH") from None
        raise


def result(
    session: Session,
    scope: tuple,
    attempt_id: UUID,
    student_id: UUID,
    grants: GrantSnapshot | None,
    *,
    appeal_window_days: int,
) -> dict:
    with write_transaction(session):
        attempt = _owned_attempt(session, scope, attempt_id, student_id)
        close_when_expired(session, attempt, utcnow())
        if not attempt.receipt_json:
            raise AppError(409)
        version = attempt_version(session, attempt)
        view = {
            "class_id": attempt.class_id,
            "assessment_id": attempt.activity_id,
            "attempt_id": attempt.id,
            "title": version.settings_json["title"],
            "version_number": version.version_number,
            "released": False,
            "receipt": attempt.receipt_json,
            "effective_grade": None,
            "grade_history": [],
            "questions": [],
        }
        grade = grading.find_target_grade(session, TARGET, attempt.id)
        if grade is None or not grade.is_released:
            return view
        history = grading.approved_history(session, grade)
        revision = grading.effective_revision(session, grade)
        scores = revision.scores_by_question_json or {}
        maxima = grading.question_maxima(session, version)
        answers = saved_answers(session, attempt.id)
        questions = []
        for question in version.content_snapshot_json:
            qid = question["id"]
            answer = answers.get(qid)
            outcome = score_question(
                question, answer.answer_json if answer else None, version.settings_json
            )
            questions.append(
                {
                    "question": _public_question(question, grants),
                    "answer": answer.answer_json if answer else None,
                    "score": scores.get(qid),
                    "maximum": maxima[qid],
                    "released_solution": question.get("explanation") or outcome.correct_answer_text,
                }
            )
        view.update(
            released=True,
            effective_grade=grading.grade_view(grade, revision),
            grade_history=history,
            questions=questions,
            effective_assessment_score=grading.compute_effective_assessment_score(
                session,
                attempt.activity_id,
                student_id,
                version.settings_json["grading_policy"],
                released_only=True,
            ),
        )
        view.update(appeals.summary(session, grade, appeal_window_days))
        return view
