from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.modules.library.models import Document


def live_documents(school_id: UUID | None = None) -> Select[tuple[Document]]:
    query = select(Document).where(Document.deleted_at.is_(None))
    return query if school_id is None else query.where(Document.school_id == school_id)


def document_query(
    session: Session,
    document_id: UUID,
    school_id: UUID,
    *,
    owner_user_id: UUID | None = None,
    include_deleted: bool = False,
    lock: bool = False,
) -> Document | None:
    query = select(Document) if include_deleted else live_documents()
    query = query.where(Document.id == document_id, Document.school_id == school_id)
    if owner_user_id is not None:
        query = query.where(Document.owner_user_id == owner_user_id)
    return session.scalar(query.with_for_update() if lock else query)


def filter_metadata(query, format_column, file_format: str | None, search: str | None):
    if file_format:
        query = query.where(format_column == file_format)
    if search := (search or "").strip():
        query = query.where(
            Document.title.icontains(search, autoescape=True)
            | Document.description.icontains(search, autoescape=True)
        )
    return query
