from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.common.access import (
    require_active_student,
    require_active_teacher,
    require_class_teacher_read,
    require_own_history,
)
from app.common.auth import AuthenticatedActor, get_authenticated_actor
from app.common.db import get_db
from app.common.errors import AppError, ErrorEnvelope
from app.integrations.document_client import optional_grants as _optional_grants
from app.modules.assessment import service as assessment_service
from app.modules.assignment import service as assignment_service
from app.modules.grading import service as grading_service
from app.modules.practice import service
from app.modules.practice.schemas import (
    AttachBankItemsRequest,
    CheckPracticeAnswerRequest,
    CopyActivityRequest,
    CreatePracticeRequest,
    ExplainMistakeRequest,
    PublishPracticeRequest,
    ReviewPracticeQuestionRequest,
    SavePracticeAttemptDraftRequest,
    SavePracticeDraftRequest,
    StartPracticeAttemptRequest,
)

Actor = Annotated[AuthenticatedActor, Depends(get_authenticated_actor)]
DbSession = Annotated[Session, Depends(get_db)]

ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 422, 503)
}

router = APIRouter(prefix="/api/v1", tags=["Practice"], responses=ERROR_RESPONSES)


async def _class_meta(request: Request, class_id: UUID, actor: AuthenticatedActor) -> tuple[str, str, int | None]:
    try:
        info = await request.app.state.core.get_class(
            class_id, actor.user, actor.token, request.state.correlation_id
        )
        return info.name, info.primary_teacher_name or "", info.grade_level
    except Exception:  # noqa: BLE001
        return "", "", None


