import logging
import os
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from conftest import ANALYTICS, INTERNAL, authorization
from fastapi.testclient import TestClient

from app.common.db import Database
from app.common.logging import RedactionFilter, logger
from app.config import Settings
from app.main import create_app


def test_health_and_error_envelope(world):
    correlation = str(uuid4())
    assert world.call("GET", "/health/live").json() == {"status": "live"}
    assert world.call("GET", "/health/ready").json() == {
        "status": "ready",
        "db": "connected",
        "alembic_head": True,
    }
    response = world.call(
        "GET", world.base + "/assessments", expected=401, headers={"X-Correlation-ID": correlation}
    )
    assert response.headers["x-correlation-id"] == correlation
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.json()["error"]["correlation_id"] == correlation
    with TestClient(create_app(Settings(learning_database_url=None))) as client:
        assert client.get("/health/ready").status_code == 503


def test_redaction_filter():
    redaction = RedactionFilter()
    bad = logging.LogRecord(logger.name, logging.ERROR, "", 0, "token abc", (), None)
    assert not redaction.filter(bad)
    server = logging.LogRecord(
        "uvicorn.error", logging.ERROR, "", 0, "sensitive %s", ("payload",), None
    )
    assert redaction.filter(server)
    assert server.getMessage() == "server_error"
    good = logging.LogRecord(
        logger.name, logging.INFO, "", 0, "service_started", ("private",), None
    )
    assert redaction.filter(good)
    assert good.args == ()


def test_session_and_live_role_fail_closed(world):
    path = world.base + "/assessments"
    for kwargs in ({"expired": True}, {"secret": "invalid-fixture-signature-key-for-tests"}):
        world.call("GET", path, expected=401, headers=authorization(world.student, **kwargs))
    world.call(
        "POST",
        path,
        expected=403,
        json={},
        headers=authorization(world.student, signed_roles=["LECTURER"]),
    )
    world.core.locked.add(world.student.id)
    world.call("GET", path, world.student, expected=401)
    world.core.offline = True
    world.call("GET", path, world.student, expected=503)


def test_service_key_is_scoped_and_errors_are_safe(world):
    params = {"user_id": str(world.student.id), "school_id": str(world.core.school_id)}
    path = "/internal/v1/assessment-lock"
    world.call("GET", path, expected=401, params=params)
    world.call("GET", path, expected=401, params=params, headers=ANALYTICS)
    result = world.call("GET", path, params=params, headers=INTERNAL)
    assert result.json() == {**params, "active_official_assessment": False}
    response = world.call(
        "GET",
        path,
        expected=422,
        params={**params, "user_id": "private"},
        headers={**INTERNAL, "X-Correlation-ID": "invalid"},
    )
    assert "private" not in response.text
    assert UUID(response.headers["x-correlation-id"])
    world.call(
        "GET",
        f"/internal/v1/classes/{world.core.class_id}/grades/confirmed",
        params={"school_id": params["school_id"]},
        expected=401,
        headers=INTERNAL,
    )


@pytest.mark.skipif(
    not os.getenv("LEARNING_TEST_DATABASE_URL"), reason="PostgreSQL test URL is unset"
)
def test_postgres_migrations(monkeypatch):
    monkeypatch.setenv("LEARNING_DATABASE_URL", os.environ["LEARNING_TEST_DATABASE_URL"])
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.check(config)
    database = Database(Settings())
    try:
        database.ping()
    finally:
        database.close()
