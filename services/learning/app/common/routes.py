from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.common.auth import AuthenticatedActor, get_authenticated_actor
from app.common.db import get_db
from app.common.errors import AppError
from app.integrations.document_client import GrantSnapshot, optional_grants

Actor = Annotated[AuthenticatedActor, Depends(get_authenticated_actor)]
DbSession = Annotated[Session, Depends(get_db)]


def school_scope(actor: AuthenticatedActor) -> UUID:
    if actor.user.school_id is None:
        raise AppError(404)
    return actor.user.school_id


def access_args(request: Request, actor: AuthenticatedActor, class_id: UUID) -> tuple:
    school_scope(actor)
    return request.app.state.core, actor.user, class_id, request.state.correlation_id


async def get_grants(request: Request, class_id: UUID, school_id: UUID) -> GrantSnapshot | None:
    return await optional_grants(
        request.app.state.document,
        class_id,
        school_id,
        request.state.correlation_id,
    )


def check_operation_header(header: UUID | None, operation_id: UUID) -> None:
    if header is not None and header != operation_id:
        raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
