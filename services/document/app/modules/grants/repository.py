from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.grants.models import ClassDocumentGrant
from app.modules.versions.public import serving_file_type, serving_version_id


def grant_for_document(
    session: Session,
    *,
    school_id: UUID,
    class_id: UUID,
    document_id: UUID,
    active_only: bool = True,
) -> ClassDocumentGrant | None:
    query = select(ClassDocumentGrant).where(
        ClassDocumentGrant.class_id == class_id,
        ClassDocumentGrant.document_id == document_id,
        ClassDocumentGrant.school_id == school_id,
    )
    if active_only:
        query = query.where(ClassDocumentGrant.is_active.is_(True))
    return session.scalar(
        query.order_by(ClassDocumentGrant.granted_at.desc(), ClassDocumentGrant.id.desc()).limit(1)
    )


def grants_for_document(
    session: Session, document_id: UUID, school_id: UUID, *, active_only: bool = False
) -> list[ClassDocumentGrant]:
    query = select(ClassDocumentGrant).where(
        ClassDocumentGrant.document_id == document_id, ClassDocumentGrant.school_id == school_id
    )
    if active_only:
        query = query.where(ClassDocumentGrant.is_active.is_(True))
    return list(session.scalars(query.order_by(ClassDocumentGrant.class_id, ClassDocumentGrant.id)))


def active_document_keys(class_id: UUID, school_id: UUID | None, file_format: str | None = None):
    query = select(ClassDocumentGrant.document_id, ClassDocumentGrant.school_id).where(
        ClassDocumentGrant.class_id == class_id,
        ClassDocumentGrant.is_active.is_(True),
        serving_version_id(ClassDocumentGrant.document_id).is_not(None),
    )
    if school_id is not None:
        query = query.where(ClassDocumentGrant.school_id == school_id)
    if file_format is not None:
        query = query.where(serving_file_type(ClassDocumentGrant.document_id) == file_format)
    return query
