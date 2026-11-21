import json
from io import StringIO
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from conftest import DOCUMENTS, INTERNAL_KEY, SERVICE_ROOT, bearer, internal_grants
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette import status

from app.common.auth import verify_internal_service_key
from app.common.config import Settings, get_settings
from app.common.core_client import HttpCoreClient
from app.common.db import get_db
from app.common.errors import AppError
from app.common.storage import StorageIntegrityError


def canonical_path(document_id, version_id):
    return f"/internal/v1/documents/{document_id}/versions/{version_id}/canonical"


def test_internal_scope_and_key_authentication(client, core, shared):
    grants = internal_grants(core.class_id)
    canonical = canonical_path(shared["id"], shared["latest_version"]["id"])
    for path in (grants, canonical):
        for headers in ({}, {"X-Service-Key": "wrong"}, bearer("primary")):
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "UNAUTHENTICATED"
            assert "wrong" not in response.text
            assert response.headers["Cache-Control"] == "no-store"
        assert client.get(path, headers=INTERNAL_KEY).status_code == 200

    client.app.dependency_overrides[verify_internal_service_key] = lambda: {"document:read_grants"}
    assert client.get(grants, headers=INTERNAL_KEY).status_code == 200
    assert client.get(canonical, headers=INTERNAL_KEY).status_code == 403


def test_unconfigured_internal_key_fails_closed(client, core, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", "")
    get_settings.cache_clear()
    assert client.get(internal_grants(core.class_id), headers=INTERNAL_KEY).status_code == 503


def test_active_grants_include_current_version_and_honor_school_filter(
    client, core, shared, set_version_state
):
    version = set_version_state(shared["latest_version"]["id"])
    response = client.get(
        internal_grants(core.class_id),
        params={"school_id": str(core.school_id)},
        headers=INTERNAL_KEY,
    )
    assert response.status_code == 200
    assert response.json() == {
        "class_id": str(core.class_id),
        "items": [
            {
                "document_id": shared["id"],
                "school_id": str(core.school_id),
                "title": shared["title"],
                "format": "TXT",
                "serving_version_id": str(version.id),
                "serving_version_number": 1,
                "canonical_schema_version": "idp-canonical-1",
                "canonical_sha256": version.canonical_sha256,
            }
        ],
    }
    wrong_school = client.get(
        internal_grants(core.class_id),
        params={"school_id": str(core.other_school_id)},
        headers=INTERNAL_KEY,
    )
    assert wrong_school.json()["items"] == []
    unrelated = client.get(internal_grants(core.second_class_id), headers=INTERNAL_KEY)
    assert unrelated.json()["items"] == []


def test_canonical_requires_success_live_document_and_verified_checksum(
    client,
    upload,
    set_version_state,
    storage,
):
    document = upload().json()
    document_id = document["id"]
    version_id = document["latest_version"]["id"]
    path = canonical_path(document_id, version_id)
    for state in ("PENDING", "PROCESSING", "NEEDS_REVIEW", "FAILED"):
        set_version_state(version_id, state)
        assert client.get(path, headers=INTERNAL_KEY).status_code == 409
    assert client.get(canonical_path(document_id, uuid4()), headers=INTERNAL_KEY).status_code == 404
    assert client.get(canonical_path(uuid4(), version_id), headers=INTERNAL_KEY).status_code == 404
    version = set_version_state(version_id)
    valid = client.get(path, headers=INTERNAL_KEY)
    assert valid.status_code == 200
    assert valid.json()["schema_version"] == "idp-canonical-1"
    assert valid.content == storage.read_bytes(version.canonical_storage_key)

    storage.save_bytes(version.canonical_storage_key, b'{"schema_version":"idp-canonical-1"}')
    assert client.get(path, headers=INTERNAL_KEY).status_code == 503
    storage.delete(version.canonical_storage_key)
    assert client.get(path, headers=INTERNAL_KEY).status_code == 409
    set_version_state(version_id)
    assert client.delete(f"{DOCUMENTS}/{document_id}", headers=bearer("primary")).status_code == 204
    assert client.get(path, headers=INTERNAL_KEY).status_code == 404


def test_health_and_correlation_headers(client, storage, monkeypatch):
    request_id = str(uuid4())
    live = client.get("/health/live", headers={"X-Correlation-ID": request_id})
    assert live.json() == {"status": "live"}
    assert live.headers["X-Correlation-ID"] == request_id
    assert live.headers["Cache-Control"] == "no-store"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "db": "connected", "storage": "ready"}
    assert not any(path.is_file() for path in storage.root.rglob("*"))

    monkeypatch.setattr(storage, "check_ready", lambda: False)
    failed = client.get("/health/ready", headers={"X-Correlation-ID": "token-like-invalid-input"})
    assert failed.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "token-like-invalid-input" not in failed.text
    assert UUID(failed.headers["X-Correlation-ID"])
    assert failed.json()["error"]["correlation_id"] == failed.headers["X-Correlation-ID"]


def test_readiness_requires_database(client):
    class OfflineDatabase:
        def execute(self, statement):
            raise SQLAlchemyError("confidential database configuration")

    client.app.dependency_overrides[get_db] = OfflineDatabase
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "confidential" not in response.text
    assert client.get("/health/live").status_code == 200


def test_readiness_requires_schema_even_when_database_responds(client):
    engine = create_engine("sqlite://")

    def empty_database():
        with Session(engine) as session:
            yield session

    client.app.dependency_overrides[get_db] = empty_database
    try:
        assert client.get("/health/ready").status_code == 503
    finally:
        engine.dispose()


