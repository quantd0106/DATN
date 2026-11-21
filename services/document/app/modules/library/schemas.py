from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from app.common.types import DocumentFormat, ProcessingStatus
from app.modules.grants.public import ClassDocumentGrantDTO
from app.modules.versions.public import DocumentVersionDTO

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=2000)]


class DocumentPolicyResponse(BaseModel):
    max_size_bytes: int
    max_pages: int
    allowed_formats: list[DocumentFormat]
    allowed_extensions: list[str]
    allowed_mime_types: list[str]


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def validate_title(self):
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("title must be a string")
        return self


class DocumentSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    owner_user_id: UUID
    owner_display_name: str | None
    title: str
    description: str | None
    format: DocumentFormat
    created_at: datetime
    updated_at: datetime
    latest_version: DocumentVersionDTO
    latest_version_number: int
    serving_version: DocumentVersionDTO | None
    processing_status: ProcessingStatus
    page_count: int | None
    error_message: str | None
    active_grant_count: int


class DocumentDetailResponse(BaseModel):
    document: DocumentSummaryDTO
    versions: list[DocumentVersionDTO]
    grants: list[ClassDocumentGrantDTO]
