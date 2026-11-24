from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.audit import record_audit_event
from app.common.db import ensure_utc, utcnow, write_transaction
from app.common.errors import AppError
from app.common.outbox import OutboxEvent, record_learning_notification, record_outbox_event
from app.common.pagination import Pagination, encode_cursor
from app.integrations.core_client import CurrentUser, TeachingRole
from app.modules.appeals import service as appeals
from app.modules.assessment import service as assessment
from app.modules.assignment import service as assignment
from app.modules.bank import activities
from app.modules.bank.models import Activity, ActivityKind, ActivityVersion, VersionStatus
from app.modules.grading.models import (
    AdjustmentAction,
    Grade,
    GradeChangeKind,
    GradeRevision,
    GradeRevisionStatus,
    GradeTargetType,
    ScoringAdjustment,
)

APPROVED = GradeRevisionStatus.APPROVED
PROPOSED = GradeRevisionStatus.PROPOSED


def find_target_grade(session: Session, target_type: str, target_id: UUID, *, lock: bool = False):
    query = select(Grade).where(Grade.target_type == target_type, Grade.target_id == target_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def scoped_grade(session: Session, grade_id: UUID, school_id: UUID, *, lock: bool = False) -> Grade:
    query = select(Grade).where(Grade.id == grade_id, Grade.school_id == school_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    grade = session.scalar(query)
    if grade is None:
        raise AppError(404)
    return grade


def effective_revision(session: Session, grade: Grade) -> GradeRevision:
    rev = (
        session.get(GradeRevision, grade.effective_revision_id)
        if grade.effective_revision_id
        else None
    )
    if rev is None or rev.grade_id != grade.id or rev.status != APPROVED:
        raise AppError(409, "GRADE_VERSION_CONFLICT")
    return rev


def grade_view(grade: Grade, revision: GradeRevision) -> dict:
    return {
        "id": grade.id,
        "grade_id": grade.id,
        "revision_id": revision.id,
        "grade_version": grade.effective_version,
        "version": revision.version,
        "score": float(revision.total_score),
        "total_score": float(revision.total_score),
        "max_score": float(revision.max_score),
        "scores": revision.scores_by_question_json,
        "feedback": revision.feedback,
        "reason": revision.reason,
        "teacher_id": revision.decided_by,
        "teacher_name": revision.decided_by_name,
        "confirmed_at": ensure_utc(revision.decided_at) if revision.decided_at else None,
    }


def approved_history(session: Session, grade: Grade) -> list[dict]:
    revisions = session.scalars(
        select(GradeRevision)
        .where(
            GradeRevision.grade_id == grade.id,
            GradeRevision.status == APPROVED,
        )
        .order_by(GradeRevision.version)
    )
    return [grade_view(grade, revision) for revision in revisions]


def revision_view(grade: Grade, rev: GradeRevision) -> dict:
    return {
        **grade_view(grade, rev),
        "id": rev.id,
        "class_id": grade.class_id,
        "target_type": grade.target_type,
        "target_id": grade.target_id,
        "status": rev.status,
        "base_version": rev.base_version,
        "change_kind": rev.change_kind,
        "proposed_by": rev.proposed_by,
        "proposed_at": ensure_utc(rev.proposed_at),
        "decision_reason": rev.decision_reason,
        "appeal_id": rev.appeal_id,
    }


def _event(session: Session, grade: Grade, rev: GradeRevision, now) -> None:
    record_outbox_event(
        session,
        event_type="grade.revision.confirmed",
        aggregate_type="grade",
        aggregate_id=grade.id,
        aggregate_version=grade.effective_version,
        payload_json={
            "school_id": str(grade.school_id),
            "class_id": str(grade.class_id),
            "student_id": str(grade.student_id),
            "activity_id": str(grade.activity_id),
            "target_type": grade.target_type,
            "target_id": str(grade.target_id),
            "grade_id": str(grade.id),
            "grade_version": grade.effective_version,
            "total_score": float(rev.total_score),
            "max_score": float(rev.max_score),
            "confirmed_at": ensure_utc(now).isoformat(),
        },
    )


def _release_event(session: Session, grade: Grade, rev: GradeRevision, now) -> None:
    emitted = session.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.event_type == "grade.revision.confirmed",
            OutboxEvent.aggregate_type == "grade",
            OutboxEvent.aggregate_id == str(grade.id),
            OutboxEvent.aggregate_version == grade.effective_version,
        )
        .limit(1)
    )
    if emitted is None:
        _event(session, grade, rev, now)


