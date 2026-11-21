from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.core_client import CoreUser
from app.common.errors import AppError
from app.modules.library.public import find_document
from app.modules.notes.models import DocumentBookmark, SourceBoundNote
from app.modules.notes.repository import source_query
from app.modules.notes.schemas import PersonalNoteDTO
from app.modules.versions.public import get_version_by_number


def source_reference(
    session: Session, record: SourceBoundNote | DocumentBookmark, accessible: bool
) -> dict:
    document = find_document(session, record.document_id, record.school_id, include_deleted=True)
    if document is None:
        raise AppError(404)
    version = get_version_by_number(session, record.document_id, record.version_number)
    return {
        "id": record.id,
        "school_id": record.school_id,
        "user_id": record.user_id,
        "class_id": record.class_id,
        "document_id": record.document_id,
        "document_title": document.title if accessible else "Tài liệu",
        "document_format": version.file_type if version is not None else document.format,
        "document_version_id": record.document_version_id,
        "version_number": record.version_number,
        "locator": record.locator,
        "page_number": record.page_number,
        "created_at": record.created_at,
        "is_source_accessible": accessible,
    }


def note_dto(session: Session, note: SourceBoundNote, accessible: bool) -> PersonalNoteDTO:
    return PersonalNoteDTO(
        **source_reference(session, note, accessible),
        note_text=note.note_text,
        source_quote=note.source_quote if accessible else None,
        updated_at=note.updated_at,
    )


def reader_notes(
    session: Session,
    actor: CoreUser,
    class_id: UUID,
    document_id: UUID,
    *,
    accessible: bool,
) -> list[PersonalNoteDTO]:
    query = source_query(SourceBoundNote, actor, class_id, document_id)
    return [
        note_dto(session, note, accessible)
        for note in session.scalars(
            query.order_by(SourceBoundNote.updated_at.desc(), SourceBoundNote.id)
        )
    ]


def bookmark_version(
    session: Session, actor: CoreUser, class_id: UUID, document_id: UUID
) -> int | None:
    query = source_query(DocumentBookmark, actor, class_id, document_id)
    versions = query.with_only_columns(DocumentBookmark.version_number).subquery()
    return session.scalar(select(func.max(versions.c.version_number)))


__all__ = ["PersonalNoteDTO", "bookmark_version", "reader_notes"]
