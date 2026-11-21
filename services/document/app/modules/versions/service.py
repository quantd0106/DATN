import json
from datetime import datetime
from typing import BinaryIO
from uuid import UUID, uuid4

from fastapi import UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.common.audit import AuditMetadata, record_audit
from app.common.config import Settings
from app.common.core_client import CoreUser
from app.common.db import transaction, utc_now
from app.common.errors import AppError
from app.common.outbox import record_outbox_event
from app.common.storage import StorageBackend, staged_upload
from app.modules.library.public import ValidatedFile, require_owned_document, validate_upload
from app.modules.versions.models import DocumentVersion
from app.modules.versions.repository import (
    get_serving_version,
    get_version_by_id,
    get_version_by_number,
    list_versions,
    next_version_number,
)
from app.modules.versions.schemas import DocumentVersionDTO, ReaderProjection


def version_dto(version: DocumentVersion, serving: DocumentVersion | None) -> DocumentVersionDTO:
    dto = DocumentVersionDTO.model_validate(version)
    if serving is not None and version.processing_status == "SUCCESS":
        dto.is_serving = version.version_number == serving.version_number
        dto.is_superseded = version.version_number < serving.version_number
    return dto


def list_versions_with_derived_state(
    session: Session, document_id: UUID
) -> list[DocumentVersionDTO]:
    versions = list_versions(session, document_id)
    serving = next(
        (version for version in versions if version.processing_status == "SUCCESS"), None
    )
    return [version_dto(version, serving) for version in versions]


def select_viewing_version(
    session: Session, document_id: UUID, serving: DocumentVersion, version_number: int | None
) -> DocumentVersion:
    if version_number is None or version_number == serving.version_number:
        return serving
    version = get_version_by_number(session, document_id, version_number)
    if version is None or version.processing_status != "SUCCESS":
        raise AppError(404)
    return version


def source_storage_key(document_id: UUID, version_id: UUID) -> str:
    return f"source/{document_id}/{version_id}"


def create_pending_version(
    session: Session,
    *,
    document_id: UUID,
    version_id: UUID,
    version_number: int,
    file: ValidatedFile,
    storage_key: str,
    source_sha256: str,
) -> DocumentVersion:
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=version_number,
        original_filename=file.filename,
        file_type=file.file_type,
        mime_type=file.mime_type,
        file_size_bytes=file.size_bytes,
        source_sha256=source_sha256,
        storage_key=storage_key,
        page_count=file.page_count,
        processing_status="PENDING",
    )
    session.add(version)
    session.flush()
    return version


def upload_version(
    session: Session,
    storage: StorageBackend,
    config: Settings,
    actor: CoreUser,
    document_id: UUID,
    upload: UploadFile,
    correlation_id: UUID,
) -> DocumentVersionDTO:
    require_owned_document(session, document_id, actor)
    file = validate_upload(upload, config)
    version_id = uuid4()
    key = source_storage_key(document_id, version_id)
    with (
        staged_upload(storage, key, upload.file, expected_sha256=file.sha256) as checksum,
        transaction(session),
    ):
        document = require_owned_document(session, document_id, actor, lock=True)
        version = create_pending_version(
            session,
            document_id=document_id,
            version_id=version_id,
            version_number=next_version_number(session, document_id),
            file=file,
            storage_key=key,
            source_sha256=checksum,
        )
        document.format = file.file_type
        document.updated_at = utc_now()
        record_audit(
            session,
            school_id=document.school_id,
            actor_id=actor.id,
            action="DOCUMENT_VERSION_UPLOADED",
            target_type="document_version",
            target_id=version.id,
            correlation_id=correlation_id,
            metadata=AuditMetadata(version_number=version.version_number),
        )
        return version_dto(version, get_serving_version(session, document_id))


def read_canonical(storage: StorageBackend, version: DocumentVersion) -> tuple[bytes, dict]:
    key, digest = version.canonical_storage_key, version.canonical_sha256
    schema_version = version.canonical_schema_version
    if not key or not digest or not schema_version:
        raise AppError(409, "Phiên bản chưa có dữ liệu đọc.")
    try:
        raw = storage.read_bytes(key, expected_sha256=digest)
        artifact = json.loads(raw)
        if not isinstance(artifact, dict) or artifact.get("schema_version") != schema_version:
            raise ValueError
        return raw, artifact
    except FileNotFoundError as exc:
        raise AppError(409, "Phiên bản chưa có dữ liệu đọc.") from exc
    except (OSError, ValueError, RecursionError) as exc:
        raise AppError(503) from exc