def _confirm(
    session: Session,
    grade: Grade,
    rev: GradeRevision,
    actor_id: UUID,
    actor_name: str,
    reason: str = "",
    *,
    emit: bool = False,
) -> None:
    now = utcnow()
    rev.status = APPROVED
    rev.decided_by, rev.decided_by_name, rev.decided_at = actor_id, actor_name, now
    rev.decision_reason = reason or None
    grade.effective_version = rev.version
    grade.effective_revision_id = rev.id
    if grade.is_released or emit:
        _event(session, grade, rev, now)
    else:
        session.flush()


def _revision(
    session: Session,
    grade: Grade,
    *,
    actor_id: UUID,
    actor_name: str = "",
    scores: dict | None,
    total_score: float,
    max_score: float,
    change_kind: str,
    feedback: str = "",
    reason: str = "",
    appeal_id: UUID | None = None,
    adjustment_id: UUID | None = None,
) -> GradeRevision:
    if not 0 <= total_score <= max_score <= 9999.99:
        raise AppError(422)
    version = session.scalar(
        select(func.max(GradeRevision.version)).where(GradeRevision.grade_id == grade.id)
    )
    row = GradeRevision(
        grade_id=grade.id,
        version=(version or 0) + 1,
        base_version=grade.effective_version,
        scores_by_question_json=scores,
        total_score=round(total_score, 2),
        max_score=round(max_score, 2),
        status=PROPOSED,
        change_kind=change_kind,
        feedback=feedback,
        reason=reason,
        appeal_id=appeal_id,
        proposed_by=actor_id,
        proposed_by_name=actor_name,
        proposed_at=utcnow(),
        adjustment_id=adjustment_id,
    )
    session.add(row)
    session.flush()
    return row


def record_initial_grade(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    activity_id: UUID,
    student_id: UUID,
    target_type: str,
    target_id: UUID,
    scores: dict | None,
    total_score: float,
    max_score: float,
    actor_id: UUID,
    actor_name: str = "",
    feedback: str = "",
    teacher: bool = False,
) -> Grade:
    existing = find_target_grade(session, target_type, target_id, lock=True)
    if existing is not None:
        raise AppError(409, "GRADE_VERSION_CONFLICT")
    released = target_type == GradeTargetType.ASSIGNMENT_SUBMISSION and teacher
    grade = Grade(
        school_id=school_id,
        class_id=class_id,
        activity_id=activity_id,
        student_id=student_id,
        target_type=target_type,
        target_id=target_id,
        is_released=released,
        released_at=utcnow() if released else None,
    )
    session.add(grade)
    session.flush()
    rev = _revision(
        session,
        grade,
        scores=scores,
        total_score=total_score,
        max_score=max_score,
        actor_id=actor_id,
        actor_name=actor_name,
        feedback=feedback,
        change_kind=GradeChangeKind.INITIAL_TEACHER if teacher else GradeChangeKind.INITIAL_AUTO,
    )
    _confirm(session, grade, rev, actor_id, actor_name)
    return grade


def compute_effective_assessment_score(
    session: Session,
    activity_id: UUID,
    student_id: UUID,
    grading_policy: str,
    *,
    released_only: bool = False,
) -> float | None:
    by_target = {
        grade.target_id: grade
        for grade in session.scalars(
            select(Grade).where(
                Grade.activity_id == activity_id,
                Grade.student_id == student_id,
                Grade.target_type == GradeTargetType.ASSESSMENT_ATTEMPT,
                Grade.is_released.is_(True) if released_only else True,
            )
        )
    }
    scores = []
    for attempt in assessment.attempts_for_scoring(session, activity_id, student_id):
        grade = by_target.get(attempt.id)
        if not grade:
            continue
        revision = effective_revision(session, grade)
        if any(score is None for score in (revision.scores_by_question_json or {}).values()):
            continue
        scores.append(float(revision.total_score))
    if not scores:
        return None
    if grading_policy == "BEST":
        return max(scores)
    if grading_policy == "LATEST":
        return scores[-1]
    if grading_policy == "AVERAGE":
        return round(sum(scores) / len(scores), 2)
    raise AppError(422)


def _expect(grade: Grade, version: int) -> None:
    if grade.effective_version != version:
        raise AppError(409, "GRADE_VERSION_CONFLICT")


