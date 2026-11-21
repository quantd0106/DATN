from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.common.audit import AuditMetadata, record_audit
from app.common.config import Settings
from app.common.core_client import CoreUser
from app.common.db import transaction, utc_now
from app.common.errors import AppError
from app.common.outbox import record_outbox_event
from app.common.pagination import Page, Pagination, paginate
from app.common.storage import StorageBackend, staged_upload
from app.modules.grants.public import (
    ClassDocumentGrantDTO,
    deactivate_document_grants,
    list_document_grants,
)
from app.modules.library.models import Document
from app.modules.library.public import require_owned_document
from app.modules.library.repository import filter_metadata, live_documents
from app.modules.library.schemas import (
    DocumentDetailResponse,
    DocumentPatch,
    DocumentPolicyResponse,
    DocumentSummaryDTO,
)
from app.modules.library.validation import EXTENSIONS, FORMATS, validate_upload
from app.modules.versions.public import (
    DocumentVersionDTO,
    create_pending_version,
    latest_status,
    list_versions_with_derived_state,
    source_storage_key,
    version_dto,
)


def upload_policy(config: Settings) -> DocumentPolicyResponse:
    return DocumentPolicyResponse(
        max_size_bytes=config.document_max_size_bytes,
        max_pages=config.document_max_pages,
        allowed_formats=list(FORMATS),
        allowed_extensions=list(EXTENSIONS),
        allowed_mime_types=[mime_type for _, mime_type in FORMATS.values()],
    )


def summarize(
    document: Document, versions: list[DocumentVersionDTO], grants: list[ClassDocumentGrantDTO]
) -> DocumentSummaryDTO:
    latest = versions[0]
    return DocumentSummaryDTO(
        id=document.id,
        school_id=document.school_id,
        owner_user_id=document.owner_user_id,
        owner_display_name=document.owner_display_name,
        title=document.title,
        description=document.description,
        format=document.format,
        created_at=document.created_at,
        updated_at=document.updated_at,
        latest_version=latest,
        latest_version_number=latest.version_number,
        serving_version=next((version for version in versions if version.is_serving), None),
        processing_status=latest.processing_status,
        page_count=latest.page_count,
        error_message=latest.error_message,
        active_grant_count=sum(grant.is_active for grant in grants),
    )


def document_detail_for(session: Session, document: Document) -> DocumentDetailResponse:
    versions = list_versions_with_derived_state(session, document.id)
    serving_number = next(
        (version.version_number for version in versions if version.is_serving), None
    )
    grants = list_document_grants(session, document.id, document.school_id, serving_number)
    return DocumentDetailResponse(
        document=summarize(document, versions, grants), versions=versions, grants=grants
    )


def list_library(
    session: Session,
    actor: CoreUser,
    pagination: Pagination,
    status: str | None,
    file_format: str | None,
    search: str | None,
) -> Page[DocumentSummaryDTO]:
    query = live_documents(actor.school_id).where(Document.owner_user_id == actor.id)
    query = filter_metadata(query, Document.format, file_format, search)
    if status:
        processing_status = "SUCCESS" if status == "READY" else status
        query = query.where(latest_status(Document.id) == processing_status)
    documents, cursor = paginate(session, query, Document.id, pagination)
    return Page[DocumentSummaryDTO](
        items=[document_detail_for(session, document).document for document in documents],
        next_cursor=cursor,
    )


def create_document(
    session: Session,
    config: Settings,
    storage: StorageBackend,
    actor: CoreUser,
    upload: UploadFile,
    title: str,
    description: str | None,
    correlation_id: UUID,
) -> DocumentSummaryDTO:
    file = validate_upload(upload, config)
    document_id, version_id = uuid4(), uuid4()
    key = source_storage_key(document_id, version_id)
    with (
        staged_upload(storage, key, upload.file, expected_sha256=file.sha256) as checksum,
        transaction(session),
    ):
        document = Document(
            id=document_id,
            school_id=actor.school_id,
            owner_user_id=actor.id,
            owner_display_name=actor.display_name,
            title=title,
            description=description,
            format=file.file_type,
        )
        session.add(document)
        session.flush()
        version = create_pending_version(
            session,
            document_id=document.id,
            version_id=version_id,
            version_number=1,
            file=file,
            storage_key=key,
            source_sha256=checksum,
        )
        record_audit(
            session,
            school_id=document.school_id,
            actor_id=actor.id,
            action="DOCUMENT_CREATED",
            target_type="document",
            target_id=document.id,
            correlation_id=correlation_id,
            metadata=AuditMetadata(version_number=1),
        )
        return summarize(document, [version_dto(version, None)], [])


def update_document(
    session: Session, actor: CoreUser, document_id: UUID, patch: DocumentPatch, correlation_id: UUID
) -> DocumentSummaryDTO:
    with transaction(session):
        document = require_owned_document(session, document_id, actor, lock=True)
        changes = patch.model_dump(exclude_unset=True)
        if changes:
            for field, value in changes.items():
                setattr(document, field, value)
            document.updated_at = utc_now()
            record_audit(
                session,
                school_id=document.school_id,
                actor_id=actor.id,
                action="DOCUMENT_UPDATED",
                target_type="document",
                target_id=document.id,
                correlation_id=correlation_id,
            )
        return document_detail_for(session, document).document


def delete_document(
    session: Session, actor: CoreUser, document_id: UUID, confirm: bool, correlation_id: UUID
) -> None:
    with transaction(session):
        document = require_owned_document(session, document_id, actor, lock=True)
        now = utc_now()
        # Grant mutations take the same document lock, so impact and revocation are atomic.
        affected = deactivate_document_grants(
            session, document.id, document.school_id, actor.id, now
        )
        if affected and not confirm:
            raise AppError(
                409,
                "Tài liệu đang được chia sẻ. Vui lòng xác nhận thu hồi khỏi các lớp bị ảnh hưởng.",
                details={"active_grant_count": len(affected), "affected_class_ids": affected},
            )
        document.deleted_at = document.updated_at = now
        record_audit(
            session,
            school_id=document.school_id,
            actor_id=actor.id,
            action="DOCUMENT_DELETED",
            target_type="document",
            target_id=document.id,
            correlation_id=correlation_id,
            metadata=AuditMetadata(active_grant_count=len(affected)),
        )
        latest = list_versions_with_derived_state(session, document.id)[0]
        record_outbox_event(
            session,
            "document.deleted",
            document.id,
            latest.version_number,
            {
                "school_id": document.school_id,
                "document_id": document.id,
                "revoked_class_ids": affected,
                "deleted_at": now,
            },
        )
