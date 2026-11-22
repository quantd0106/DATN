from uuid import UUID

from fastapi import APIRouter, Request

from app.common.access import require_grade_submitted_work, require_own_history
from app.common.errors import AppError
from app.common.routes import Actor, DbSession, access_args, get_grants, school_scope
from app.modules.appeals import service
from app.modules.appeals.schemas import AppealRequest, ResolveRequest
from app.modules.assessment import service as assessment
from app.modules.assignment import service as assignment
from app.modules.grading import service as grading

router = APIRouter(prefix="/api/v1", tags=["Appeals"])


@router.post("/classes/{class_id}/appeals", status_code=201)
async def appeal(
    class_id: UUID,
    body: AppealRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_own_history(*access_args(request, actor, class_id))
    return service.create(
        session,
        school_scope(actor),
        class_id,
        actor.user.id,
        body,
        request.app.state.settings.default_appeal_window_days,
    )


@router.post("/classes/{class_id}/appeals/{appeal_id}/resolve")
async def resolve(
    class_id: UUID,
    appeal_id: UUID,
    body: ResolveRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_grade_submitted_work(*access_args(request, actor, class_id))
    return service.resolve(
        session,
        school_scope(actor),
        class_id,
        appeal_id,
        actor.user,
        body,
        request.state.correlation_id,
    )


@router.get("/history/results/{result_id}")
async def history(result_id: UUID, request: Request, actor: Actor, session: DbSession) -> dict:
    school_id = school_scope(actor)
    window_days = request.app.state.settings.default_appeal_window_days
    try:
        attempt = assessment.find_attempt(session, school_id, result_id, student_id=actor.user.id)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        submission = assignment.find_submission(session, school_id, result_id)
        if submission.student_id != actor.user.id:
            raise AppError(404) from None
        await require_own_history(*access_args(request, actor, submission.class_id))
        grade = grading.find_target_grade(session, assignment.TARGET, result_id)
        view = assignment.submission_view(session, submission)
        if grade is not None and grade.is_released:
            view.update(service.summary(session, grade, window_days))
    else:
        await require_own_history(*access_args(request, actor, attempt.class_id))
        ids = school_id, attempt.class_id, attempt.activity_id
        grants = await get_grants(request, attempt.class_id, school_id)
        view = assessment.result(
            session, ids, result_id, actor.user.id, grants, appeal_window_days=window_days
        )
    return {**view, "limited_history": True}