def _audit(
    session: Session,
    row,
    actor: CurrentUser,
    correlation_id: str,
    action: str,
    target_id: UUID,
    *,
    reason_code: str | None = None,
    metadata: dict | None = None,
) -> None:
    record_audit_event(
        session,
        correlation_id=correlation_id,
        school_id=row.school_id,
        class_id=row.class_id,
        actor_id=actor.id,
        actor_roles=actor.roles,
        action=action,
        target_type="scoring_adjustment" if isinstance(row, ScoringAdjustment) else "grade",
        target_id=target_id,
        reason_code=reason_code,
        metadata=metadata,
    )


def _sum_scores(scores: dict) -> float:
    return round(sum(score or 0 for score in scores.values()), 2)


def assessment_grading(
    session: Session,
    scope: tuple[UUID, UUID, UUID],
    attempt_id: UUID,
    actor_id: UUID,
    teaching_role: TeachingRole | None,
) -> dict:
    school_id, class_id, assessment_id = scope
    attempt = assessment.find_attempt(
        session, school_id, attempt_id, class_id=class_id, activity_id=assessment_id
    )
    if attempt.receipt_json is None:
        raise AppError(404)
    target = GradeTargetType.ASSESSMENT_ATTEMPT
    grade = find_target_grade(session, target, attempt.id)
    current = effective_revision(session, grade) if grade else None
    scores = (current.scores_by_question_json or {}) if current else {}
    version = assessment.attempt_version(session, attempt)
    answers = assessment.saved_answers(session, attempt.id)
    adjustments = _adjustments(session, version.id)
    adjusted = {
        row.question_id
        for row in adjustments
        if row.action in (AdjustmentAction.VOID_QUESTION, AdjustmentAction.GRANT_FULL_CREDIT)
    }
    voided = {
        row.question_id for row in adjustments if row.action == AdjustmentAction.VOID_QUESTION
    }
    released = bool(grade and grade.is_released)
    questions = []
    for question in version.content_snapshot_json:
        qid = question["id"]
        answer = answers[qid].answer_json if qid in answers else None
        questions.append(
            {
                "question": question,
                "answer": answer,
                "score": scores.get(qid),
                "feedback": "",  # Revisions store feedback for the whole attempt.
                "maximum": 0.0 if qid in voided else float(question["points"]),
                "can_grade": bool(
                    grade
                    and not released
                    and qid not in adjusted
                    and assessment.score_question(
                        question, answer, version.settings_json
                    ).requires_manual_grading
                ),
            }
        )
    pending = (
        session.scalar(
            select(GradeRevision)
            .where(GradeRevision.grade_id == grade.id, GradeRevision.status == PROPOSED)
            .order_by(GradeRevision.version.desc())
            .limit(1)
        )
        if grade
        else None
    )
    target_appeals = appeals.grade_appeals(session, grade) if grade else []
    appeal = next(
        (row for row in target_appeals if pending and row["id"] == pending.appeal_id),
        _current_appeal(target_appeals),
    )
    return {
        "class_id": class_id,
        "assessment_id": assessment_id,
        "attempt_id": attempt.id,
        "student_id": attempt.student_id,
        "student_name": attempt.student_name,
        "title": version.settings_json["title"],
        "version_number": version.version_number,
        "released": released,
        "questions": questions,
        "grade_id": grade.id if grade else None,
        "grade_version": grade.effective_version if grade else 0,
        "effective_grade": grade_view(grade, current) if current else None,
        "grade_history": approved_history(session, grade) if grade else [],
        "pending_proposal": revision_view(grade, pending) if pending else None,
        "appeal_id": appeal["id"] if appeal else None,
        "appeal_reason": appeal["reason"] if appeal else None,
        "can_approve": bool(
            teaching_role == "PRIMARY" and pending and pending.proposed_by != actor_id
        ),
    }


