from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from app.common.auth import verify_analytics_service_key, verify_internal_service_key
from app.common.pagination import Pagination, get_pagination
from app.common.routes import DbSession, check_operation_header
from app.modules.assessment import service as assessment
from app.modules.bank import service as bank
from app.modules.grading import service as grading
from app.modules.internal.schemas import AssessmentLock, QuizImportReceipt, QuizImportRequest

router = APIRouter(prefix="/internal/v1", tags=["Internal"])


@router.get("/assessment-lock", dependencies=[Depends(verify_internal_service_key)])
def assessment_lock(user_id: UUID, school_id: UUID, session: DbSession) -> AssessmentLock:
    return AssessmentLock(
        user_id=user_id,
        school_id=school_id,
        active_official_assessment=assessment.active_lock(session, user_id, school_id),
    )


@router.post(
    "/quiz-items/import",
    status_code=201,
    responses={200: {"model": QuizImportReceipt}},
    dependencies=[Depends(verify_internal_service_key)],
)
def quiz_import(
    body: QuizImportRequest,
    response: Response,
    session: DbSession,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
) -> QuizImportReceipt:
    check_operation_header(idempotency_key, body.idempotency_key)
    item_id, created = bank.import_quiz_item(
        session,
        body.class_id,
        body.school_id,
        idempotency_key,
        body.approved_by,
        body.model_dump(mode="json"),
    )
    if not created:
        response.status_code = 200
    return QuizImportReceipt(
        class_id=body.class_id,
        school_id=body.school_id,
        idempotency_key=idempotency_key,
        learning_item_id=item_id,
    )


@router.get(
    "/classes/{class_id}/grades/confirmed", dependencies=[Depends(verify_analytics_service_key)]
)
def confirmed_grades(
    class_id: UUID,
    school_id: UUID,
    session: DbSession,
    pagination: Annotated[Pagination, Depends(get_pagination)],
    since_version: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return grading.confirmed_grades(session, school_id, class_id, since_version, pagination)
