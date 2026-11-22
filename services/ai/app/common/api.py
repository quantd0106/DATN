from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.common.auth import bearer, get_current_user
from app.common.db import Database
from app.common.errors import AppError, ErrorEnvelope
from app.integrations.core_client import CoreClient, CurrentUser
from app.integrations.document_client import DocumentPort
from app.integrations.learning_client import LearningPort
from app.runtime.rag_runtime import RagRuntime

Actor = Annotated[CurrentUser, Depends(get_current_user)]
Credentials = Annotated[HTTPAuthorizationCredentials, Depends(bearer)]
ERROR_RESPONSES = {code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422, 503)}


@dataclass(frozen=True)
class Services:
    database: Database | None
    core: CoreClient
    document: DocumentPort
    learning: LearningPort
    runtime: RagRuntime
    correlation_id: str

    def require_db(self) -> Database:
        if self.database is None:
            raise AppError(503)
        return self.database


def services(request: Request) -> Services:
    state = request.app.state
    return Services(
        state.database,
        state.core,
        state.document,
        state.learning,
        state.runtime,
        request.state.correlation_id,
    )


ApiServices = Annotated[Services, Depends(services)]