def record_version_published(
    session: Session, version: DocumentVersion, school_id: UUID, published_at: datetime
) -> None:
    record_outbox_event(
        session,
        "document.version.published",
        version.document_id,
        version.version_number,
        {
            "school_id": school_id,
            "document_id": version.document_id,
            "version_id": version.id,
            "version_number": version.version_number,
            "canonical_schema_version": version.canonical_schema_version,
            "canonical_sha256": version.canonical_sha256,
            "acceptance_mode": version.acceptance_mode,
            "published_at": published_at,
        },
    )


def accept_version(
    session: Session,
    storage: StorageBackend,
    actor: CoreUser,
    document_id: UUID,
    version_id: UUID,
    correlation_id: UUID,
) -> DocumentVersionDTO:
    with transaction(session):
        document = require_owned_document(session, document_id, actor, lock=True)
        version = get_version_by_id(session, document_id, version_id, lock=True)
        if version is None:
            raise AppError(404)
        if version.processing_status != "NEEDS_REVIEW":
            raise AppError(409, "Chỉ có thể chấp nhận phiên bản đang ở trạng thái cần rà soát.")
        if not load_reader_projection(storage, version).pages:
            raise AppError(409, "Phiên bản chưa có dữ liệu đọc.")
        now = utc_now()
        version.processing_status = "SUCCESS"
        version.acceptance_mode = "OWNER_ACCEPTED"
        version.accepted_by_user_id = actor.id
        version.accepted_at = version.updated_at = document.updated_at = now
        record_audit(
            session,
            school_id=document.school_id,
            actor_id=actor.id,
            action="DOCUMENT_VERSION_ACCEPTED",
            target_type="document_version",
            target_id=version.id,
            correlation_id=correlation_id,
            metadata=AuditMetadata(version_number=version.version_number),
        )
        record_version_published(session, version, document.school_id, now)
    return version_dto(version, get_serving_version(session, document_id))


def retry_version(
    session: Session, actor: CoreUser, document_id: UUID, version_id: UUID, correlation_id: UUID
) -> DocumentVersionDTO:
    with transaction(session):
        document = require_owned_document(session, document_id, actor, lock=True)
        version = get_version_by_id(session, document_id, version_id, lock=True)
        if version is None:
            raise AppError(404)
        if version.processing_status != "FAILED":
            raise AppError(409, "Chỉ có thể thử lại phiên bản xử lý thất bại.")
        version.processing_status = "PENDING"
        version.available_at = version.updated_at = utc_now()
        version.error_code = version.error_message = version.review_summary_json = None
        record_audit(
            session,
            school_id=document.school_id,
            actor_id=actor.id,
            action="DOCUMENT_VERSION_RETRIED",
            target_type="document_version",
            target_id=version.id,
            correlation_id=correlation_id,
            metadata=AuditMetadata(version_number=version.version_number),
        )
    return version_dto(version, get_serving_version(session, document_id))


def load_canonical_artifact(storage: StorageBackend, version: DocumentVersion) -> bytes:
    raw, _ = read_canonical(storage, version)
    return raw


def load_reader_projection(storage: StorageBackend, version: DocumentVersion) -> ReaderProjection:
    _, artifact = read_canonical(storage, version)
    try:
        return project_canonical(artifact, version.file_type)
    except (KeyError, TypeError, AttributeError, ValidationError, ValueError) as exc:
        raise AppError(503) from exc


def canonical_page_number(page: dict) -> int:
    number = page.get("page_number")
    if number is None:
        number = page.get("page_id")
    if type(number) is not int:
        raise TypeError
    if number < 1:
        raise ValueError
    return number


def project_canonical(artifact: dict, file_type: str) -> ReaderProjection:
    if "reader_pages" in artifact:
        return ReaderProjection(pages=artifact["reader_pages"], outline=artifact.get("outline", []))

    pages = []
    outline = []
    for page in artifact["pages"]:
        page_number = canonical_page_number(page)
        label = f"Trang chiếu {page_number}" if file_type == "PPTX" else f"Trang {page_number}"
        paragraphs = []
        formulas = []
        for block in page["blocks"]:
            if block.get("excluded"):
                continue
            text = (block.get("text") or block.get("markdown") or "").strip()
            if text:
                paragraphs.append(text)
                if block.get("type") in {"Heading", "SectionHeader", "Title"}:
                    outline.append(
                        {
                            "id": block.get("block_id") or f"{page_number}-{len(outline) + 1}",
                            "label": text,
                            "page_number": page_number,
                        }
                    )
            formulas.extend(block.get("latex") or [])
        pages.append(
            {
                "page_number": page_number,
                "title": page.get("title") or label,
                "paragraphs": paragraphs,
                "formulas": formulas,
            }
        )
    return ReaderProjection(pages=pages, outline=artifact.get("outline", outline))


def open_original(storage: StorageBackend, version: DocumentVersion) -> BinaryIO:
    try:
        return storage.open_stream(version.storage_key)
    except (OSError, ValueError) as exc:
        raise AppError(503) from exc