def grade_assessment(
    session: Session, scope: tuple, attempt_id: UUID, actor: CurrentUser, body
) -> dict:
    school_id, class_id, activity_id = scope
    with write_transaction(session):
        attempt = assessment.find_attempt(
            session,
            school_id,
            attempt_id,
            class_id=class_id,
            activity_id=activity_id,
            lock=True,
        )
        grade = find_target_grade(
            session, GradeTargetType.ASSESSMENT_ATTEMPT, attempt.id, lock=True
        )
        if grade is None or grade.is_released:
            raise AppError(409)
        _expect(grade, body.expected_grade_version)
        current = effective_revision(session, grade)
        version = assessment.attempt_version(session, attempt)
        answers = assessment.saved_answers(session, attempt.id)
        maxima = question_maxima(session, version)
        scores = dict(current.scores_by_question_json)
        manual = {
            q["id"]
            for q in version.content_snapshot_json
            if maxima[q["id"]] > 0
            and assessment.score_question(
                q,
                answers[q["id"]].answer_json if q["id"] in answers else None,
                version.settings_json,
            ).requires_manual_grading
            and not _full_credit(session, version.id, q["id"])
        }
        if not body.scores.keys() <= manual:
            raise AppError(422)
        _update_scores(scores, body.scores, maxima)
        rev = _revision(
            session,
            grade,
            actor_id=actor.id,
            actor_name=actor.display_name or "",
            scores=scores,
            total_score=_sum_scores(scores),
            max_score=float(current.max_score),
            change_kind=GradeChangeKind.INITIAL_TEACHER,
            feedback=body.feedback,
        )
        _confirm(session, grade, rev, actor.id, actor.display_name or "")
        if all(score is not None for score in scores.values()):
            attempt.status = "GRADED"
        return assessment_grading(session, scope, attempt.id, actor.id, None)


def release_results(
    session: Session,
    scope: tuple,
    actor: CurrentUser,
    reason: str,
    correlation: str,
    *,
    release_now: bool = False,
) -> dict:
    with write_transaction(session):
        act = activities.get_activity(session, *scope, ActivityKind.ASSESSMENT, lock=True)
        activities.get_version(session, act, act.current_version_id)
        attempts = assessment.lock_activity_attempts(session, act)
        now = utcnow()
        count = 0
        for attempt in attempts:
            version = assessment.attempt_version(session, attempt)
            release_at = (
                version.settings_json.get("results_at") or version.settings_json["closes_at"]
            )
            if not release_now and assessment.parse_time(release_at) > now:
                continue
            assessment.close_when_expired(session, attempt, now)
            if attempt.receipt_json is None:
                continue
            grade = find_target_grade(
                session, GradeTargetType.ASSESSMENT_ATTEMPT, attempt.id, lock=True
            )
            if grade is None or grade.is_released:
                continue
            rev = effective_revision(session, grade)
            if any(s is None for s in (rev.scores_by_question_json or {}).values()):
                continue
            grade.is_released, grade.released_at = True, now
            attempt.status = "RELEASED"
            attempt.results_released_at, attempt.results_released_by = now, actor.id
            _release_event(session, grade, rev, now)
            _audit(
                session,
                grade,
                actor,
                correlation,
                "ASSESSMENT_RESULT_RELEASED",
                grade.id,
                metadata={"grade_version": grade.effective_version, "reason": reason},
            )
            record_learning_notification(
                session,
                event_type="ASSESSMENT_RESULT_RELEASED",
                school_id=grade.school_id,
                class_id=grade.class_id,
                resource_type="assessment_attempt",
                resource_id=attempt.id,
                student_id=grade.student_id,
            )
            count += 1
        return {"released_count": count}


def target_version(session: Session, grade: Grade) -> ActivityVersion:
    if grade.target_type == GradeTargetType.ASSESSMENT_ATTEMPT:
        attempt = assessment.find_attempt(
            session,
            grade.school_id,
            grade.target_id,
            class_id=grade.class_id,
            activity_id=grade.activity_id,
            student_id=grade.student_id,
        )
        return assessment.attempt_version(session, attempt)
    submission = assignment.find_submission(session, grade.school_id, grade.target_id)
    if (submission.class_id, submission.activity_id, submission.student_id) != (
        grade.class_id,
        grade.activity_id,
        grade.student_id,
    ):
        raise AppError(404)
    return assignment.submission_version(session, submission)


def _update_scores(scores: dict, changes: dict, maxima: dict) -> None:
    if any(qid not in maxima or score > maxima[qid] for qid, score in changes.items()):
        raise AppError(422)
    scores.update({qid: round(score, 2) for qid, score in changes.items()})


