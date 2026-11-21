from uuid import UUID

from sqlalchemy.orm import Session

from app.common.core_client import CoreClient, CoreUser
from app.common.db import transaction, utc_now
from app.common.pagination import Page, Pagination, paginate
from app.modules.grants.public import is_source_accessible, require_live_source
from app.modules.notes.models import DocumentBookmark, SourceBoundNote
from app.modules.notes.public import note_dto, source_reference
from app.modules.notes.repository import owned_query, require_owned_note, source_query
from app.modules.notes.schemas import (
    BookmarkDTO,
    BookmarkToggle,
    BookmarkToggleResponse,
    NoteCreate,
    NotePatch,
    PersonalNoteDTO,
)
from app.modules.versions.public import select_viewing_version


def personal_records(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    model: type[SourceBoundNote] | type[DocumentBookmark],
    pagination: Pagination,
    class_id: UUID | None,
    document_id: UUID | None = None,
):
    query = owned_query(model, actor)
    if class_id is not None:
        query = query.where(model.class_id == class_id)
    if document_id is not None:
        query = query.where(model.document_id == document_id)
    records, cursor = paginate(session, query, model.id, pagination)
    access = {
        (class_id, document_id): is_source_accessible(session, core, actor, class_id, document_id)
        for class_id, document_id in {(record.class_id, record.document_id) for record in records}
    }
    return [(record, access[record.class_id, record.document_id]) for record in records], cursor


def list_notes(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    pagination: Pagination,
    class_id: UUID | None,
    document_id: UUID | None,
) -> Page[PersonalNoteDTO]:
    records, cursor = personal_records(
        session, core, actor, SourceBoundNote, pagination, class_id, document_id
    )
    return Page[PersonalNoteDTO](
        items=[note_dto(session, note, accessible) for note, accessible in records],
        next_cursor=cursor,
    )


def create_note(
    session: Session, core: CoreClient, actor: CoreUser, class_id: UUID, payload: NoteCreate
) -> PersonalNoteDTO:
    with transaction(session):
        serving = require_live_source(
            session, core, actor, class_id, payload.document_id, lock=True
        )
        version = select_viewing_version(
            session, payload.document_id, serving, payload.version_number
        )
        note = SourceBoundNote(
            school_id=actor.school_id,
            user_id=actor.id,
            class_id=class_id,
            document_id=payload.document_id,
            document_version_id=version.id,
            version_number=version.version_number,
            locator=payload.locator,
            page_number=payload.page_number,
            note_text=payload.note_text,
            source_quote=payload.source_quote,
        )
        session.add(note)
        session.flush()
        return note_dto(session, note, True)


def patch_note(
    session: Session, core: CoreClient, actor: CoreUser, note_id: UUID, payload: NotePatch
) -> PersonalNoteDTO:
    with transaction(session):
        note = require_owned_note(session, actor, note_id)
        accessible = is_source_accessible(session, core, actor, note.class_id, note.document_id)
        note.note_text = payload.note_text
        note.updated_at = utc_now()
        return note_dto(session, note, accessible)


def delete_note(session: Session, actor: CoreUser, note_id: UUID) -> None:
    with transaction(session):
        session.delete(require_owned_note(session, actor, note_id))


def list_bookmarks(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    pagination: Pagination,
    class_id: UUID | None,
) -> Page[BookmarkDTO]:
    records, cursor = personal_records(session, core, actor, DocumentBookmark, pagination, class_id)
    return Page[BookmarkDTO](
        items=[
            BookmarkDTO(**source_reference(session, bookmark, accessible))
            for bookmark, accessible in records
        ],
        next_cursor=cursor,
    )


def toggle_bookmark(
    session: Session, core: CoreClient, actor: CoreUser, class_id: UUID, payload: BookmarkToggle
) -> BookmarkToggleResponse:
    with transaction(session):
        serving = require_live_source(
            session, core, actor, class_id, payload.document_id, lock=True
        )
        version = select_viewing_version(
            session, payload.document_id, serving, payload.version_number
        )
        locator = payload.locator
        if locator is None:
            locator = "document" if payload.page_number is None else f"page:{payload.page_number}"
        query = source_query(DocumentBookmark, actor, class_id, payload.document_id)
        existing = session.scalar(query.where(DocumentBookmark.locator == locator))
        if existing is not None:
            session.delete(existing)
            return BookmarkToggleResponse(bookmarked=False, bookmark=None)

        bookmark = DocumentBookmark(
            school_id=actor.school_id,
            user_id=actor.id,
            class_id=class_id,
            document_id=payload.document_id,
            document_version_id=version.id,
            version_number=version.version_number,
            locator=locator,
            page_number=payload.page_number,
        )
        session.add(bookmark)
        session.flush()
        return BookmarkToggleResponse(
            bookmarked=True, bookmark=BookmarkDTO(**source_reference(session, bookmark, True))
        )
