import asyncio
from uuid import uuid4

import httpx
import pytest

from app.common.errors import AppError
from app.config import Settings
from app.integrations.document_client import ActiveGrant, GrantSnapshot, HttpDocumentClient


def _settings(**overrides) -> Settings:
    defaults = {
        "ai_database_url": None,
        "document_mode": "http",
        "document_api_url": "http://document.test",
        "document_service_key": "doc-secret-key",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_http_document_client_parses_document_active_grants_items():
    class_id, school_id = uuid4(), uuid4()
    doc_id, version_id = uuid4(), uuid4()
    correlation_id = str(uuid4())
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["key"] = request.headers.get("x-service-key")
        captured["correlation"] = request.headers.get("x-correlation-id")
        return httpx.Response(
            200,
            json={
                "class_id": str(class_id),
                "items": [
                    {
                        "document_id": str(doc_id),
                        "school_id": str(school_id),
                        "title": "Bài giảng Hình học",
                        "format": "PDF",
                        "serving_version_id": str(version_id),
                        "serving_version_number": 2,
                        "canonical_schema_version": "v1",
                        "canonical_sha256": "abc123",
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://document.test"
        ) as client:
            return await HttpDocumentClient(client, _settings()).active_grants(
                class_id, school_id, correlation_id
            )

    snapshot = asyncio.run(run())
    assert captured == {
        "path": f"/internal/v1/classes/{class_id}/documents/active-grants",
        "params": {"school_id": str(school_id)},
        "key": "doc-secret-key",
        "correlation": correlation_id,
    }
    assert snapshot == GrantSnapshot(
        class_id=class_id,
        school_id=school_id,
        grants=(
            ActiveGrant(
                document_id=doc_id,
                document_version_id=version_id,
                source_id=str(version_id),
            ),
        ),
    )


def test_http_document_client_preserves_legacy_grant_snapshot_payload():
    class_id, school_id = uuid4(), uuid4()
    doc_id, version_id = uuid4(), uuid4()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "class_id": str(class_id),
                "school_id": str(school_id),
                "grants": [
                    {
                        "document_id": str(doc_id),
                        "document_version_id": str(version_id),
                        "source_id": "content-hash-1",
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://document.test"
        ) as client:
            return await HttpDocumentClient(client, _settings()).active_grants(
                class_id, school_id, str(uuid4())
            )

    snapshot = asyncio.run(run())
    assert snapshot.source_ids == frozenset({"content-hash-1"})


@pytest.mark.parametrize("case", ["cross_school", "wrong_class", "duplicate_version", "empty"])
def test_http_document_client_fails_closed_on_invalid_active_grants(case: str):
    class_id, school_id = uuid4(), uuid4()
    doc_id, version_id = uuid4(), uuid4()
    if case == "cross_school":
        payload = {
            "class_id": str(class_id),
            "items": [
                {
                    "document_id": str(doc_id),
                    "school_id": str(uuid4()),
                    "serving_version_id": str(version_id),
                }
            ],
        }
    elif case == "wrong_class":
        payload = {"class_id": str(uuid4()), "items": []}
    elif case == "duplicate_version":
        payload = {
            "class_id": str(class_id),
            "items": [
                {
                    "document_id": str(doc_id),
                    "school_id": str(school_id),
                    "serving_version_id": str(version_id),
                },
                {
                    "document_id": str(uuid4()),
                    "school_id": str(school_id),
                    "serving_version_id": str(version_id),
                },
            ],
        }
    else:
        payload = {"class_id": str(class_id)}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://document.test"
        ) as client:
            await HttpDocumentClient(client, _settings()).active_grants(
                class_id, school_id, str(uuid4())
            )

    with pytest.raises(AppError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 503
