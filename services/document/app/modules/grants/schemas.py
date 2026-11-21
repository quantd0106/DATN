from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.common.types import DocumentFormat
from app.modules.notes.public import PersonalNoteDTO
from app.modules.versions.public import ReaderOutlineEntry, ReaderPage


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID


class ClassDocumentGrantDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    class_id: UUID
    document_id: UUID
    granted_by_user_id: UUID
    revoked_by_user_id: UUID | None
    is_active: bool
    granted_at: datetime
    revoked_at: datetime | None
    serving_version_number: int | None = None


class ReaderDocumentDTO(BaseModel):
    id: UUID
    school_id: UUID
    owner_user_id: UUID
    owner_display_name: str | None
    title: str
    description: str | None
    format: DocumentFormat
    created_at: datetime
    updated_at: datetime
    current_version: int
    page_count: int | None
    processing_status: Literal["SUCCESS"] = "SUCCESS"


class ClassDocumentSummaryDTO(ReaderDocumentDTO):
    serving_version: int


class DocumentReaderDTO(BaseModel):
    document: ReaderDocumentDTO
    is_source_accessible: bool
    serving_version: int | None
    viewing_version: int | None
    outline: list[ReaderOutlineEntry]
    pages: list[ReaderPage]
    notes: list[PersonalNoteDTO]
    revoked_reason: str | None = None
    is_bookmarked: bool = False


class ActiveGrantItem(BaseModel):
    document_id: UUID
    school_id: UUID
    title: str
    format: DocumentFormat
    serving_version_id: UUID
    serving_version_number: int
    canonical_schema_version: str | None
    canonical_sha256: str | None


class ActiveGrantsResponse(BaseModel):
    class_id: UUID
    items: list[ActiveGrantItem]
