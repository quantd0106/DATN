from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Request, Response, UploadFile

from app.common.access import (
    require_active_student,
    require_active_teacher,
    require_class_reader,
    require_class_teacher_read,
    require_grade_submitted_work,
    require_own_history,
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
from app.modules.assignment import service
from app.modules.assignment.schemas import (
    DueOverrideRequest,
    RecipientsRequest,
    SaveDraftRequest,
    SubmitRequest,
)
from app.modules.grading.schemas import AssignmentGradeRequest

router = APIRouter(prefix="/api/v1", tags=["Assignment"])
CLASS = "/classes/{class_id}/assignments"
ASSIGNMENT = CLASS + "/{assignment_id}"
SUBMISSION = "/submissions/{submission_id}"


def _scope(actor: Actor, class_id: UUID, assignment_id: UUID) -> tuple:
    return school_scope(actor), class_id, assignment_id


async def _reader(request: Request, actor: Actor, class_id: UUID):
    args = access_args(request, actor, class_id)
    if "LECTURER" in actor.user.roles:
        return await require_class_teacher_read(*args), None
    if "STUDENT" not in actor.user.roles:
        raise AppError(403)
    return await require_class_reader(*args), actor.user.id


@router.post(CLASS, status_code=201)
async def create(class_id: UUID, request: Request, actor: Actor, session: DbSession) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    return service.create(session, school_scope(actor), class_id, actor.user)


@router.put(ASSIGNMENT + "/draft")
async def draft(
    class_id: UUID,
    assignment_id: UUID,
    body: SaveDraftRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    return service.save_draft(session, _scope(actor, class_id, assignment_id), actor.user, body)


@router.put(ASSIGNMENT + "/recipients", status_code=204)
async def recipients(
    class_id: UUID,
    assignment_id: UUID,
    body: RecipientsRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> Response:
    await require_active_teacher(*access_args(request, actor, class_id))
    service.set_recipients(
        session,
        _scope(actor, class_id, assignment_id),
        actor.user,
        body.expected_revision,
        body.student_ids,
    )
    return Response(status_code=204)


@router.get(ASSIGNMENT + "/recipients")
async def read_recipients(
    class_id: UUID, assignment_id: UUID, request: Request, actor: Actor, session: DbSession
) -> dict:
    await require_class_teacher_read(*access_args(request, actor, class_id))
    return service.read_recipients(session, _scope(actor, class_id, assignment_id))


@router.get(ASSIGNMENT + SUBMISSION + "/grading")
async def read_grading(
    class_id: UUID,
    assignment_id: UUID,
    submission_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision = await require_grade_submitted_work(*access_args(request, actor, class_id))
    return service.read_grading(
        session, _scope(actor, class_id, assignment_id), submission_id, decision.teaching_role
    )


@router.post(ASSIGNMENT + "/due-overrides", status_code=201)
async def deadline(
    class_id: UUID,
    assignment_id: UUID,
    body: DueOverrideRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    await require_active_teacher(*access_args(request, actor, class_id))
    return service.extend_due(session, _scope(actor, class_id, assignment_id), actor.user, body)


@router.get(CLASS)
async def list_assignments(
    class_id: UUID, request: Request, actor: Actor, session: DbSession
) -> dict:
    _, student_id = await _reader(request, actor, class_id)
    school_id = school_scope(actor)
    grants = await get_grants(request, class_id, school_id) if student_id is not None else None
    return service.list_assignments(session, school_id, class_id, student_id, grants)


@router.get(ASSIGNMENT)
async def assignment(
    class_id: UUID,
    assignment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    decision, student_id = await _reader(request, actor, class_id)
    ids = _scope(actor, class_id, assignment_id)
    grants = await get_grants(request, class_id, ids[0]) if student_id is not None else None
    return service.detail(
        session, ids, student_id, grants, active=decision.class_status == "ACTIVE"
    )


@router.post(ASSIGNMENT + "/uploads", status_code=201)
async def upload(
    class_id: UUID,
    assignment_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
    file: Annotated[UploadFile, File()],
    expected_revision: Annotated[int, Form(ge=1)],
) -> dict:
    await require_active_student(*access_args(request, actor, class_id))
    max_bytes = request.app.state.settings.assignment_max_upload_bytes
    content = await file.read(max_bytes + 1)
    return service.upload(
        session,
        _scope(actor, class_id, assignment_id),
        actor.user,
        request.app.state.blob_store,
        expected=expected_revision,
        file_name=file.filename or "",
        mime_type=file.content_type or "",
        content=content,
        max_bytes=max_bytes,
    )


@router.post(ASSIGNMENT + "/submissions", status_code=201)
async def submit(
    class_id: UUID,
    assignment_id: UUID,
    body: SubmitRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
    idempotency_key: Annotated[UUID | None, Header()] = None,
) -> dict:
    await require_active_student(*access_args(request, actor, class_id))
    check_operation_header(idempotency_key, body.operation_id)
    return service.submit(
        session,
        _scope(actor, class_id, assignment_id),
        actor.user,
        body,
        request.app.state.blob_store,
        request.app.state.settings.assignment_max_upload_bytes,
    )


@router.get(ASSIGNMENT + "/submissions/reconcile")
async def reconcile(
    class_id: UUID,
    assignment_id: UUID,
    operation_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict | None:
    await require_own_history(*access_args(request, actor, class_id))
    return service.reconcile(
        session, _scope(actor, class_id, assignment_id), actor.user.id, operation_id
    )


@router.get(SUBMISSION + "/files/{file_id}")
async def download(
    submission_id: UUID,
    file_id: UUID,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> Response:
    row = service.find_submission(session, school_scope(actor), submission_id)
    if row.upload_id != file_id:
        raise AppError(404)
    args = access_args(request, actor, row.class_id)
    if row.student_id == actor.user.id:
        await require_own_history(*args)
    elif "LECTURER" in actor.user.roles:
        await require_grade_submitted_work(*args)
    else:
        raise AppError(404)
    content = request.app.state.blob_store.get(row.blob_ref)
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.file_name, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(SUBMISSION + "/grade")
async def grade(
    submission_id: UUID,
    body: AssignmentGradeRequest,
    request: Request,
    actor: Actor,
    session: DbSession,
) -> dict:
    school_id = school_scope(actor)
    row = service.find_submission(session, school_id, submission_id)
    await require_grade_submitted_work(*access_args(request, actor, row.class_id))
    return service.grade_submission(session, school_id, submission_id, actor.user, body)


@router.post(SUBMISSION + "/grade-hint")
async def grade_hint(
    submission_id: UUID, request: Request, actor: Actor, session: DbSession
) -> dict:
    row = service.find_submission(session, school_scope(actor), submission_id)
    await require_grade_submitted_work(*access_args(request, actor, row.class_id))
    settings = service.submission_version(session, row).settings_json
    hint = await request.app.state.ai.grade_hint(
        school_id=row.school_id,
        class_id=row.class_id,
        assignment_title=settings["title"],
        instructions=settings["instructions"],
        submission_note=row.note,
        correlation_id=request.state.correlation_id,
    )
    return hint.model_dump(include={"feedback", "limitations"})