def propose_revision(
    session: Session,
    school_id: UUID,
    grade_id: UUID,
    actor: CurrentUser,
    body,
    correlation: str,
    admin_reason: str | None,
) -> dict:
    with write_transaction(session):
        grade = scoped_grade(session, grade_id, school_id, lock=True)
        if not grade.is_released:
            raise AppError(409)
        _expect(grade, body.expected_grade_version)
        current = effective_revision(session, grade)
        scores = current.scores_by_question_json
        total = float(current.total_score)
        if body.scores is not None:
            if scores is None:
                raise AppError(422)
            scores = dict(scores)
            _update_scores(
                scores, body.scores, question_maxima(session, target_version(session, grade))
            )
            total = _sum_scores(scores)
            if body.total_score is not None and round(body.total_score, 2) != total:
                raise AppError(422)
        elif body.total_score is not None:
            total = body.total_score
        if body.appeal_id:
            appeals.mark_proposed(session, grade, body.appeal_id)
        rev = _revision(
            session,
            grade,
            actor_id=actor.id,
            actor_name=actor.display_name or "",
            scores=scores,
            total_score=total,
            max_score=float(current.max_score),
            change_kind=GradeChangeKind.ADMIN_EXCEPTION
            if admin_reason
            else GradeChangeKind.CORRECTION_PROPOSAL,
            feedback=body.feedback,
            reason=body.reason,
            appeal_id=body.appeal_id,
        )
        _audit(
            session,
            grade,
            actor,
            correlation,
            "GRADE_REVISION_PROPOSED",
            rev.id,
            reason_code=admin_reason,
            metadata={"base_version": grade.effective_version},
        )
        record_learning_notification(
            session,
            event_type="GRADE_PROPOSAL_PENDING",
            school_id=grade.school_id,
            class_id=grade.class_id,
            resource_type="grade_revision",
            resource_id=rev.id,
        )
        return revision_view(grade, rev)


def revision_scope(session: Session, revision_id: UUID, school_id: UUID) -> Grade:
    grade = session.scalar(
        select(Grade)
        .join(GradeRevision, Grade.id == GradeRevision.grade_id)
        .where(
            GradeRevision.id == revision_id,
            Grade.school_id == school_id,
        )
    )
    if grade is None:
        raise AppError(404)
    return grade


def validate_appeal_resolution(
    session: Session,
    appeal_id: UUID,
    grade_id: UUID,
    revision_id: UUID | None,
) -> None:
    if revision_id is not None:
        rev = session.get(GradeRevision, revision_id)
        if rev is None or rev.appeal_id != appeal_id or rev.grade_id != grade_id:
            raise AppError(404)
        if rev.status != APPROVED:
            raise AppError(409)
    pending = session.scalar(
        select(GradeRevision.id)
        .where(
            GradeRevision.appeal_id == appeal_id,
            GradeRevision.grade_id == grade_id,
            GradeRevision.status == PROPOSED,
        )
        .limit(1)
    )
    if pending:
        raise AppError(409)


def decide_revision(
    session: Session,
    school_id: UUID,
    grade_id: UUID,
    revision_id: UUID,
    actor: CurrentUser,
    body,
    correlation: str,
    admin_reason: str | None,
) -> dict:
    with write_transaction(session):
        grade = scoped_grade(session, grade_id, school_id, lock=True)
        rev = session.get(GradeRevision, revision_id, populate_existing=True)
        if rev is None or rev.grade_id != grade.id:
            raise AppError(404)
        if rev.proposed_by == actor.id and body.decision == "APPROVE":
            raise AppError(403, "SELF_APPROVAL_FORBIDDEN")
        if rev.status != PROPOSED:
            raise AppError(409)
        _expect(grade, body.expected_grade_version)
        if body.decision == "APPROVE":
            _expect(grade, rev.base_version)
            _confirm(session, grade, rev, actor.id, actor.display_name or "", body.reason)
        else:
            rev.status = GradeRevisionStatus.REJECTED
            rev.decided_by, rev.decided_by_name, rev.decided_at = (
                actor.id,
                actor.display_name,
                utcnow(),
            )
            rev.decision_reason = body.reason
        _audit(
            session,
            grade,
            actor,
            correlation,
            "GRADE_REVISION_DECIDED",
            rev.id,
            reason_code=admin_reason,
            metadata={"grade_version": grade.effective_version, "decision": body.decision},
        )
        return revision_view(grade, rev)


