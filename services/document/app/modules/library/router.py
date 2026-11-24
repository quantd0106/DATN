from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.common.auth import Actor, Lecturer
from app.common.config import Config
from app.common.db import Db
from app.common.errors import correlation_id
from app.common.pagination import Page, PageRequest
from app.common.storage import Storage, safe_content_disposition, stream_chunks
from app.common.types import DocumentFormat, ProcessingStatus
from app.modules.grants.public import DocumentReaderDTO
from app.modules.library.public import require_owned_document
from app.modules.library.schemas import (
    Description,
    DocumentDetailResponse,
    DocumentPatch,
    DocumentPolicyResponse,
    DocumentSummaryDTO,
    Title,
)
from app.modules.library.service import (
    create_document,
    delete_document,
    document_detail_for,
    library_document_download,
    library_document_reader,
    list_library,
    update_document,
    upload_policy,
)

router = APIRouter(prefix="/api/v1/documents", tags=["Library"])


@router.get("/policy")
def policy(actor: Actor, config: Config) -> DocumentPolicyResponse:
    return upload_policy(config)


@router.get("")
def documents(
    session: Db,
    actor: Lecturer,
    pagination: PageRequest,
    status: ProcessingStatus | Literal["READY"] | None = None,
    file_format: Annotated[DocumentFormat | None, Query(alias="format")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[DocumentSummaryDTO]:
    return list_library(session, actor, pagination, status, file_format, search)


@router.post("", status_code=201)
def upload_document(
    actor: Lecturer,
    session: Db,
    storage: Storage,
    config: Config,
    request_id: Annotated[UUID, Depends(correlation_id)],
    file: Annotated[UploadFile, File()],
    title: Annotated[Title, Form()],
    description: Annotated[Description | None, Form()] = None,
) -> DocumentSummaryDTO:
    return create_document(session, config, storage, actor, file, title, description, request_id)


@router.get("/{document_id}")
def detail(document_id: UUID, session: Db, actor: Lecturer) -> DocumentDetailResponse:
    return document_detail_for(session, require_owned_document(session, document_id, actor))


@router.patch("/{document_id}")
def patch_document(
    document_id: UUID,
    patch: DocumentPatch,
    session: Db,
    actor: Lecturer,
    request_id: Annotated[UUID, Depends(correlation_id)],
) -> DocumentSummaryDTO:
    return update_document(session, actor, document_id, patch, request_id)


@router.delete("/{document_id}", status_code=204)
def remove_document(
    document_id: UUID,
    session: Db,
    actor: Lecturer,
    request_id: Annotated[UUID, Depends(correlation_id)],
    confirm_multi_class: bool = False,
) -> None:
    delete_document(session, actor, document_id, confirm_multi_class, request_id)


@router.get("/{document_id}/reader")
def library_reader(
    document_id: UUID,
    session: Db,
    storage: Storage,
    actor: Lecturer,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> DocumentReaderDTO:
    return library_document_reader(session, storage, actor, document_id, version)


@router.get("/{document_id}/download", response_class=StreamingResponse)
def library_download(
    document_id: UUID,
    session: Db,
    storage: Storage,
    actor: Lecturer,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> StreamingResponse:
    viewing, stream = library_document_download(session, storage, actor, document_id, version)
    return StreamingResponse(
        stream_chunks(stream),
        media_type=viewing.mime_type,
        headers={
            "Content-Disposition": safe_content_disposition(viewing.original_filename),
            "Content-Length": str(viewing.file_size_bytes),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
