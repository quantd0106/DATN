from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.common.types import DocumentFormat

Locator = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
NoteText = Annotated[str, StringConstraints(min_length=1)]


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    version_number: int | None = Field(default=None, ge=1)
    page_number: int | None = Field(default=None, ge=1)


class NoteCreate(SourceRequest):
    locator: Locator
    note_text: NoteText
    source_quote: str | None = None


class NotePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_text: NoteText


class BookmarkToggle(SourceRequest):
    locator: Locator | None = None


class SourceDTO(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    class_id: UUID
    document_id: UUID
    document_title: str
    document_format: DocumentFormat
    document_version_id: UUID | None
    version_number: int
    locator: str
    page_number: int | None
    created_at: datetime
    is_source_accessible: bool = False


class PersonalNoteDTO(SourceDTO):
    note_text: str
    source_quote: str | None
    updated_at: datetime


class BookmarkDTO(SourceDTO):
    pass


class BookmarkToggleResponse(BaseModel):
    bookmarked: bool
    bookmark: BookmarkDTO | None