def _adjustments(session: Session, version_id: UUID) -> list[ScoringAdjustment]:
    return list(
        session.scalars(
            select(ScoringAdjustment)
            .where(
                ScoringAdjustment.activity_version_id == version_id,
                ScoringAdjustment.status == APPROVED,
            )
            .order_by(ScoringAdjustment.decided_at, ScoringAdjustment.id)
        )
    )


def question_maxima(session: Session, version: ActivityVersion) -> dict[str, float]:
    void = {
        a.question_id
        for a in _adjustments(session, version.id)
        if a.action == AdjustmentAction.VOID_QUESTION
    }
    return {
        q["id"]: 0.0 if q["id"] in void else float(q["points"])
        for q in version.content_snapshot_json
    }


def _full_credit(session: Session, version_id: UUID, qid: str) -> bool:
    return any(
        a.question_id == qid and a.action == AdjustmentAction.GRANT_FULL_CREDIT
        for a in _adjustments(session, version_id)
    )


def apply_scoring_adjustments(
    session: Session,
    version: ActivityVersion,
    answers: dict,
    scores: dict,
    *,
    question_id: str | None = None,
) -> tuple[dict, float]:
    adjustments = _adjustments(session, version.id)
    questions = {q["id"]: q for q in version.content_snapshot_json}
    void = {a.question_id for a in adjustments if a.action == AdjustmentAction.VOID_QUESTION}
    for adjustment in adjustments:
        qid = adjustment.question_id
        if question_id is not None and question_id != qid:
            continue
        q = questions[qid]
        if qid in void:
            scores[qid] = 0.0
        elif adjustment.action == AdjustmentAction.GRANT_FULL_CREDIT:
            scores[qid] = float(q["points"])
        else:
            answer = answers.get(qid)
            if answer is None:
                continue
            updated = {**q, "key": adjustment.payload_json["key"]}
            outcome = assessment.score_question(updated, answer.answer_json, version.settings_json)
            if outcome.score is not None and (
                outcome.score > (scores.get(qid) or 0)
                or scores.get(qid) is None
                and outcome.is_correct
            ):
                scores[qid] = outcome.score
    maximum = sum(float(q["points"]) for qid, q in questions.items() if qid not in void)
    return scores, round(maximum, 2)


def version_scope(session: Session, school_id: UUID, class_id: UUID, version_id: UUID):
    row = session.execute(
        select(Activity, ActivityVersion)
        .join(
            ActivityVersion,
            ActivityVersion.activity_id == Activity.id,
        )
        .where(
            Activity.school_id == school_id,
            Activity.class_id == class_id,
            Activity.kind == ActivityKind.ASSESSMENT,
            ActivityVersion.id == version_id,
        )
    ).one_or_none()
    if row is None:
        raise AppError(404)
    return row


def adjustment_view(row: ScoringAdjustment) -> dict:
    return {
        "id": row.id,
        "version_id": row.activity_version_id,
        "class_id": row.class_id,
        "question_id": row.question_id,
        "action": row.action,
        "status": row.status,
        "payload": row.payload_json,
        "reason": row.reason,
        "decision_reason": row.decision_reason,
        "proposed_by": row.proposed_by,
        "created_at": ensure_utc(row.created_at),
        "decided_at": ensure_utc(row.decided_at) if row.decided_at else None,
    }


def list_version_adjustments(session: Session, version_id: UUID) -> list[dict]:
    rows = session.scalars(
        select(ScoringAdjustment)
        .where(ScoringAdjustment.activity_version_id == version_id)
        .order_by(ScoringAdjustment.created_at.desc(), ScoringAdjustment.id)
    )
    return [adjustment_view(row) for row in rows]


def propose_adjustment(
    session: Session,
    school_id: UUID,
    class_id: UUID,
    version_id: UUID,
    actor: CurrentUser,
    body,
    correlation: str,
) -> dict:
    with write_transaction(session):
        _, ver = version_scope(session, school_id, class_id, version_id)
        if ver.status not in (
            VersionStatus.PUBLISHED,
            VersionStatus.SUPERSEDED,
            VersionStatus.CLOSED,
        ):
            raise AppError(409)
        q = next((q for q in ver.content_snapshot_json if q["id"] == body.question_id), None)
        if q is None:
            raise AppError(404)
        payload = body.payload
        if body.action == AdjustmentAction.ACCEPT_ADDITIONAL_KEY:
            if "key" not in payload or q.get("manual_grading_required"):
                raise AppError(422)
            assessment.validate_question_key({**q, "key": payload["key"]})
            payload = {"key": payload["key"]}
        elif payload:
            raise AppError(422)
        row = ScoringAdjustment(
            school_id=school_id,
            class_id=class_id,
            activity_version_id=ver.id,
            question_id=body.question_id,
            action=body.action,
            payload_json=payload,
            reason=body.reason,
            proposed_by=actor.id,
        )
        session.add(row)
        session.flush()
        _audit(session, row, actor, correlation, "SCORING_ADJUSTMENT_PROPOSED", row.id)
        return adjustment_view(row)


