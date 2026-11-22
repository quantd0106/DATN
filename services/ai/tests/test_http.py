import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.common.auth import get_current_user, verify_internal_service_key
from app.common.logging import RedactionFilter
from app.config import Settings
from app.integrations.core_client import CoreClient, CurrentUser
from app.main import create_app
from app.runtime.rag_runtime import RagRuntime


def test_health_and_correlation(monkeypatch):
    app = create_app(Settings())
    correlation_id = str(uuid4())
    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"X-Correlation-ID": correlation_id, "Origin": "http://localhost:3000"},
        )
        assert response.json() == {"status": "live"}
        assert response.headers["X-Correlation-ID"] == correlation_id
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"
        assert response.headers["Access-Control-Expose-Headers"] == "X-Correlation-ID"
        response = client.get("/health/ready")
        assert response.status_code == 503
        err = response.json()["error"]
        assert err["code"] == "SERVICE_UNAVAILABLE"
        assert err["correlation_id"] == response.headers["X-Correlation-ID"]

        class ConnectedDatabase:
            def ping(self):
                pass

        monkeypatch.setattr(app.state, "database", ConnectedDatabase())
        result = client.get("/health/ready").json()
        assert result["db"] == "connected" and result["llama"] == "disabled"
        assert result["scheduler"]["slots"] == 1

        def unavailable():
            raise SQLAlchemyError("private connection info")

        monkeypatch.setattr(app.state.database, "ping", unavailable)
        assert client.get("/health/ready").status_code == 503


def test_errors_omit_private_input_and_keep_cors():
    app = create_app(Settings())

    @app.get("/validation")
    async def validation(id: UUID):
        return {"id": id}

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError("private content")

    @app.get("/internal-key", dependencies=[Depends(verify_internal_service_key)])
    async def internal_key():
        return {"ok": True}

    with TestClient(app) as client:
        for path, status, code in [
            ("/validation?id=private%20content", 422, "VALIDATION_ERROR"),
            ("/unexpected", 500, "INTERNAL_ERROR"),
            ("/missing", 404, "NOT_FOUND"),
            ("/internal-key", 503, "SERVICE_UNAVAILABLE"),
        ]:
            response = client.get(
                path,
                headers={"Origin": "http://localhost:3000", "X-Correlation-ID": "private content"},
            )
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
            assert "private content" not in response.text
            assert UUID(response.headers["X-Correlation-ID"])
            assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:3000"


def test_log_filter_discards_chat_and_erases_exception_data():
    redact = RedactionFilter()
    access = logging.LogRecord("uvicorn.access", 20, "", 0, "quote in path", (), None)
    assert not redact.filter(access)
    arbitrary = logging.LogRecord("app.ai", 40, "", 0, "private question", (), None)
    assert not redact.filter(arbitrary)
    allowed = logging.LogRecord(
        "app.ai",
        40,
        "",
        0,
        "request_failed",
        ("token",),
        (RuntimeError, RuntimeError("quote"), None),
    )
    assert redact.filter(allowed)
    assert allowed.getMessage() == "request_failed"
    assert allowed.exc_info is None


def test_internal_summary_checks_its_key_and_suppresses_small_topics():
    app = create_app(
        Settings(
            ai_internal_service_secret="index-test-key",
            ai_analytics_service_secret="analytics-test-key",
        )
    )
    school, klass, large, small = (str(uuid4()) for _ in range(4))
    rows = [
        {"class_id": klass, "school_id": school, "student_id": str(uuid4()), "topic_id": topic}
        for topic in [large] * 5 + [small] * 4
    ]
    path = f"/internal/v1/classes/{klass}/tutor/topic-summary"
    with TestClient(app) as client:
        body = {"school_id": school, "rows": rows}
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path,
                json=body,
                headers={"X-Service-Key": "index-test-key"},
            ).status_code
            == 401
        )
        headers = {"X-Service-Key": "analytics-test-key"}
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == [{"topic_id": large, "students": 5}]
        assert response.headers["Cache-Control"] == "no-store"
        rows[0]["class_id"] = str(uuid4())
        assert client.post(path, json=body, headers=headers).status_code == 422
        paths = client.get("/openapi.json").json()["paths"]
        assert (
            "text/event-stream"
            in paths["/api/v1/tutor/conversations/{conversation_id}/messages"]["post"]["responses"][
                "200"
            ]["content"]
        )


def test_jwt_requires_live_core_session_and_uses_current_roles(monkeypatch):
    secret = "test-secret-value-32-characters-only"
    settings = Settings(core_jwt_secret=secret, ai_internal_service_secret="internal-test-key")
    app = create_app(settings)
    user_id, school_id = uuid4(), uuid4()
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "sub": str(user_id),
            "roles": ["ADMIN"],
            "session_version": 1,
            "iat": now,
            "exp": now + int(timedelta(minutes=5).total_seconds()),
        },
        secret,
        algorithm="HS256",
    )
    core_calls = []

    def core(request):
        core_calls.append(request)
        assert request.url.path == "/api/v1/users/me"
        assert request.headers["Authorization"] == f"Bearer {token}"
        if len(core_calls) == 1:
            return httpx.Response(
                200,
                json={
                    "id": str(user_id),
                    "school_id": str(school_id),
                    "status": "ACTIVE",
                    "roles": ["STUDENT"],
                },
            )
        return httpx.Response(401, text="revoked")

    @app.get("/actor")
    async def actor(user: Annotated[CurrentUser, Depends(get_current_user)]):
        return user

    @app.get("/internal-key", dependencies=[Depends(verify_internal_service_key)])
    async def internal_key():
        return {"ok": True}

    with TestClient(app) as client:
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(core))
        monkeypatch.setattr(app.state, "core", CoreClient(upstream, settings))
        try:
            assert client.get("/actor").status_code == 401
            assert (
                client.get("/actor", headers={"Authorization": "Bearer malformed"}).status_code
                == 401
            )
            assert not core_calls
            headers = {"Authorization": f"Bearer {token}"}
            assert client.get("/actor", headers=headers).json()["roles"] == ["STUDENT"]
            assert client.get("/actor", headers=headers).status_code == 401
            assert (
                client.get("/internal-key", headers={"X-Service-Key": "wrong"}).status_code == 401
            )
            assert (
                client.get(
                    "/internal-key", headers={"X-Service-Key": "internal-test-key"}
                ).status_code
                == 200
            )
        finally:
            client.portal.call(upstream.aclose)


@pytest.mark.parametrize(
    ("code", "body", "is_ready"),
    [
        (200, {"status": "ok"}, True),
        (200, {"status": "loading model"}, False),
        (503, {"status": "loading model"}, False),
    ],
)
def test_llama_probe(code, body, is_ready):
    from app.common.errors import AppError

    def server(request):
        assert request.url.path == "/health"
        return httpx.Response(code, json=body)

    async def run():
        runtime = RagRuntime(
            Settings(
                llama_mode="local",
                llama_model_path="test.gguf",
                llama_server_path="llama-server",
                llama_model_repo="fixture",
                llama_model_revision="fixture",
                llama_model_variant="fixture",
                llama_model_sha256="a" * 64,
            )
        )
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
                if is_ready:
                    assert await runtime.probe_llama(client) == "connected"
                else:
                    with pytest.raises(AppError) as error:
                        await runtime.probe_llama(client)
                    assert error.value.status_code == 503
        finally:
            await runtime.close()

    asyncio.run(run())
