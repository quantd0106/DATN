from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.common.api import ERROR_RESPONSES, ApiServices
from app.common.auth import verify_internal_service_key
from app.common.errors import AppError
from app.integrations.document_client import SourceID
from app.modules.indexing.service import invalidate_grants, publish_generation, record_failed_build

router = APIRouter(
    prefix="/internal/v1/document-versions/{document_version_id}",
    tags=["Indexing"],
    dependencies=[Depends(verify_internal_service_key)],
    responses=ERROR_RESPONSES,
)
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class IndexBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_sha256: Digest


class IndexPublication(IndexBuild):
    source_id: SourceID
    generation_id: UUID
    expected_generation_id: UUID | None = None


@router.put("/index", status_code=204)
async def publish(document_version_id: UUID, body: IndexPublication, api: ApiServices) -> Response:
    db = api.require_db()
    root = api.runtime.settings.ai_index_root
    if root is None:
        raise AppError(503)

    def persist() -> None:
        with db.SessionLocal.begin() as session:
            publish_generation(
                session,
                artifact_root=root,
                document_version_id=document_version_id,
                generation_id=body.generation_id,
                expected_generation_id=body.expected_generation_id,
                source_id=body.source_id,
                source_sha256=body.source_sha256,
                encoder=api.runtime.encoder(),
            )

    await api.runtime.executor.run(persist)
    return Response(status_code=204)


@router.post("/index/failure", status_code=204)
def record_failure(document_version_id: UUID, body: IndexBuild, api: ApiServices) -> Response:
    with api.require_db().SessionLocal.begin() as session:
        record_failed_build(session, document_version_id, body.source_sha256)
    return Response(status_code=204)


@router.post("/grant-invalidation", status_code=204)
def grant_invalidation(document_version_id: UUID, api: ApiServices) -> Response:
    with api.require_db().SessionLocal() as session:
        invalidate_grants(session, api.runtime, document_version_id)
    return Response(status_code=204)
