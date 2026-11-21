from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Annotated, BinaryIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.common.auth import Actor, InternalScopes, Lecturer
from app.common.core_client import Core
from app.common.db import Db
from app.common.errors import AppError, correlation_id
from app.common.pagination import Page, PageRequest
from app.common.storage import CHUNK_SIZE, Storage
from app.common.types import DocumentFormat
from app.modules.grants.public import require_live_source
from app.modules.grants.schemas import (
    ActiveGrantsResponse,
    ClassDocumentGrantDTO,
    ClassDocumentSummaryDTO,
    DocumentReaderDTO,
    GrantRequest,
)
from app.modules.grants.service import (
    active_grants,
    canonical_artifact,
    document_reader,
    grant_document,
    list_class_documents,
    revoke_grant,
)
from app.modules.versions.public import open_original, select_viewing_version

router = APIRouter(prefix="/api/v1/classes/{class_id}/documents", tags=["Class documents"])
internal_router = APIRouter(prefix="/internal/v1", tags=["Internal documents"])
VersionQuery = Annotated[int | None, Query(ge=1)]


@router.post("/grants", status_code=201, responses={200: {"model": ClassDocumentGrantDTO}})
def add_grant(
    class_id: UUID,
    payload: GrantRequest,
    session: Db,
    core: Core,
    actor: Lecturer,
    response: Response,
    request_id: Annotated[UUID, Depends(correlation_id)],
) -> ClassDocumentGrantDTO:
    grant, changed = grant_document(session, core, actor, class_id, payload.document_id, request_id)
    if not changed:
        response.status_code = 200
    return grant


@router.delete("/grants/{document_id}", status_code=204)
def remove_grant(
    class_id: UUID,
    document_id: UUID,
    session: Db,
    core: Core,
    actor: Lecturer,
    request_id: Annotated[UUID, Depends(correlation_id)],
) -> None:
    revoke_grant(session, core, actor, class_id, document_id, request_id)


@router.get("")
def get_class_documents(
    class_id: UUID,
    session: Db,
    core: Core,
    actor: Actor,
    pagination: PageRequest,
    file_format: Annotated[DocumentFormat | None, Query(alias="format")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[ClassDocumentSummaryDTO]:
    return list_class_documents(session, core, actor, class_id, pagination, file_format, search)


@router.get("/{document_id}/reader")
def reader(
    class_id: UUID,
    document_id: UUID,
    session: Db,
    core: Core,
    storage: Storage,
    actor: Actor,
    version: VersionQuery = None,
) -> DocumentReaderDTO:
    return document_reader(session, core, storage, actor, class_id, document_id, version)


def stream_chunks(stream: BinaryIO) -> Iterator[bytes]:
    with stream:
        while chunk := stream.read(CHUNK_SIZE):
            yield chunk


@router.get("/{document_id}/download", response_class=StreamingResponse)
def download(
    class_id: UUID,
    document_id: UUID,
    session: Db,
    core: Core,
    storage: Storage,
    actor: Actor,
    version: VersionQuery = None,
) -> StreamingResponse:
    serving = require_live_source(session, core, actor, class_id, document_id)
    viewing = select_viewing_version(session, document_id, serving, version)
    filename = viewing.original_filename
    # The fallback is ASCII and the RFC 5987 value contains no raw header metacharacters.
    suffix = PurePosixPath(filename).suffix.lower()
    safe_suffix = suffix if suffix in {".pdf", ".pptx", ".docx", ".txt", ".md", ".markdown"} else ""
    disposition = (
        f'attachment; filename="document{safe_suffix}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    stream = open_original(storage, viewing)
    return StreamingResponse(
        stream_chunks(stream),
        media_type=viewing.mime_type,
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(viewing.file_size_bytes),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@internal_router.get("/classes/{class_id}/documents/active-grants")
def internal_active_grants(
    class_id: UUID, session: Db, scopes: InternalScopes, school_id: UUID | None = None
) -> ActiveGrantsResponse:
    if "document:read_grants" not in scopes:
        raise AppError(403)
    return active_grants(session, class_id, school_id)


@internal_router.get("/documents/{document_id}/versions/{version_id}/canonical")
def internal_canonical(
    document_id: UUID, version_id: UUID, session: Db, storage: Storage, scopes: InternalScopes
) -> Response:
    if "document:read_canonical" not in scopes:
        raise AppError(403)
    return Response(
        canonical_artifact(session, storage, document_id, version_id), media_type="application/json"
    )
