from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request

from app.common.access import (
    require_active_student,
    require_active_teacher,
    require_class_teacher_read,
    require_own_history,
    require_primary_teacher,
)
from app.common.errors import AppError
from app.common.routes import (
    Actor,
    DbSession,
    access_args,
    check_operation_header,
    get_grants,
    school_scope,
)
from app.modules.assessment import service
from app.modules.assessment.schemas import (
    PublishDecision,
    RevisionRequest,
    SaveAnswersRequest,
    SaveDraftRequest,
    SubmitRequest,
)

router = APIRouter(prefix="/api/v1/classes/{class_id}/assessments", tags=["Assessment"])
ASSESSMENT = "/{assessment_id}"
ATTEMPT = ASSESSMENT + "/attempts/{attempt_id}"


def scope(actor: Actor, class_id: UUID, assessment_id: UUID) -> tuple:
    return school_scope(actor), class_id, assessment_id


async def _with_available_sources(
    request: Request, class_id: UUID, school_id: UUID, draft: dict
) -> dict:
    grants = await get_grants(request, class_id, school_id)
    return {
        **draft,
        "available_sources": [
            {
                "document_id": str(grant.document_id),
                "document_version_id": str(grant.document_version_id),
                "source_id": grant.source_id,
                "title": grant.title or f"Tài liệu {str(grant.document_id)[:8]}",
                "version": int(grant.version_number or 1),
                "locator": "",
                "accessible": True,
            }
            for grant in (grants.grants if grants is not None else ())
        ],
    }


async def _attempt_access(
    request: Request,
    actor: Actor,
    session: DbSession,
    ids: tuple,
    attempt_id: UUID,
) -> bool:
    school_id, class_id, assessment_id = ids
    args = access_args(request, actor, class_id)
    try:
        await require_active_student(*args)
    except AppError as exc:
        if exc.status_code != 403:
            raise
        decision = await require_own_history(*args)
        if decision.class_status != "ACTIVE":
            raise
        service.find_attempt(
            session,
            school_id,
            attempt_id,
            class_id=class_id,
            activity_id=assessment_id,
            student_id=actor.user.id,
        )
        return (
            request.app.state.settings.transfer_pending_attempt_policy == "auto_submit_on_transfer"
        )
    return False


@router.post("", status_code=201)
async def create(class_id: UUID, request: Request, actor: Actor, session: DbSession) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    return service.create(session, school_scope(actor), class_id, actor.user)


@router.get(ASSESSMENT + "/draft")
async def draft(
    class_id: UUID, assessment_id: UUID, request: Request, actor: Actor, session: DbSession
) -> dict:
    decision = await require_class_teacher_read(*access_args(request, actor, class_id))
    snapshot = {
        **service.get_draft(session, scope(actor, class_id, assessment_id)),
        "can_approve": decision.teaching_role == "PRIMARY",
    }
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.put(ASSESSMENT + "/draft")
async def save_draft(
    class_id: UUID,
    assessment_id: UUID,
    body: SaveDraftRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    snapshot = service.save_draft(session, scope(actor, class_id, assessment_id), body)
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.post(ASSESSMENT + "/questions/{question_id}/review")
async def review(
    class_id: UUID,
    assessment_id: UUID,
    question_id: str,
    body: RevisionRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    snapshot = service.review(
        session, scope(actor, class_id, assessment_id), body.expected_revision, question_id
    )
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.post(ASSESSMENT + "/propose")
async def propose(
    class_id: UUID,
    assessment_id: UUID,
    body: RevisionRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    snapshot = service.propose(
        session, scope(actor, class_id, assessment_id), actor.user, body.expected_revision
    )
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.post(ASSESSMENT + "/decision")
async def decide(
    class_id: UUID,
    assessment_id: UUID,
    body: PublishDecision,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_primary_teacher(*access_args(request, actor, class_id))
    snapshot = service.decide(session, scope(actor, class_id, assessment_id), actor.user, body)
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.post(ASSESSMENT + "/versions", status_code=201)
async def version(
    class_id: UUID,
    assessment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    snapshot = service.next_version(session, scope(actor, class_id, assessment_id), actor.user)
    return await _with_available_sources(request, class_id, school_scope(actor), snapshot)


@router.get("")
async def list_assessments(
    class_id: UUID, request: Request, actor: Actor, session: DbSession
) -> dict:
    await require_active_student(*access_args(request, actor, class_id))
    return service.list_published(session, school_scope(actor), class_id, actor.user.id)


@router.get(ASSESSMENT)
async def assessment_detail(
    class_id: UUID,
    assessment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_student(*access_args(request, actor, class_id))
    return service.published_detail(session, scope(actor, class_id, assessment_id), actor.user.id)


@router.post(ASSESSMENT + "/attempts", status_code=201)
async def start(
    class_id: UUID,
    assessment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_student(*access_args(request, actor, class_id))
    return service.start(session, scope(actor, class_id, assessment_id), actor.user)


@router.get(ATTEMPT)
async def attempt(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    ids = scope(actor, class_id, assessment_id)
    transferred = await _attempt_access(request, actor, session, ids, attempt_id)
    grants = await get_grants(request, class_id, ids[0])
    return service.read_attempt(
        session, ids, attempt_id, actor.user.id, grants, transferred=transferred
    )


@router.put(ATTEMPT + "/answers")
async def save_answers(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    body: SaveAnswersRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    ids = scope(actor, class_id, assessment_id)
    transferred = await _attempt_access(request, actor, session, ids, attempt_id)
    grants = await get_grants(request, class_id, ids[0])
    return service.save_answers(
        session, ids, attempt_id, actor.user.id, body, grants, transferred=transferred
    )


@router.post(ATTEMPT + "/submit")
async def submit(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    body: SubmitRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
    idempotency_key: Annotated[UUID | None, Header()] = None,
) -> dict:
    check_operation_header(idempotency_key, body.operation_id)
    ids = scope(actor, class_id, assessment_id)
    transferred = await _attempt_access(request, actor, session, ids, attempt_id)
    return service.submit(session, ids, attempt_id, actor.user.id, body, transferred=transferred)


@router.get(ATTEMPT + "/reconcile")
async def reconcile(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
    operation_id: UUID | None = None,
) -> dict:
    ids = scope(actor, class_id, assessment_id)
    transferred = await _attempt_access(request, actor, session, ids, attempt_id)
    grants = await get_grants(request, class_id, ids[0])
    return service.read_attempt(
        session,
        ids,
        attempt_id,
        actor.user.id,
        grants,
        reconcile=True,
        transferred=transferred,
    )


@router.get(ATTEMPT + "/result")
async def result(
    class_id: UUID,
    assessment_id: UUID,
    attempt_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_own_history(*access_args(request, actor, class_id))
    ids = scope(actor, class_id, assessment_id)
    grants = await get_grants(request, class_id, ids[0])
    return service.result(
        session,
        ids,
        attempt_id,
        actor.user.id,
        grants,
        appeal_window_days=request.app.state.settings.default_appeal_window_days,
    )