def test_storage_enforces_paths_and_checksum_without_overwriting(storage):
    key = "source/abc/document.txt"
    checksum = storage.save_bytes(key, b"original")
    assert storage.read_bytes(key, expected_sha256=checksum) == b"original"
    with pytest.raises(StorageIntegrityError):
        storage.save_bytes(key, b"incorrect", expected_sha256=checksum)
    assert storage.read_bytes(key, expected_sha256=checksum) == b"original"
    for path in ("../escape", "/absolute", "drive:C.txt", "a/../b", "a//b", "a\\b", "NUL.txt"):
        with pytest.raises(ValueError):
            storage.open_stream(path)
    assert [path.name for path in storage.root.rglob("*") if path.is_file()] == ["document.txt"]


def test_storage_rejects_symlinks_when_supported(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.root.mkdir()
    try:
        (storage.root / "shortcut").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        # Exercise path containment in the other storage test on Windows without symlink privileges.
        return
    with pytest.raises(ValueError):
        storage.save_bytes("shortcut/escape", b"data")
    assert not (outside / "escape").exists()


def test_http_core_contract_and_no_auth_cache():
    user_id, class_id, school_id = uuid4(), uuid4(), uuid4()
    responses = iter((True, False))

    def handle(request):
        assert request.headers["X-Service-Key"] == "service-key"
        if request.method == "POST":
            assert request.url.path == "/internal/v1/auth/introspect"
            assert not request.url.query
            assert json.loads(request.content) == {"token": "jwt-from-web"}
            if not next(responses):
                return httpx.Response(200, json={"active": False})
            return httpx.Response(
                200,
                json={
                    "active": True,
                    "user": {
                        "id": str(user_id),
                        "school_id": str(school_id),
                        "status": "ACTIVE",
                        "roles": ["LECTURER"],
                        "display_name": "Teacher",
                    },
                },
            )
        assert request.url.path == f"/internal/v1/classes/{class_id}/access"
        assert dict(request.url.params) == {
            "user_id": str(user_id),
            "action": "manage_class_documents",
        }
        return httpx.Response(
            200,
            json={
                "allowed": True,
                "class_id": str(class_id),
                "user_id": str(user_id),
                "action": "manage_class_documents",
                "class_status": "ACTIVE",
                "school_id": str(school_id),
                "teaching_role": "ASSISTING",
            },
        )

    settings = Settings(core_api_url="http://core.invalid/", internal_service_secret="service-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        core = HttpCoreClient(settings, http)
        assert core.introspect_token("jwt-from-web").id == user_id
        with pytest.raises(AppError) as revoked:
            core.introspect_token("jwt-from-web")
        assert revoked.value.status_code == 401
        assert core.check_class_access(class_id, user_id, "manage_class_documents").allowed


@pytest.mark.parametrize("failure", ["timeout", "network", "5xx", "401", "malformed"])
def test_http_core_fail_closed(failure):
    def handle(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("Untrusted timeout detail")
        if failure == "network":
            raise httpx.ConnectError("Untrusted network detail")
        if failure == "malformed":
            return httpx.Response(200, content=b"invalid JSON")
        return httpx.Response(503 if failure == "5xx" else 401)

    settings = Settings(core_api_url="http://core.invalid", internal_service_secret="service-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        core = HttpCoreClient(settings, http)
        with pytest.raises(AppError) as auth:
            core.introspect_token("jwt-from-web")
        assert auth.value.status_code == 503
        with pytest.raises(AppError) as access:
            core.check_class_access(uuid4(), uuid4(), "read_document_source")
        assert access.value.status_code == 503


def test_http_core_rejects_missing_configuration():
    with httpx.Client() as http:
        core = HttpCoreClient(Settings(core_api_url="", internal_service_secret=""), http)
        with pytest.raises(AppError) as error:
            core.introspect_token("jwt-from-web")
        assert error.value.status_code == 503


def test_alembic_postgresql_schema(database):
    tables = {
        "documents",
        "document_versions",
        "class_document_grants",
        "source_bound_notes",
        "document_bookmarks",
        "outbox_events",
        "audit_events",
    }
    if database.dialect.name == "postgresql":
        inspector = inspect(database)
        assert set(inspector.get_table_names(schema="document")) == tables | {"alembic_version"}
        assert database.scalar(text("SELECT version_num FROM document.alembic_version")) == "0001"
        checks = inspector.get_check_constraints("document_versions", schema="document")
        assert len(checks) == 4
        assert all("SUPERSEDED" not in check["sqltext"] for check in checks)
        grants = inspector.get_indexes("class_document_grants", schema="document")
        partial = next(
            index for index in grants if index["name"] == "uq_active_class_document_grant"
        )
        assert partial["unique"]
        assert "is_active" in partial["dialect_options"]["postgresql_where"]
        for table in ("source_bound_notes", "document_bookmarks"):
            for fk in inspector.get_foreign_keys(table, schema="document"):
                assert fk.get("options", {}).get("ondelete") != "CASCADE"
    else:
        output = StringIO()
        config = Config(str(SERVICE_ROOT / "alembic.ini"), output_buffer=output)
        config.attributes["database_url"] = "postgresql+psycopg://"
        command.upgrade(config, "head", sql=True)
        sql = output.getvalue()
        assert "CREATE SCHEMA IF NOT EXISTS document" in sql
        assert all(f"CREATE TABLE document.{table}" in sql for table in tables)
        assert "JSONB" in sql
        assert "WHERE is_active = true" in sql
        assert "WHERE published_at IS NULL" in sql
        assert "ON DELETE CASCADE" not in sql
        assert "SUPERSEDED" not in sql
