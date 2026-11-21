from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.common.types import AcceptanceMode, DocumentFormat, ProcessingStatus


class VersionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    version_number: int
    original_filename: str
    file_type: DocumentFormat
    mime_type: str
    file_size_bytes: int
    page_count: int | None
    processing_status: ProcessingStatus
    acceptance_mode: AcceptanceMode | None
    accepted_by_user_id: UUID | None
    accepted_at: datetime | None
    review_summary: dict | None = Field(
        validation_alias=AliasChoices("review_summary", "review_summary_json")
    )
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    is_serving: bool = False
    is_superseded: bool = False


class ReaderOutlineEntry(BaseModel):
    id: str
    label: str
    page_number: int = Field(ge=1)


class ReaderPage(BaseModel):
    page_number: int = Field(ge=1)
    title: str
    paragraphs: list[str] = Field(default_factory=list)
    formulas: list[str] = Field(default_factory=list)


class ReaderProjection(BaseModel):
    outline: list[ReaderOutlineEntry]
    pages: list[ReaderPage]
