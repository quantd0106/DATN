from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.library.public import live_document_ids
from app.modules.versions.models import DocumentVersion


def latest_version_field(document_id, field, *, successful=False):
    query = select(field).where(DocumentVersion.document_id == document_id)
    if successful:
        query = query.where(DocumentVersion.processing_status == "SUCCESS")
    return query.order_by(DocumentVersion.version_number.desc()).limit(1).scalar_subquery()


def latest_status(document_id):
    return latest_version_field(document_id, DocumentVersion.processing_status)


def serving_version_id(document_id):
    return latest_version_field(document_id, DocumentVersion.id, successful=True)


def serving_file_type(document_id):
    return latest_version_field(document_id, DocumentVersion.file_type, successful=True)


def get_serving_version(session: Session, document_id: UUID) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.processing_status == "SUCCESS",
        )
        .order_by(DocumentVersion.version_number.desc())
        .limit(1)
    )


def get_version_by_number(
    session: Session, document_id: UUID, version_number: int
) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )


def get_version_by_id(
    session: Session, document_id: UUID, version_id: UUID, *, lock: bool = False
) -> DocumentVersion | None:
    query = select(DocumentVersion).where(
        DocumentVersion.id == version_id, DocumentVersion.document_id == document_id
    )
    return session.scalar(query.with_for_update() if lock else query)


def expired_versions(session: Session, now: datetime) -> list[DocumentVersion]:
    query = (
        select(DocumentVersion)
        .where(
            DocumentVersion.processing_status == "PROCESSING",
            DocumentVersion.available_at <= now,
        )
        .with_for_update(skip_locked=True)
    )
    return list(session.scalars(query))


def next_available_version(session: Session, now: datetime) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.processing_status == "PENDING",
            DocumentVersion.available_at <= now,
            DocumentVersion.document_id.in_(live_document_ids()),
        )
        .order_by(DocumentVersion.created_at, DocumentVersion.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def list_versions(session: Session, document_id: UUID) -> list[DocumentVersion]:
    return list(
        session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_number.desc())
        )
    )


def next_version_number(session: Session, document_id: UUID) -> int:
    maximum = session.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    return (maximum or 0) + 1