@router.get("/classes/{class_id}/activities")
async def list_activities(
    class_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
    kind: Annotated[str | None, Query()] = None,
) -> dict:
    decision = await require_class_teacher_read(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    pending_counts = grading_service.pending_counts_by_activity(
        session, decision.school_id, class_id
    )
    items = service.list_class_activities(
        session,
        decision.school_id,
        class_id,
        kind=kind,
        pending_counts=pending_counts,
    )
    return {"items": items, "next_cursor": None}


@router.delete("/classes/{class_id}/activities/{activity_id}", status_code=204)
async def delete_draft_activity(
    class_id: UUID,
    activity_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> Response:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    service.delete_draft_activity(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        activity_id=activity_id,
        has_attempts=assessment_service.activity_has_attempts(session, activity_id),
        has_submissions=assignment_service.activity_has_submissions(session, activity_id),
    )
    return Response(status_code=204)


@router.post("/classes/{class_id}/activities/{activity_id}/copy", status_code=201)
async def copy_activity(
    class_id: UUID,
    activity_id: UUID,
    body: CopyActivityRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    source_decision = await require_class_teacher_read(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    target_decision = await require_active_teacher(
        request.app.state.core, actor.user, body.target_class_id, request.state.correlation_id
    )
    if source_decision.school_id != target_decision.school_id:
        raise AppError(403)
    target_grants = await _optional_grants(
        request.app.state.document,
        body.target_class_id,
        target_decision.school_id,
        request.state.correlation_id,
    )
    return service.copy_activity(
        session,
        school_id=source_decision.school_id,
        source_class_id=class_id,
        source_activity_id=activity_id,
        target_class_id=body.target_class_id,
        actor=actor.user,
        title=body.title,
        target_grants=target_grants,
    )


@router.get("/classes/{class_id}/question-bank")
async def list_question_bank(
    class_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
    origin: Annotated[str | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    topic: Annotated[str | None, Query()] = None,
) -> dict:
    decision = await require_class_teacher_read(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    items = service.list_question_bank(
        session, decision.school_id, class_id, origin=origin, qtype=type, topic=topic
    )
    return {"items": items, "next_cursor": None}


@router.post("/classes/{class_id}/practices", status_code=201)
async def create_practice(
    class_id: UUID,
    body: CreatePracticeRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.create_practice(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        actor=actor.user,
        title=body.title,
        topic=body.topic,
    )


@router.get("/classes/{class_id}/practices/{practice_id}/editor")
async def get_practice_editor(
    class_id: UUID,
    practice_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_class_teacher_read(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, _, _ = await _class_meta(request, class_id, actor)
    return await service.load_practice_editor(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        class_name=class_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.put("/classes/{class_id}/practices/{practice_id}/draft")
async def save_practice_draft(
    class_id: UUID,
    practice_id: UUID,
    body: SavePracticeDraftRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, _, _ = await _class_meta(request, class_id, actor)
    return await service.save_practice_draft(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        expected_revision=body.expected_revision,
        title=body.title,
        topic=body.topic,
        questions=body.questions,
        class_name=class_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/items/attach")
async def attach_bank_items(
    class_id: UUID,
    practice_id: UUID,
    body: AttachBankItemsRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, _, _ = await _class_meta(request, class_id, actor)
    return await service.attach_bank_items(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        expected_revision=body.expected_revision,
        learning_item_ids=body.learning_item_ids,
        class_name=class_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/questions/{question_id}/review")
async def review_practice_question(
    class_id: UUID,
    practice_id: UUID,
    question_id: str,
    body: ReviewPracticeQuestionRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, _, _ = await _class_meta(request, class_id, actor)
    return await service.review_practice_question(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        question_id=question_id,
        expected_revision=body.expected_revision,
        approved=body.approved,
        class_name=class_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/publish")
async def publish_practice(
    class_id: UUID,
    practice_id: UUID,
    body: PublishPracticeRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.publish_practice(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        expected_revision=body.expected_revision,
        actor=actor.user,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/versions", status_code=201)
async def create_next_version(
    class_id: UUID,
    practice_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_teacher(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.create_next_practice_version(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        actor=actor.user,
    )


@router.get("/classes/{class_id}/practices")
async def list_student_practices(
    class_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_own_history(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.list_student_practices(
        session, school_id=decision.school_id, class_id=class_id, student_id=actor.user.id
    )


@router.post("/classes/{class_id}/practices/{practice_id}/attempts", status_code=201)
async def start_practice_attempt(
    class_id: UUID,
    practice_id: UUID,
    body: StartPracticeAttemptRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_student(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.start_practice_attempt(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        student_id=actor.user.id,
        intent=body.intent,
    )


@router.get("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}")
async def get_practice_attempt(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_own_history(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, primary_teacher_name, _ = await _class_meta(request, class_id, actor)
    return await service.get_practice_attempt_detail(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
        class_name=class_name,
        primary_teacher_name=primary_teacher_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.put("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/draft")
async def save_practice_attempt_draft(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    body: SavePracticeAttemptDraftRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_student(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.save_practice_attempt_draft(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
        answers=body.answers,
        current_question_index=body.current_question_index,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/check")
async def check_practice_answer(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    body: CheckPracticeAnswerRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_student(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return await service.check_practice_answer(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
        question_id=body.question_id,
        answer=body.answer,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/explain-mistake")
async def explain_mistake(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    body: ExplainMistakeRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_student(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    _, _, grade_level = await _class_meta(request, class_id, actor)
    return await service.explain_practice_mistake(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
        question_id=body.question_id,
        grade_level=grade_level,
        ai=request.app.state.ai,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )


@router.post("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/finish")
async def finish_practice_attempt(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_active_student(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    return service.finish_practice_attempt(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
    )


@router.get("/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/result")
async def get_practice_result(
    class_id: UUID,
    practice_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_own_history(
        request.app.state.core, actor.user, class_id, request.state.correlation_id
    )
    class_name, _, _ = await _class_meta(request, class_id, actor)
    return await service.get_practice_result_detail(
        session,
        school_id=decision.school_id,
        class_id=class_id,
        practice_id=practice_id,
        attempt_id=attempt_id,
        student_id=actor.user.id,
        class_name=class_name,
        document=request.app.state.document,
        correlation_id=request.state.correlation_id,
    )
