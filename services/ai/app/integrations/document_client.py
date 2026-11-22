from collections.abc import Mapping
from typing import Annotated, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.errors import AppError
from app.config import Settings
from app.integrations.http import request_model, service_headers

SourceID = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
SourceSpan = tuple[
    Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, gt=0)]
]


class ActiveGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: UUID
    document_version_id: UUID
    source_id: SourceID


class GrantSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    class_id: UUID
    school_id: UUID
    grants: tuple[ActiveGrant, ...]

    @model_validator(mode="after")
    def unique_sources(self) -> "GrantSnapshot":
        if len({grant.source_id for grant in self.grants}) != len(self.grants):
            raise ValueError("Duplicate active source")
        return self

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(grant.source_id for grant in self.grants)


class SourceLocation(BaseModel):
    document_name: str = Field(strict=True, min_length=1, max_length=500)
    page: int | None = Field(default=None, strict=True, ge=1)
    slide: int | None = Field(default=None, strict=True, ge=1)
    section: str | None = Field(default=None, strict=True, max_length=500)


class LocationResponse(BaseModel):
    class_id: UUID
    school_id: UUID
    document_version_id: UUID
    source_id: SourceID
    location: SourceLocation | None


class _DocumentActiveGrantItem(BaseModel):
    document_id: UUID
    school_id: UUID
    serving_version_id: UUID
    serving_version_number: int | None = Field(default=None, ge=1)
    title: str | None = None


class _ActiveGrantsPayload(BaseModel):
    class_id: UUID
    school_id: UUID | None = None
    grants: tuple[ActiveGrant, ...] | None = None
    items: tuple[_DocumentActiveGrantItem, ...] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "_ActiveGrantsPayload":
        if (self.grants is None) == (self.items is None):
            raise ValueError("Expected either grants or items")
        if self.grants is not None and self.school_id is None:
            raise ValueError("Expected school_id with grants")
        return self


class DocumentPort(Protocol):
    async def active_grants(
        self, class_id: UUID, school_id: UUID, correlation_id: str
    ) -> GrantSnapshot: ...

    async def locate(
        self,
        class_id: UUID,
        school_id: UUID,
        grant: ActiveGrant,
        spans: tuple[SourceSpan, ...],
        correlation_id: str,
    ) -> SourceLocation | None: ...


class HttpDocumentClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings
        self._document_names: dict[UUID, str] = {}

    def document_name(self, document_version_id: UUID) -> str | None:
        return self._document_names.get(document_version_id)

    async def active_grants(
        self, class_id: UUID, school_id: UUID, correlation_id: str
    ) -> GrantSnapshot:
        if self.settings.document_mode != "http":
            raise AppError(503)
        payload = await request_model(
            self.client,
            self.settings.document_api_url,
            f"/internal/v1/classes/{class_id}/documents/active-grants",
            _ActiveGrantsPayload,
            params={"school_id": str(school_id)},
            headers=service_headers(self.settings.document_service_key, correlation_id),
        )
        if payload.grants is not None:
            if (payload.class_id, payload.school_id) != (class_id, school_id):
                raise AppError(503)
            try:
                return GrantSnapshot(
                    class_id=payload.class_id,
                    school_id=payload.school_id,
                    grants=payload.grants,
                )
            except ValueError:
                raise AppError(503) from None
        assert payload.items is not None
        if payload.class_id != class_id or any(
            item.school_id != school_id for item in payload.items
        ):
            raise AppError(503)
        try:
            for item in payload.items:
                if item.title:
                    self._document_names[item.serving_version_id] = item.title
            grants = tuple(
                ActiveGrant(
                    document_id=item.document_id,
                    document_version_id=item.serving_version_id,
                    source_id=str(item.serving_version_id),
                )
                for item in payload.items
            )
            return GrantSnapshot(class_id=class_id, school_id=school_id, grants=grants)
        except ValueError:
            raise AppError(503) from None

    async def locate(
        self,
        class_id: UUID,
        school_id: UUID,
        grant: ActiveGrant,
        spans: tuple[SourceSpan, ...],
        correlation_id: str,
    ) -> SourceLocation | None:
        # Re-read grants before sending spans to the location endpoint.
        snapshot = await self.active_grants(class_id, school_id, correlation_id)
        if grant not in snapshot.grants:
            raise AppError(404)
        result = await request_model(
            self.client,
            self.settings.document_api_url,
            self.settings.document_locate_path,
            LocationResponse,
            headers=service_headers(self.settings.document_service_key, correlation_id),
            method="POST",
            payload={
                "class_id": str(class_id),
                "school_id": str(school_id),
                "document_version_id": str(grant.document_version_id),
                "source_id": grant.source_id,
                "source_spans": spans,
            },
        )
        if (result.class_id, result.school_id, result.document_version_id, result.source_id) != (
            class_id,
            school_id,
            grant.document_version_id,
            grant.source_id,
        ):
            raise AppError(503)
        return result.location

    async def canonical_artifact(self, grant: ActiveGrant, correlation_id: str) -> dict | None:
        if self.settings.document_mode != "http" or self.settings.document_api_url is None:
            return None
        try:
            response = await self.client.get(
                f"{str(self.settings.document_api_url).rstrip('/')}/internal/v1/documents/{grant.document_id}/versions/{grant.document_version_id}/canonical",
                headers=service_headers(self.settings.document_service_key, correlation_id),
            )
            if response.status_code != 200:
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError):
            return None


class LocalDevDocumentClient:
    """Fixture grants are exact per school/class; an unknown scope grants nothing."""

    def __init__(
        self,
        snapshots: Mapping[tuple[UUID, UUID], GrantSnapshot],
        locations: Mapping[tuple[UUID, UUID, UUID, str, tuple[SourceSpan, ...]], SourceLocation],
    ):
        self.snapshots = dict(snapshots)
        self.locations = dict(locations)

    async def active_grants(
        self, class_id: UUID, school_id: UUID, correlation_id: str
    ) -> GrantSnapshot:
        snapshot = self.snapshots.get((school_id, class_id))
        if snapshot is None or (snapshot.class_id, snapshot.school_id) != (class_id, school_id):
            raise AppError(503)
        return snapshot

    async def locate(
        self,
        class_id: UUID,
        school_id: UUID,
        grant: ActiveGrant,
        spans: tuple[SourceSpan, ...],
        correlation_id: str,
    ) -> SourceLocation | None:
        snapshot = await self.active_grants(class_id, school_id, correlation_id)
        if grant not in snapshot.grants:
            raise AppError(404)
        return self.locations.get(
            (school_id, class_id, grant.document_version_id, grant.source_id, spans)
        )
