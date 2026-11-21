import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.common.auth_dependencies import verify_internal_service_key
from app.common.config import settings
from app.common.db import get_db
from app.common.security import create_access_token, decode_access_token, hash_password
from app.main import app
from app.modules.identity.models import Role, RoleAssignment, User, UserStatus
from app.modules.identity.public import IntrospectionSubject
from app.modules.identity.rate_limit import RateLimiter, get_rate_limiter

PATH = "/internal/v1/auth/introspect"
SERVICE_KEY = "test-only introspection service credential"
SIGNING_KEY = "test-only introspection token signing key"
SERVICE_HEADERS = {"X-Service-Key": SERVICE_KEY}


@pytest.fixture
def auth_settings(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", SecretStr(SIGNING_KEY))
    monkeypatch.setattr(settings, "internal_service_secret", SecretStr(SERVICE_KEY))


@pytest.fixture
def introspection(auth_settings, monkeypatch):
    subject = IntrospectionSubject(
        id=uuid4(),
        status=UserStatus.ACTIVE,
        session_version=3,
        school_id=uuid4(),
        display_name="Test Lecturer",
        roles=[Role.LECTURER, Role.ADMIN],
    )
    session = Mock(spec=Session)
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: session)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, session, subject


def set_subject(session, subject):
    rows = []
    if subject is not None:
        rows = [
            SimpleNamespace(
                id=subject.id,
                status=subject.status,
                session_version=subject.session_version,
                school_id=subject.school_id,
                display_name=subject.display_name,
                role=role,
            )
            for role in subject.roles or [None]
        ]
    session.execute.return_value.all.return_value = rows


def assert_inactive(response):
    assert response.status_code == 200
    assert response.json() == {"active": False}
    assert response.headers["Cache-Control"] == "no-store"


def test_valid_token_returns_current_database_identity(introspection):
    client, session, subject = introspection
    set_subject(session, subject)
    token = create_access_token(subject.id, ["STUDENT"], subject.session_version)

    response = client.post(PATH, json={"token": token}, headers=SERVICE_HEADERS)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "active": True,
        "user": {
            "id": str(subject.id),
            "status": "ACTIVE",
            "roles": ["LECTURER", "ADMIN"],
            "school_id": str(subject.school_id),
            "display_name": subject.display_name,
        },
        "exp": decode_access_token(token).exp,
    }
    session.execute.assert_called_once()


@pytest.mark.parametrize("invalid", ["expired", "tampered-signature", "malformed"])
def test_invalid_token_is_inactive_without_database_lookup(introspection, invalid):
    client, session, subject = introspection
    token = create_access_token(subject.id, ["LECTURER"], subject.session_version)
    if invalid == "expired":
        now = int(datetime.now(UTC).timestamp())
        expired = decode_access_token(token).model_copy(update={"iat": now - 60, "exp": now - 1})
        token = jwt.encode(expired.model_dump(mode="json"), SIGNING_KEY, settings.algorithm)
    elif invalid == "tampered-signature":
        header, payload, signature = token.split(".")
        first_char = "A" if signature[0] != "A" else "B"
        token = f"{header}.{payload}.{first_char}{signature[1:]}"
    else:
        token = "invalid.jwt"

    assert_inactive(client.post(PATH, json={"token": token}, headers=SERVICE_HEADERS))
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "subject_changes",
    [
        {"session_version": 4},
        {"status": UserStatus.LOCKED},
        {"status": UserStatus.PENDING_VERIFICATION},
        None,
        {"roles": []},
    ],
    ids=["revoked", "locked", "pending-verification", "missing-user", "no-roles"],
)
def test_introspection_rejects_current_account_state(introspection, subject_changes):
    client, session, subject = introspection
    set_subject(session, None if subject_changes is None else replace(subject, **subject_changes))
    token = create_access_token(subject.id, ["LECTURER"], subject.session_version)

    assert_inactive(client.post(PATH, json={"token": token}, headers=SERVICE_HEADERS))
    session.execute.assert_called_once()


@pytest.mark.parametrize("headers", [{}, {"X-Service-Key": "incorrect"}], ids=["missing", "wrong"])
def test_introspection_requires_internal_service_key(introspection, headers):
    client, session, _ = introspection
    response = client.post(PATH, json={"token": "invalid.jwt"}, headers=headers)
    assert response.status_code == 401
    session.execute.assert_not_called()


