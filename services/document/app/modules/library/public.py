from uuid import UUID

from sqlalchemy import Select, tuple_
from sqlalchemy.orm import Session

from app.common.core_client import CoreUser
from app.common.errors import AppError
from app.common.pagination import Pagination, paginate
from app.modules.library.models import Document
from app.modules.library.repository import document_query, filter_metadata, live_documents
from app.modules.library.validation import ValidatedFile, validate_upload


def find_document(
    session: Session,
    document_id: UUID,
    school_id: UUID | None,
    *,
    include_deleted: bool = False,
    lock: bool = False,
) -> Document | None:
    if school_id is None:
        return None
    return document_query(
        session, document_id, school_id, include_deleted=include_deleted, lock=lock
    )


def require_owned_document(
    session: Session, document_id: UUID, actor: CoreUser, *, lock: bool = False
) -> Document:
    if actor.school_id is None:
        raise AppError(404)
    document = document_query(
        session, document_id, actor.school_id, owner_user_id=actor.id, lock=lock
    )
    if document is None:
        raise AppError(404)
    return document


def live_document_ids() -> Select[tuple[UUID]]:
    return live_documents().with_only_columns(Document.id)


def get_live_document(
    session: Session, document_id: UUID, *, lock: bool = False
) -> Document | None:
    query = live_documents().where(Document.id == document_id)
    return session.scalar(query.with_for_update() if lock else query)


def is_document_live(session: Session, document_id: UUID) -> bool:
    return session.scalar(live_document_ids().where(Document.id == document_id)) is not None


def shared_documents(
    session: Session,
    granted_keys,
    *,
    school_id: UUID | None = None,
    search: str | None = None,
    pagination: Pagination | None = None,
) -> tuple[list[Document], str | None]:
    query = live_documents(school_id).where(
        tuple_(Document.id, Document.school_id).in_(granted_keys)
    )
    query = filter_metadata(query, Document.format, None, search)
    if pagination is not None:
        return paginate(session, query, Document.id, pagination)
    return list(session.scalars(query.order_by(Document.id))), None


__all__ = [
    "ValidatedFile",
    "find_document",
    "get_live_document",
    "is_document_live",
    "live_document_ids",
    "require_owned_document",
    "shared_documents",
    "validate_upload",
]
