from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from app.common.auth import Lecturer
from app.common.config import Config
from app.common.db import Db
from app.common.errors import correlation_id
from app.common.storage import Storage
from app.modules.versions.schemas import DocumentVersionDTO, VersionActionRequest
from app.modules.versions.service import accept_version, retry_version, upload_version

router = APIRouter(prefix="/api/v1/documents/{document_id}/versions", tags=["Versions"])


@router.post("", status_code=201)
def create_version(
    document_id: UUID,
    actor: Lecturer,
    session: Db,
    storage: Storage,
    config: Config,
    request_id: Annotated[UUID, Depends(correlation_id)],
    file: Annotated[UploadFile, File()],
) -> DocumentVersionDTO:
    return upload_version(session, storage, config, actor, document_id, file, request_id)


@router.post("/{version_id}/accept")
def accept(
    document_id: UUID,
    version_id: UUID,
    actor: Lecturer,
    session: Db,
    storage: Storage,
    request_id: Annotated[UUID, Depends(correlation_id)],
    _body: VersionActionRequest | None = None,
) -> DocumentVersionDTO:
    return accept_version(session, storage, actor, document_id, version_id, request_id)


@router.post("/{version_id}/retry")
def retry(
    document_id: UUID,
    version_id: UUID,
    actor: Lecturer,
    session: Db,
    request_id: Annotated[UUID, Depends(correlation_id)],
    _body: VersionActionRequest | None = None,
) -> DocumentVersionDTO:
    return retry_version(session, actor, document_id, version_id, request_id)
