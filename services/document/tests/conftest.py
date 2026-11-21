import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.common.config import Settings, get_settings
from app.common.core_client import ClassAccess, CoreUser, get_core_client
from app.common.db import get_db
from app.common.errors import AppError
from app.common.storage import LocalVolumeStorage, get_storage
from app.main import create_app
from app.model_registry import Base, DocumentVersion

SERVICE_ROOT = Path(__file__).resolve().parents[1]
INTERNAL_KEY = {"X-Service-Key": "document-test-key"}
API = "/api/v1"
DOCUMENTS = f"{API}/documents"
SECRET = "document-test-key"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def class_documents(class_id):
    return f"{API}/classes/{class_id}/documents"


def internal_grants(class_id):
    return f"/internal/v1/classes/{class_id}/documents/active-grants"


class FakeCoreClient:
    def __init__(self):
        self.school_id, self.other_school_id = uuid4(), uuid4()
        self.class_id, self.second_class_id = uuid4(), uuid4()
        self.users = {
            token: CoreUser(
                id=uuid4(),
                status="ACTIVE",
                roles=[role],
                school_id=school,
                display_name=token,
            )
            for token, role, school in (
                ("primary", "LECTURER", self.school_id),
                ("assisting", "LECTURER", self.school_id),
                ("other-school-teacher", "LECTURER", self.other_school_id),
                ("student", "STUDENT", self.school_id),
                ("other-student", "STUDENT", self.school_id),
                ("new-student", "STUDENT", self.school_id),
                ("outsider", "STUDENT", self.other_school_id),
                ("admin", "ADMIN", self.school_id),
                ("schoolless", "LECTURER", None),
            )
        }
        self.class_status = {self.class_id: "ACTIVE", self.second_class_id: "ACTIVE"}
        self.assignments = {
            (class_id, self.users[token].id): role
            for class_id in self.class_status
            for token, role in (("primary", "PRIMARY"), ("assisting", "ASSISTING"))
        }
        self.members = {
            (class_id, self.users[token].id)
            for class_id in self.class_status
            for token in ("student", "other-student")
        }
        self.history = self.members.copy()
        self.members.add((self.class_id, self.users["new-student"].id))
        self.revoked = set()
        self.outage = False
        self.access_outage = False
        self.introspections = []
        self.access_checks = []

    def introspect_token(self, token):
        self.introspections.append(token)
        if self.outage:
            raise AppError(503)
        if token in self.revoked or token not in self.users:
            raise AppError(401)
        return self.users[token]

    def check_class_access(self, class_id, user_id, action):
        self.access_checks.append((class_id, user_id, action))
        if self.outage or self.access_outage:
            raise AppError(503)
        user = next(user for user in self.users.values() if user.id == user_id)
        status = self.class_status.get(class_id) if user.school_id == self.school_id else None
        if status is None:
            return ClassAccess(allowed=False, class_id=class_id)
        assignment = self.assignments.get((class_id, user_id))
        if action == "manage_class_documents":
            allowed = status == "ACTIVE" and assignment is not None
        elif action == "read_document_source":
            allowed = assignment is not None or (class_id, user_id) in self.members
        else:
            allowed = (class_id, user_id) in self.history
        return ClassAccess(
            allowed=allowed,
            class_id=class_id,
            class_status=status,
            school_id=user.school_id if allowed else None,
            teaching_role=assignment,
        )


@pytest.fixture(scope="session")
def postgres():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        yield None
        return
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    engine = create_engine(Settings(database_url=url).database_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            version = int(connection.scalar(text("SHOW server_version_num")))
            if not 160000 <= version < 170000:
                pytest.fail("TEST_DATABASE_URL must use PostgreSQL 16.", pytrace=False)
            if inspect(connection).has_schema("document"):
                pytest.fail("Use a disposable database without a document schema.", pytrace=False)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
            connection.execute(text("DROP TABLE document.alembic_version"))
            connection.execute(text("DROP SCHEMA document"))
    finally:
        engine.dispose()


@pytest.fixture
def database(postgres):
    if postgres is not None:
        with postgres.connect() as connection, connection.begin() as transaction:
            yield connection
            transaction.rollback()
        return

    sqlite = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"document": None}},
    )

    @event.listens_for(sqlite, "connect")
    def enforce_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(sqlite)
    try:
        yield sqlite
    finally:
        sqlite.dispose()


@pytest.fixture
def db_session(database):
    factory = sessionmaker(
        bind=database,
        autoflush=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    @contextmanager
    def session():
        with factory() as db:
            yield db

    return session


@pytest.fixture
def core():
    return FakeCoreClient()


@pytest.fixture
def storage(tmp_path):
    return LocalVolumeStorage(tmp_path / "volume")


@pytest.fixture
def client(monkeypatch, db_session, core, storage):
    monkeypatch.setenv("INTERNAL_SERVICE_SECRET", SECRET)
    get_settings.cache_clear()
    app = create_app()

    def override_db():
        with db_session() as session:
            yield session

    app.dependency_overrides.update(
        {get_db: override_db, get_core_client: lambda: core, get_storage: lambda: storage}
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        get_settings.cache_clear()


@pytest.fixture
def upload(client):
    def create(
        filename="lesson.txt",
        content=b"Teaching material",
        mime="text/plain",
        *,
        token="primary",
        title="Bai hoc",
        description=None,
    ):
        fields = {"title": title}
        if description is not None:
            fields["description"] = description
        return client.post(
            DOCUMENTS, data=fields, files={"file": (filename, content, mime)}, headers=bearer(token)
        )

    return create


@pytest.fixture
def set_version_state(db_session, storage):
    def set_state(version_id, status="SUCCESS", text="Canonical lesson"):
        with db_session() as session:
            version = session.get(DocumentVersion, UUID(version_id))
            assert version is not None
            version.processing_status = status
            if status == "SUCCESS":
                canonical = {
                    "schema_version": "idp-canonical-1",
                    "pages": [
                        {
                            "page_number": 1,
                            "blocks": [
                                {"block_id": "heading", "type": "SectionHeader", "text": "Lesson"},
                                {"type": "Text", "text": text},
                                {"type": "Formula", "latex": ["x^2"]},
                                {"type": "Text", "text": "Excluded", "excluded": True},
                            ],
                        }
                    ],
                }
                version.canonical_schema_version = canonical["schema_version"]
                version.canonical_storage_key = f"canonical/{version.id}.json"
                version.canonical_sha256 = storage.save_bytes(
                    version.canonical_storage_key, json.dumps(canonical).encode()
                )
                version.acceptance_mode = "AUTO_PUBLISHED"
            session.commit()
            return version

    return set_state


@pytest.fixture
def publish(upload, set_version_state):
    def published(token="primary", **kwargs):
        response = upload(token=token, **kwargs)
        assert response.status_code == 201, response.text
        document = response.json()
        set_version_state(document["latest_version"]["id"])
        return document

    return published


@pytest.fixture
def shared(client, core, publish):
    document = publish()
    response = client.post(
        f"{class_documents(core.class_id)}/grants",
        json={"document_id": document["id"]},
        headers=bearer("primary"),
    )
    assert response.status_code == 201, response.text
    return document
