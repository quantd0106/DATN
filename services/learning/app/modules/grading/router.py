from asyncio import gather
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.common.access import (
    require_active_teacher,
    require_admin_reason,
    require_class_teacher_read,
    require_grade_submitted_work,
    require_primary_teacher,
)
from app.common.routes import Actor, DbSession, access_args, school_scope
from app.integrations.core_client import ClassAction, CurrentUser
from app.modules.grading import service
from app.modules.grading.schemas import (
    AdjustmentDecision,
    AdjustmentRequest,
    AssessmentGradeRequest,
    ReleaseRequest,
    RevisionDecision,
    RevisionRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Grading"])
CLASS = "/classes/{class_id}"
ASSESSMENT = CLASS + "/assessments/{assessment_id}"
AdminReason = Annotated[str | None, Header(alias="X-Admin-Reason")]


async def _correction_access(
    request: Request,
    actor: Actor,
    class_id: UUID,
    admin_reason: str | None,
    *,
    primary: bool = False,
) -> str | None:
    if "ADMIN" in actor.user.roles and (
        "LECTURER" not in actor.user.roles or admin_reason is not None
    ):
        return require_admin_reason(actor.user, admin_reason)
    check = require_primary_teacher if primary else require_grade_submitted_work
    await check(*access_args(request, actor, class_id))
    return None


@router.get(CLASS + "/grading")
async def queue(class_id: UUID, request: Request, actor: Actor, session: DbSession) -> dict:
    core, user, _, correlation_id = access_args(request, actor, class_id)
    await require_class_teacher_read(core, user, class_id, correlation_id)
    school_id = school_scope(actor)
    result = service.grading_queue(session, school_id, class_id)
    session.rollback()
    ended = {}
    student_ids = list(dict.fromkeys(item["student_id"] for item in result["items"]))
    # Check current enrollment in Core even when the original work remains gradeable.
    for start in range(0, len(student_ids), 10):
        batch = student_ids[start : start + 10]
        decisions = await gather(
            *(
                core.check_access(
                    class_id,
                    CurrentUser(
                        id=student_id, school_id=school_id, roles=("STUDENT",), status="ACTIVE"
                    ),
                    ClassAction.READ_CLASS_CONTENT,
                    correlation_id,
                )
                for student_id in batch
            )
        )
        ended.update(
            {
                student_id: not decision.allowed
                for student_id, decision in zip(batch, decisions, strict=True)
            }
        )
    for item in result["items"]:
        item["membership_ended"] = ended[item["student_id"]]
    return result


@router.get(ASSESSMENT + "/attempts/{attempt_id}/grading")
async def assessment_grading(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_class_teacher_read(*access_args(request, actor, class_id))
    return service.assessment_grading(
        session,
        (school_scope(actor), class_id, assessment_id),
        attempt_id,
        actor.user.id,
        decision.teaching_role,
    )


@router.post(ASSESSMENT + "/attempts/{attempt_id}/grade")
async def grade(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    body: AssessmentGradeRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_grade_submitted_work(*access_args(request, actor, class_id))
    return service.grade_assessment(
        session,
        (school_scope(actor), class_id, assessment_id),
        attempt_id,
        actor.user,
        body,
    )


@router.post(ASSESSMENT + "/release-results")
async def release(
    class_id: UUID,
    assessment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
    body: ReleaseRequest | None = None,
) -> dict:
    await require_primary_teacher(*access_args(request, actor, class_id))
    return service.release_results(
        session,
        (school_scope(actor), class_id, assessment_id),
        actor.user,
        body.reason if body else "",
        request.state.correlation_id,
        release_now=body.release_now if body else False,
    )


@router.post("/grades/{grade_id}/revisions", status_code=201)
async def propose(
    grade_id: UUID,
    body: RevisionRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
    admin_reason: AdminReason = None,
) -> dict:
    school_id = school_scope(actor)
    grade = service.scoped_grade(session, grade_id, school_id)
    reason = await _correction_access(request, actor, grade.class_id, admin_reason)
    return service.propose_revision(
        session,
        school_id,
        grade_id,
        actor.user,
        body,
        request.state.correlation_id,
        reason,
    )


@router.post("/grade-revisions/{revision_id}/decision")
async def decide(
    revision_id: UUID,
    body: RevisionDecision,
    request: Request,
    actor: Actor,
    session: DbSession,
    admin_reason: AdminReason = None,
) -> dict:
    school_id = school_scope(actor)
    grade = service.revision_scope(session, revision_id, school_id)
    reason = await _correction_access(request, actor, grade.class_id, admin_reason, primary=True)
    return service.decide_revision(
        session,
        school_id,
        grade.id,
        revision_id,
        actor.user,
        body,
        request.state.correlation_id,
        reason,
    )


@router.post(CLASS + "/activity-versions/{version_id}/scoring-adjustments", status_code=201)
async def propose_adjustment(
    class_id: UUID,
    version_id: UUID,
    body: AdjustmentRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    return service.propose_adjustment(
        session,
        school_scope(actor),
        class_id,
        version_id,
        actor.user,
        body,
        request.state.correlation_id,
    )


@router.post("/scoring-adjustments/{adjustment_id}/decision")
async def decide_adjustment(
    adjustment_id: UUID,
    body: AdjustmentDecision,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    school_id = school_scope(actor)
    row = service.scoped_adjustment(session, school_id, adjustment_id)
    await require_primary_teacher(*access_args(request, actor, row.class_id))
    return service.decide_adjustment(
        session,
        school_id,
        adjustment_id,
        actor.user,
        body,
        request.state.correlation_id,
    )
