from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.core_client import CoreUser
from app.common.errors import AppError
from app.modules.notes.models import DocumentBookmark, SourceBoundNote


def owned_query(model: type[SourceBoundNote] | type[DocumentBookmark], actor: CoreUser):
    return select(model).where(model.school_id == actor.school_id, model.user_id == actor.id)


def source_query(model, actor: CoreUser, class_id: UUID, document_id: UUID):
    return owned_query(model, actor).where(
        model.class_id == class_id, model.document_id == document_id
    )


def require_owned_note(session: Session, actor: CoreUser, note_id: UUID) -> SourceBoundNote:
    note = session.scalar(
        owned_query(SourceBoundNote, actor).where(SourceBoundNote.id == note_id).with_for_update()
    )
    if note is None:
        raise AppError(404)
    return note
