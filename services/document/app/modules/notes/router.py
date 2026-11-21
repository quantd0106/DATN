from uuid import UUID

from fastapi import APIRouter

from app.common.auth import Actor
from app.common.core_client import Core
from app.common.db import Db
from app.common.pagination import Page, PageRequest
from app.modules.notes import service
from app.modules.notes.schemas import (
    BookmarkDTO,
    BookmarkToggle,
    BookmarkToggleResponse,
    NoteCreate,
    NotePatch,
    PersonalNoteDTO,
)

router = APIRouter(prefix="/api/v1", tags=["Notes and bookmarks"])


@router.get("/users/me/notes")
def my_notes(
    session: Db,
    core: Core,
    actor: Actor,
    pagination: PageRequest,
    class_id: UUID | None = None,
    document_id: UUID | None = None,
) -> Page[PersonalNoteDTO]:
    return service.list_notes(session, core, actor, pagination, class_id, document_id)


@router.post("/classes/{class_id}/notes", status_code=201)
def create_note(
    class_id: UUID, payload: NoteCreate, session: Db, core: Core, actor: Actor
) -> PersonalNoteDTO:
    return service.create_note(session, core, actor, class_id, payload)


@router.patch("/notes/{note_id}")
def patch_note(
    note_id: UUID, payload: NotePatch, session: Db, core: Core, actor: Actor
) -> PersonalNoteDTO:
    return service.patch_note(session, core, actor, note_id, payload)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: UUID, session: Db, actor: Actor) -> None:
    service.delete_note(session, actor, note_id)


@router.get("/users/me/bookmarks")
def my_bookmarks(
    session: Db, core: Core, actor: Actor, pagination: PageRequest, class_id: UUID | None = None
) -> Page[BookmarkDTO]:
    return service.list_bookmarks(session, core, actor, pagination, class_id)


@router.post("/classes/{class_id}/bookmarks/toggle")
def toggle_bookmark(
    class_id: UUID, payload: BookmarkToggle, session: Db, core: Core, actor: Actor
) -> BookmarkToggleResponse:
    return service.toggle_bookmark(session, core, actor, class_id, payload)