@pytest.mark.parametrize("setting", ["internal_service_secret", "secret_key"])
def test_introspection_fails_closed_when_auth_is_unconfigured(introspection, monkeypatch, setting):
    client, session, _ = introspection
    monkeypatch.setattr(settings, setting, None)
    response = client.post(PATH, json={"token": "invalid.jwt"}, headers=SERVICE_HEADERS)
    assert response.status_code == 503
    session.execute.assert_not_called()


def test_introspection_requires_its_own_scope(introspection, monkeypatch):
    client, session, _ = introspection
    monkeypatch.setitem(
        app.dependency_overrides,
        verify_internal_service_key,
        lambda: frozenset({"core:check_access"}),
    )
    response = client.post(PATH, json={"token": "invalid.jwt"}, headers=SERVICE_HEADERS)
    assert response.status_code == 403
    session.execute.assert_not_called()


def test_introspection_database_outage_returns_503(introspection):
    client, session, subject = introspection
    session.execute.side_effect = OperationalError(None, None, OSError("database unavailable"))
    token = create_access_token(subject.id, ["LECTURER"], subject.session_version)

    response = client.post(PATH, json={"token": token}, headers=SERVICE_HEADERS)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "active" not in response.json()
    session.execute.assert_called_once()


@pytest.mark.parametrize(
    "token", ["", "oversized-token-".ljust(4097, "x")], ids=["empty", "oversized"]
)
def test_introspection_validation_does_not_echo_token(introspection, caplog, token):
    client, session, _ = introspection
    response = client.post(PATH, json={"token": token}, headers=SERVICE_HEADERS)
    assert response.status_code == 422
    assert "input" not in response.json()["error"]
    assert "oversized-token-" not in response.text
    assert "oversized-token-" not in caplog.text
    session.execute.assert_not_called()


def test_openapi_includes_internal_auth_introspection():
    spec = app.openapi()
    operation = spec["paths"][PATH]["post"]
    assert operation["tags"] == ["Internal"]
    assert [p["name"] for p in operation["parameters"] if p["in"] == "header"] == ["X-Service-Key"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/IntrospectTokenRequest"
    )
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/IntrospectTokenResponse"
    )
    assert set(operation["responses"]) == {"200", "400", "401", "403", "422", "500", "503"}


@pytest.fixture
def postgres_connection(monkeypatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    core_dir = Path(__file__).resolve().parents[2]
    config = Config(str(core_dir / "alembic.ini"))
    config.set_main_option("script_location", str(core_dir / "migrations"))
    with monkeypatch.context() as migration_settings:
        migration_settings.setattr(settings, "database_url", database_url)
        command.upgrade(config, "head")
    engine = create_engine(database_url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            connection.begin()
            yield connection
    finally:
        engine.dispose()


def test_postgres_login_introspect_and_logout_all(postgres_connection, auth_settings, monkeypatch):
    user_id = uuid4()
    email = f"introspect-{user_id.hex}@example.com"
    password = "test-only password for introspection"

    def test_db():
        with Session(
            bind=postgres_connection, join_transaction_mode="create_savepoint", autoflush=False
        ) as session:
            yield session

    with Session(bind=postgres_connection, join_transaction_mode="create_savepoint") as session:
        session.add(
            User(
                id=user_id,
                email=email,
                password_hash=hash_password(password),
                status=UserStatus.ACTIVE,
                email_verified_at=datetime.now(UTC),
            )
        )
        session.flush()
        session.add(RoleAssignment(user_id=user_id, role=Role.LECTURER))
        session.commit()

    monkeypatch.setitem(app.dependency_overrides, get_db, test_db)
    limiter = RateLimiter()
    monkeypatch.setitem(app.dependency_overrides, get_rate_limiter, lambda: limiter)
    with TestClient(app, base_url="https://core.example.com") as client:
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200
        token = login.json()["access_token"]
        body = {"token": token}
        response = client.post(PATH, json=body, headers=SERVICE_HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "active": True,
            "user": {
                "id": str(user_id),
                "status": "ACTIVE",
                "roles": ["LECTURER"],
                "school_id": None,
                "display_name": None,
            },
            "exp": decode_access_token(token).exp,
        }

        logout = client.post(
            "/api/v1/auth/logout-all", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout.status_code == 204
        assert_inactive(client.post(PATH, json=body, headers=SERVICE_HEADERS))