def scoped_adjustment(session: Session, school_id: UUID, adjustment_id: UUID) -> ScoringAdjustment:
    row = session.scalar(
        select(ScoringAdjustment).where(
            ScoringAdjustment.id == adjustment_id,
            ScoringAdjustment.school_id == school_id,
        )
    )
    if row is None:
        raise AppError(404)
    return row


def decide_adjustment(
    session: Session,
    school_id: UUID,
    adjustment_id: UUID,
    actor: CurrentUser,
    body,
    correlation: str,
) -> dict:
    with write_transaction(session):
        row = scoped_adjustment(session, school_id, adjustment_id)
        act, ver = version_scope(session, school_id, row.class_id, row.activity_version_id)
        act = activities.get_activity(
            session, school_id, row.class_id, act.id, ActivityKind.ASSESSMENT, lock=True
        )
        session.refresh(row, with_for_update=True)
        if row.status != PROPOSED:
            raise AppError(409)
        attempts = (
            assessment.lock_activity_attempts(session, act, version_id=ver.id)
            if body.decision == "APPROVE"
            else []
        )
        row.status = APPROVED if body.decision == "APPROVE" else GradeRevisionStatus.REJECTED
        row.decided_by, row.decided_at, row.decision_reason = actor.id, utcnow(), body.reason
        session.flush()
        count = 0
        for attempt in attempts:
            if attempt.receipt_json is None:
                continue
            grade = find_target_grade(
                session, GradeTargetType.ASSESSMENT_ATTEMPT, attempt.id, lock=True
            )
            current = effective_revision(session, grade)
            scores, maximum = apply_scoring_adjustments(
                session,
                ver,
                assessment.saved_answers(session, attempt.id),
                dict(current.scores_by_question_json),
                question_id=row.question_id,
            )
            # Retain any previously approved total-only correction as an offset.
            difference = float(current.total_score) - _sum_scores(current.scores_by_question_json)
            total = min(maximum, max(0, _sum_scores(scores) + difference))
            rev = _revision(
                session,
                grade,
                actor_id=actor.id,
                actor_name=actor.display_name or "",
                scores=scores,
                total_score=total,
                max_score=maximum,
                feedback=current.feedback,
                reason=row.reason,
                adjustment_id=row.id,
                change_kind=GradeChangeKind.SCORING_ADJUSTMENT,
            )
            _confirm(
                session, grade, rev, actor.id, actor.display_name or "", body.reason, emit=True
            )
            count += 1
        _audit(
            session,
            row,
            actor,
            correlation,
            "SCORING_ADJUSTMENT_DECIDED",
            row.id,
            metadata={"decision": body.decision, "regraded_count": count},
        )
        return {**adjustment_view(row), "regraded_count": count}


def _current_appeal(rows: list[dict]) -> dict | None:
    return next((row for row in rows if row["status"] != "RESOLVED"), rows[0] if rows else None)


def grading_queue(session: Session, school_id: UUID, class_id: UUID) -> dict:
    class_appeals = appeals.class_appeals(session, school_id, class_id)
    appeals_by_grade: dict[UUID, list[dict]] = {}
    for appeal in class_appeals:
        appeals_by_grade.setdefault(appeal["grade_id"], []).append(appeal)
    rows = [
        (GradeTargetType.ASSESSMENT_ATTEMPT, a)
        for a in assessment.submitted_attempts(session, school_id, class_id)
    ] + [
        (GradeTargetType.ASSIGNMENT_SUBMISSION, s)
        for s in assignment.class_submissions(session, school_id, class_id)
    ]
    items = []
    for target_type, row in sorted(
        rows, key=lambda item: ensure_utc(item[1].submitted_at), reverse=True
    ):
        official = target_type == GradeTargetType.ASSESSMENT_ATTEMPT
        if official:
            kind = ActivityKind.ASSESSMENT
            version = assessment.attempt_version(session, row)
            target_path = f"assessments/{row.activity_id}/attempts/{row.id}"
        else:
            kind = ActivityKind.ASSIGNMENT
            version = assignment.submission_version(session, row)
            target_path = f"assignments/{row.activity_id}/submissions/{row.id}"
        grade = find_target_grade(session, target_type, row.id)
        current = effective_revision(session, grade) if grade else None
        appeal = _current_appeal(appeals_by_grade.get(grade.id, [])) if grade else None
        items.append(
            {
                "id": row.id,
                "activity_id": row.activity_id,
                "class_id": class_id,
                "student_id": row.student_id,
                "student_name": row.student_name,
                "kind": kind,
                "title": version.settings_json["title"],
                "submitted_at": ensure_utc(row.submitted_at),
                "number": getattr(row, "attempt_number", getattr(row, "submission_number", 1)),
                "late": getattr(row, "is_late", False),
                "grade_id": grade.id if grade else None,
                "grade_version": grade.effective_version if grade else 0,
                "released": bool(grade and grade.is_released),
                "effective_score": float(current.total_score) if current else None,
                "feedback": current.feedback if current else "",
                "grade_history": approved_history(session, grade) if grade else [],
                "appeal_id": appeal["id"] if appeal else None,
                "appeal_reason": appeal["reason"] if appeal else None,
                "graded": current is not None
                and all(s is not None for s in (current.scores_by_question_json or {}).values()),
                "href": f"/teacher/classes/{class_id}/{target_path}/grade",
            }
        )
    proposals = session.execute(
        select(Grade, GradeRevision)
        .join(
            GradeRevision,
            GradeRevision.grade_id == Grade.id,
        )
        .where(
            Grade.school_id == school_id,
            Grade.class_id == class_id,
            GradeRevision.status == PROPOSED,
        )
    )
    return {
        "items": items,
        "appeals": class_appeals,
        "proposals": [revision_view(grade, rev) for grade, rev in proposals],
    }


def pending_counts_by_activity(session: Session, school_id: UUID, class_id: UUID) -> dict[UUID, int]:
    counts: dict[UUID, int] = {}
    for attempt in assessment.submitted_attempts(session, school_id, class_id):
        grade = find_target_grade(session, GradeTargetType.ASSESSMENT_ATTEMPT, attempt.id)
        if grade is None or not grade.is_released:
            counts[attempt.activity_id] = counts.get(attempt.activity_id, 0) + 1
    for submission in assignment.class_submissions(session, school_id, class_id):
        if not submission.is_graded_version:
            continue
        grade = find_target_grade(session, GradeTargetType.ASSIGNMENT_SUBMISSION, submission.id)
        if grade is None:
            counts[submission.activity_id] = counts.get(submission.activity_id, 0) + 1
    return counts


def confirmed_grades(
    session: Session, school_id: UUID, class_id: UUID, since_version: int, pagination: Pagination
) -> dict:
    query = (
        select(Grade, GradeRevision, Activity)
        .join(
            GradeRevision,
            GradeRevision.id == Grade.effective_revision_id,
        )
        .join(Activity, Activity.id == Grade.activity_id)
        .where(
            Grade.school_id == school_id,
            Grade.class_id == class_id,
            Grade.is_released.is_(True),
            GradeRevision.status == APPROVED,
            GradeRevision.grade_id == Grade.id,
            Grade.effective_version > since_version,
        )
    )
    if pagination.cursor is not None:
        query = query.where(Grade.id > pagination.cursor)
    rows = list(session.execute(query.order_by(Grade.id).limit(pagination.limit + 1)))
    items = []
    for grade, rev, activity in rows[: pagination.limit]:
        topic = target_version(session, grade).settings_json.get("topic", activity.topic)
        items.append(
            {
                "grade_id": grade.id,
                "grade_version": grade.effective_version,
                "topic": topic,
                "activity_id": grade.activity_id,
                "school_id": grade.school_id,
                "class_id": grade.class_id,
                "student_id": grade.student_id,
                "target_type": grade.target_type,
                "target_id": grade.target_id,
                "total_score": float(rev.total_score),
                "max_score": float(rev.max_score),
                "confirmed_at": ensure_utc(rev.decided_at or grade.released_at),
            }
        )
    return {
        "items": items,
        "next_cursor": encode_cursor(items[-1]["grade_id"])
        if len(rows) > pagination.limit
        else None,
    }
