import builtins
import json
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import (
    INTERNAL,
    JWT_KEY,
    publish_assessment,
    publish_assignment,
    start_attempt,
    submit_assignment,
    submit_attempt,
    upload_pdf,
)
from fastapi.testclient import TestClient
from pydantic import SecretStr
from rag.generation.tasks.quiz import review
from sqlalchemy import JSON, CheckConstraint, DefaultClause, create_engine, event, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from app.background_worker import LEARNING_NOTIFICATION_EVENTS, run_worker_cycle
from app.common.db import Database, utcnow
from app.common.outbox import OutboxEvent
from app.config import Settings
from app.integrations.ai_client import HttpAiClient
from app.integrations.core_client import CoreClient
from app.main import create_app
from app.modules.appeals import service as appeals
from app.modules.grading import service as grading

SERVICES = Path(__file__).resolve().parents[2]
SERVICE_ROOTS = {f"wire_{name}": SERVICES / name / "app" for name in ("ai", "core")}
CORE_KEY = "core-cross-service-fixture-key-12345"
AI_KEY = "ai-inbound-cross-service-fixture-key"
LEARNING_EVENT_PATH = "/internal/v1/notifications/learning-events"


class ServiceLoader(SourceFileLoader):
    def exec_module(self, module):
        namespace = module.__name__.split(".")[0]

        def service_import(name, globals=None, locals=None, fromlist=(), level=0):
            if not level and (name == "app" or name.startswith("app.")):
                name = namespace + name[3:]
            return builtins.__import__(name, globals, locals, fromlist, level)

        module.__dict__["__builtins__"] = vars(builtins) | {"__import__": service_import}
        super().exec_module(module)


class SiblingServices:
    """Separate the three top-level app packages without modifying any service source."""

    def find_spec(self, fullname, path=None, target=None):
        root, *parts = fullname.split(".")
        if root not in SERVICE_ROOTS:
            return None
        location = SERVICE_ROOTS[root].joinpath(*parts)
        file = location / "__init__.py" if location.is_dir() else location.with_suffix(".py")
        if file.is_file():
            return spec_from_file_location(
                fullname, file, loader=ServiceLoader(fullname, str(file))
            )
        return None


@pytest.fixture
def sibling_services(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [SiblingServices(), *sys.meta_path])
    yield
    for name in tuple(sys.modules):
        if name.split(".")[0] in SERVICE_ROOTS:
            del sys.modules[name]


def create_wire_tables(engine, metadata, models):
    tables = [model.__table__ for model in models]
    for table in tables:
        for constraint in list(table.constraints):
            if isinstance(constraint, CheckConstraint):
                sql = str(constraint.sqltext)
                if "~" in sql or "jsonb_typeof(" in sql:
                    table.constraints.discard(constraint)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = column.type.with_variant(
                    JSON(none_as_null=column.type.none_as_null), "sqlite"
                )
            if column.server_default is not None:
                default_sql = str(getattr(column.server_default, "arg", ""))
                if "::jsonb" in default_sql or "gen_random_uuid()" in default_sql:
                    column.server_default = None
        for index in table.indexes:
            index.dialect_options["sqlite"]["where"] = index.dialect_options["postgresql"]["where"]
    metadata.create_all(engine, tables=tables)


@pytest.fixture
def core_server(sibling_services, monkeypatch, tmp_path):
    core_db = import_module("wire_core.common.db")
    core_main = import_module("wire_core.main")
    classroom = import_module("wire_core.modules.classroom.models")
    identity = import_module("wire_core.modules.identity.models")
    security = import_module("wire_core.common.security")
    calendar = import_module("wire_core.modules.social.calendar.models")
    outbox = import_module("wire_core.common.outbox")
    notifications = import_module("wire_core.modules.notification.models")
    worker = import_module("wire_core.modules.notification.worker")
    monkeypatch.setattr(core_db.settings, "secret_key", SecretStr(JWT_KEY))
    monkeypatch.setattr(core_db.settings, "internal_service_secret", SecretStr(CORE_KEY))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'core.sqlite'}",
        execution_options={"schema_translate_map": {"core": None}},
    )
    # Emulate PostgreSQL-specific defaults/functions in this serial ASGI fixture.
    # Core's PostgreSQL suite covers the actual locks and concurrent delivery.
    sequence = count(1)

    @event.listens_for(engine, "connect")
    def postgres_functions(connection, _):
        connection.create_function(
            "clock_timestamp", 0, lambda: utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        )
        connection.create_function("pg_advisory_xact_lock", 1, lambda _: None)
        connection.create_function("next_notification_sequence", 0, lambda: next(sequence))

    notifications.Notification.__table__.c.sequence.server_default = DefaultClause(
        text("(next_notification_sequence())")
    )
    create_wire_tables(
        engine,
        core_db.Base.metadata,
        (
            classroom.School,
            classroom.AcademicYear,
            classroom.Semester,
            classroom.Course,
            classroom.HomeroomClass,
            classroom.SubjectClass,
            identity.User,
            identity.RoleAssignment,
            classroom.TeachingAssignment,
            classroom.ClassMembership,
            calendar.CalendarEvent,
            outbox.OutboxEvent,
            notifications.Notification,
        ),
    )
    core_db.SessionLocal.configure(bind=engine)
    school_id, class_id, year_id, term_id, course_id = (uuid4() for _ in range(5))
    primary, assisting, student = (
        SimpleNamespace(id=uuid4(), role=role) for role in ("LECTURER", "LECTURER", "STUDENT")
    )
    with core_db.SessionLocal.begin() as db:
        db.add_all(
            [
                classroom.School(id=school_id, name="Wire school"),
                classroom.AcademicYear(id=year_id, school_id=school_id, label="2026"),
                classroom.Semester(id=term_id, academic_year_id=year_id, name="First term"),
                classroom.Course(id=course_id, school_id=school_id, code="MATH", name="Math"),
                classroom.SubjectClass(
                    id=class_id,
                    school_id=school_id,
                    academic_year_id=year_id,
                    semester_id=term_id,
                    course_id=course_id,
                    name="Geometry",
                    grade_level=9,
                    enrollment_mode="MANUAL",
                ),
            ]
        )
        for name, user in (("Primary", primary), ("Assisting", assisting), ("Student", student)):
            db.add(
                identity.User(
                    id=user.id,
                    status="ACTIVE",
                    school_id=school_id,
                    display_name=name,
                    password_hash="fixture",
                )
            )
            db.add(identity.RoleAssignment(user_id=user.id, role=user.role))
        db.add_all(
            [
                classroom.TeachingAssignment(
                    class_id=class_id, lecturer_id=primary.id, role="PRIMARY", assigned_at=utcnow()
                ),
                classroom.TeachingAssignment(
                    class_id=class_id,
                    lecturer_id=assisting.id,
                    role="ASSISTING",
                    assigned_at=utcnow(),
                ),
                classroom.ClassMembership(
                    class_id=class_id,
                    student_id=student.id,
                    source="MANUAL",
                    status="ACTIVE",
                    joined_at=utcnow(),
                ),
            ]
        )
    try:
        yield SimpleNamespace(
            app=core_main.app,
            school_id=school_id,
            class_id=class_id,
            primary=primary,
            assisting=assisting,
            student=student,
            token=security.create_access_token,
            sessions=core_db.SessionLocal,
            users=identity.User,
            memberships=classroom.ClassMembership,
            outbox=outbox.OutboxEvent,
            drain=worker.drain_outbox,
        )
    finally:
        core_db.SessionLocal.configure(bind=core_db.engine)
        engine.dispose()
        core_db.engine.dispose()


@dataclass
class Wire:
    client: TestClient
    core: SimpleNamespace
    ai: SimpleNamespace
    ai_responses: list[httpx.Response]
    learning_responses: list[httpx.Response]
    core_responses: list[httpx.Response]
    core_http: httpx.AsyncClient
    ai_http: httpx.AsyncClient
    database: Database
    core_client: CoreClient

    @property
    def base(self):
        return f"/api/v1/classes/{self.core.class_id}"

    @property
    def student(self):
        return self.core.student

    @property
    def primary(self):
        return self.core.primary

    @property
    def assisting(self):
        return self.core.assisting

    def call(self, method, path, user, *, expected=200, client=None, **kwargs):
        token = self.core.token(user.id, [user.role], 1)
        headers = {"Authorization": f"Bearer {token}"}
        if client is None:
            response = self.client.request(method, path, headers=headers, **kwargs)
        else:
            response = self.run(client.request, method, path, headers=headers, **kwargs)
        assert response.status_code == expected, response.text
        return response

    def run(self, method, *args, **kwargs):
        return self.client.portal.call(lambda: method(*args, **kwargs))


@pytest.fixture
def wire(core_server, database, tmp_path):
    ai_main = import_module("wire_ai.main")
    ai_config = import_module("wire_ai.config")
    ai_integration = import_module("wire_ai.integrations.learning_client")
    ai_core = import_module("wire_ai.integrations.core_client")
    ai_document = import_module("wire_ai.integrations.document_client")
    ai_db = import_module("wire_ai.common.db")
    ai_tutor = import_module("wire_ai.modules.tutor.models")
    ai_quiz = import_module("wire_ai.modules.quiz.models")
    ai_runtime_models = import_module("wire_ai.runtime.models")
    ai_worker = import_module("wire_ai.background_worker")
    ai_errors = import_module("wire_ai.common.errors")
    ai_settings = ai_config.Settings(
        ai_database_url=None,
        ai_internal_service_secret=AI_KEY,
        ai_environment="local_dev",
        document_mode="local_dev",
        core_jwt_secret=JWT_KEY,
        core_api_url="http://core.test",
        core_service_key=CORE_KEY,
        llama_mode="disabled",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_service_key=INTERNAL["X-Service-Key"],
        learning_active_assessment_path="/internal/v1/assessment-lock",
        learning_quiz_export_path="/internal/v1/quiz-items/import",
    )
    settings = Settings(
        core_jwt_secret=JWT_KEY,
        core_api_url="http://core.test",
        core_service_key=CORE_KEY,
        learning_internal_service_secret=INTERNAL["X-Service-Key"],
        ai_mode="http",
        ai_api_url="http://ai.test",
        ai_service_key=AI_KEY,
        blob_storage_dir=tmp_path / "wire-blobs",
    )
    document = ai_document.LocalDevDocumentClient(
        {
            (core_server.school_id, core_server.class_id): ai_document.GrantSnapshot(
                class_id=core_server.class_id, school_id=core_server.school_id, grants=()
            )
        },
        {},
    )
    ai_app = ai_main.create_app(ai_settings, local_document=document)
    ai_responses, learning_responses, core_responses = [], [], []

    def capture(responses):
        async def read(response):
            await response.aread()
            responses.append(response)

        return read

    with ExitStack() as stack:
        stack.enter_context(TestClient(ai_app))
        ai_engine = create_engine(
            f"sqlite:///{tmp_path / 'ai.sqlite'}",
            connect_args={"check_same_thread": False},
            execution_options={"schema_translate_map": {"ai": None}},
        )
        stack.callback(ai_engine.dispose)
        create_wire_tables(
            ai_engine,
            ai_db.Base.metadata,
            (
                ai_tutor.TutorConfigRow,
                ai_tutor.Conversation,
                ai_tutor.Message,
                ai_quiz.QuizJob,
                ai_quiz.QuizItemDraft,
                ai_runtime_models.GenerationProvenance,
            ),
        )
        ai_app.state.database = SimpleNamespace(SessionLocal=sessionmaker(bind=ai_engine))
        core_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=core_server.app),
            base_url="http://core.test",
            event_hooks={"response": [capture(core_responses)]},
        )
        ai_app.state.core = ai_core.CoreClient(core_http, ai_settings)
        ai_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ai_app),
            base_url="http://ai.test",
            event_hooks={"response": [capture(ai_responses)]},
        )
        core_client = CoreClient(core_http, settings)
        learning_app = create_app(
            settings,
            database=database,
            core=core_client,
            ai=HttpAiClient(ai_http, settings),
        )
        client = stack.enter_context(TestClient(learning_app))
        learning_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=learning_app),
            base_url="http://learning.test",
            event_hooks={"response": [capture(learning_responses)]},
        )
        for http in (core_http, ai_http, learning_http):
            stack.callback(client.portal.call, http.aclose)
        ai_app.state.learning = ai_integration.HttpLearningClient(learning_http, ai_settings)
        yield Wire(
            client,
            core_server,
            SimpleNamespace(
                app=ai_app,
                http=ai_http,
                db=ai_app.state.database,
                client=ai_app.state.learning,
                document=ai_document,
                quiz=ai_quiz,
                tutor=ai_tutor,
                worker=ai_worker,
                receipt=ai_integration.QuizExportReceipt,
                error=ai_errors.AppError,
            ),
            ai_responses,
            learning_responses,
            core_responses,
            core_http,
            ai_http,
            database,
            core_client,
        )


def test_learning_core_calendar_deadline_wire(wire):
    assessment_path, _ = publish_assessment(wire)
    assignment_path, _ = publish_assignment(wire)
    correlation_id = str(uuid4())
    counts = wire.run(run_worker_cycle, wire.database, wire.core_client, correlation_id)
    assert counts == {
        "expired_attempts": 0,
        "deadlines_sent": 2,
        "deadlines_failed": 0,
        "notifications_sent": 0,
        "notifications_failed": 0,
    }
    responses = [
        response
        for response in wire.core_responses
        if response.request.url.path == "/internal/v1/calendar/learning-deadlines"
    ]
    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.request.method == "PUT" for response in responses)
    assert all(response.headers["x-correlation-id"] == correlation_id for response in responses)
    events = wire.call("GET", wire.base + "/calendar", wire.student, client=wire.core_http).json()
    assert {item["id"] for item in events["items"]} == {
        response.json()["event_id"] for response in responses
    }
    assert {item["origin"] for item in events["items"]} == {"LEARNING"}
    assert {item["external_ref"] for item in events["items"]} == {
        f"learning:activity:{path.rsplit('/', 1)[-1]}"
        for path in (assessment_path, assignment_path)
    }


def test_learning_core_notification_delivery_and_lost_ack_retry(wire, monkeypatch):
    path, _ = publish_assessment(wire)
    attempt, started = start_attempt(wire, path)
    submit_attempt(wire, attempt)
    later = utcnow() + timedelta(hours=2)
    monkeypatch.setattr(grading, "utcnow", lambda: later)
    monkeypatch.setattr(appeals, "utcnow", lambda: later)
    assert wire.call("POST", path + "/release-results", wire.primary, json={}).json() == {
        "released_count": 1,
    }
    grade = wire.call("GET", attempt + "/result", wire.student).json()["effective_grade"]
    proposal = wire.call(
        "POST",
        f"/api/v1/grades/{grade['grade_id']}/revisions",
        wire.assisting,
        expected=201,
        json={
            "expected_grade_version": grade["grade_version"],
            "total_score": 4,
            "reason": "Private grade review",
        },
    ).json()
    appeal_path = wire.base + "/appeals"
    appeal = wire.call(
        "POST",
        appeal_path,
        wire.student,
        expected=201,
        json={
            "target_type": "ASSESSMENT_ATTEMPT",
            "target_id": started["attempt_id"],
            "reason": "Private appeal reason",
        },
    ).json()
    result = wire.call(
        "POST",
        f"{appeal_path}/{appeal['id']}/resolve",
        wire.primary,
        json={"resolution_note": "Private appeal response"},
    ).json()
    assert result["status"] == "RESOLVED"

    async def drop_ack(response):
        if response.request.url.path == LEARNING_EVENT_PATH and response.status_code == 202:
            raise httpx.ReadError("Lost response after Core committed", request=response.request)

    wire.core_http.event_hooks["response"].append(drop_ack)
    correlation_id = str(uuid4())
    first = wire.run(run_worker_cycle, wire.database, wire.core_client, correlation_id)
    assert first["deadlines_sent"] == 1
    assert (first["notifications_sent"], first["notifications_failed"]) == (0, 3)
    with wire.core.sessions.begin() as session:
        membership = session.scalar(
            select(wire.core.memberships).where(
                wire.core.memberships.class_id == wire.core.class_id,
                wire.core.memberships.student_id == wire.student.id,
            )
        )
        membership.status, membership.ended_at = "ENDED", utcnow()
    assert wire.core.drain(session_factory=wire.core.sessions) == 4

    with wire.database.SessionLocal.begin() as session:
        events = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type.in_(LEARNING_NOTIFICATION_EVENTS))
            )
        )
        assert len(events) == 3
        for event in events:
            assert event.status == "PENDING" and event.retry_count == 1
            assert event.next_retry_at is not None and event.sent_at is None
            event.next_retry_at = utcnow() - timedelta(seconds=1)

    retry = wire.run(run_worker_cycle, wire.database, wire.core_client, correlation_id)
    assert (retry["notifications_sent"], retry["notifications_failed"]) == (3, 0)
    assert wire.core.drain(session_factory=wire.core.sessions) == 0
    with wire.database.SessionLocal() as session:
        rows = session.scalars(
            select(OutboxEvent).where(OutboxEvent.event_type.in_(LEARNING_NOTIFICATION_EVENTS))
        )
        assert all(
            row.status == "SENT" and row.sent_at and row.next_retry_at is None for row in rows
        )

    forwarded = [r for r in wire.core_responses if r.request.url.path == LEARNING_EVENT_PATH]
    assert [r.status_code for r in forwarded] == [202, 202, 202, 200, 200, 200]
    accepted = json.loads(forwarded[0].request.content)
    for field, status in (("school_id", 404), ("class_id", 404), ("resource_id", 409)):
        rejected = wire.run(
            wire.core_http.post,
            LEARNING_EVENT_PATH,
            json={**accepted, field: str(uuid4())},
            headers={"X-Service-Key": CORE_KEY},
        )
        assert rejected.status_code == status
    expected_ids = {
        "ASSESSMENT_RESULT_RELEASED": ("assessment_attempt", started["attempt_id"]),
        "GRADE_PROPOSAL_PENDING": ("grade_revision", proposal["id"]),
        "APPEAL_RESOLVED": ("appeal", appeal["id"]),
    }
    expected_inbox = {}
    with wire.core.sessions() as session:
        for response in forwarded:
            body = json.loads(response.request.content)
            event_type = body["event_type"]
            resource_type, resource_id = expected_ids[event_type]
            assert body == {
                "source_event_id": response.json()["outbox_event_id"],
                "event_type": event_type,
                "school_id": str(wire.core.school_id),
                "class_id": str(wire.core.class_id),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "student_id": None
                if event_type == "GRADE_PROPOSAL_PENDING"
                else str(wire.student.id),
            }
            row = session.get(wire.core.outbox, UUID(body["source_event_id"]))
            assert row.published_at is not None
            assert (row.aggregate_type, row.aggregate_id) == (resource_type, resource_id)
            assert row.payload_json == {
                key: value
                for key, value in body.items()
                if key not in {"source_event_id", "event_type"} and value is not None
            }
            expected_inbox[event_type] = {
                "class_id": body["class_id"],
                "resource_type": resource_type,
                "resource_id": resource_id,
                "event_id": body["source_event_id"],
                "schema_version": 2,
            }

    for user, event_types in (
        (wire.student, {"ASSESSMENT_RESULT_RELEASED", "APPEAL_RESOLVED"}),
        (wire.primary, {"GRADE_PROPOSAL_PENDING"}),
        (wire.assisting, {"GRADE_PROPOSAL_PENDING"}),
    ):
        inbox = wire.call("GET", "/api/v1/notifications", user, client=wire.core_http).json()
        items = [item for item in inbox["items"] if item["type"] in LEARNING_NOTIFICATION_EVENTS]
        assert len(items) == len(event_types)
        assert {item["type"]: item["payload_json"] for item in items} == {
            event_type: expected_inbox[event_type] for event_type in event_types
        }


def test_ai_core_tutor_config_and_learning_d6_lock_wire(wire):
    config_path = wire.base + "/tutor/config"
    defaults = wire.call("GET", config_path, wire.primary, client=wire.ai_http).json()
    assert defaults["version"] is None
    values = {key: value for key, value in defaults.items() if key != "version"}
    values["max_answer_words"] = 95
    body = {**values, "expected_version": None}
    updated = wire.call("PUT", config_path, wire.primary, client=wire.ai_http, json=body).json()
    assert updated == {**values, "version": 1}
    assert wire.call("GET", config_path, wire.primary, client=wire.ai_http).json() == updated
    assert any(
        r.request.url.path == "/api/v1/users/me" and r.status_code == 200
        for r in wire.core_responses
    )
    assert any(
        r.request.url.path == f"/internal/v1/classes/{wire.core.class_id}/access"
        and r.json()["teaching_role"] == "PRIMARY"
        for r in wire.core_responses
    )
    wire.call("GET", config_path, wire.student, client=wire.ai_http, expected=403)
    wire.call("PUT", config_path, wire.student, client=wire.ai_http, expected=403, json=body)

    path, _ = publish_assessment(wire)
    attempt, _ = start_attempt(wire, path)
    conversation = wire.base + "/tutor/conversations"
    body = {"title": "Geometry practice"}
    wire.call("POST", conversation, wire.student, client=wire.ai_http, expected=403, json=body)
    assert wire.learning_responses[-1].request.url.path == "/internal/v1/assessment-lock"
    assert wire.learning_responses[-1].json()["active_official_assessment"] is True
    submit_attempt(wire, attempt)
    created = wire.call(
        "POST", conversation, wire.student, client=wire.ai_http, expected=201, json=body
    )
    assert wire.learning_responses[-1].json()["active_official_assessment"] is False
    assert UUID(created.json()["id"])
    assert any(
        r.request.url.params.get("action") == "use_ai_tutor" and r.json()["allowed"]
        for r in wire.core_responses
    )


def test_ai_learning_d6_lock_and_quiz_export_wire(wire):
    path, _ = publish_assessment(wire)
    attempt, _ = start_attempt(wire, path)
    correlation = str(uuid4())
    tutor = wire.ai.client.require_tutor_available
    with pytest.raises(wire.ai.error) as blocked:
        wire.run(tutor, wire.student.id, wire.core.school_id, correlation)
    assert blocked.value.status_code == 403
    lock = wire.learning_responses[-1]
    assert lock.request.url.path == "/internal/v1/assessment-lock"
    assert lock.headers["x-correlation-id"] == correlation
    assert lock.json() == {
        "user_id": str(wire.student.id),
        "school_id": str(wire.core.school_id),
        "active_official_assessment": True,
    }
    submit_attempt(wire, attempt)
    assert wire.run(tutor, wire.student.id, wire.core.school_id, correlation) is None
    assert wire.learning_responses[-1].json()["active_official_assessment"] is False

    draft_id = uuid4()
    payload = review.export_payload(
        {
            "id": str(draft_id),
            "status": review.ACCEPTED,
            "level": "easy",
            "item": {
                "type": "true_false_4",
                "context": "Classify the geometry claims",
                "statements": [
                    {"text": f"Claim {index}", "truth": index % 2 == 0} for index in range(4)
                ],
            },
        },
        job={"id": str(uuid4())},
        approved_by=str(wire.primary.id),
    )
    export = wire.ai.client.export_quiz_item
    arguments = (wire.core.class_id, wire.core.school_id, draft_id, payload, correlation)
    first = wire.run(export, *arguments)
    assert isinstance(first, wire.ai.receipt)
    assert (first.class_id, first.school_id, first.idempotency_key) == arguments[:3]
    assert wire.run(export, *arguments) == first
    assert [row.status_code for row in wire.learning_responses[-2:]] == [201, 200]
    assert wire.learning_responses[-1].request.url.path == "/internal/v1/quiz-items/import"
    items = wire.call("GET", wire.base + "/question-bank", wire.primary).json()["items"]
    assert [item["id"] for item in items] == [str(first.learning_item_id)]

    with wire.core.sessions.begin() as db:
        db.get(wire.core.users, wire.primary.id).session_version = 2
    wire.call("GET", wire.base + "/grading", wire.primary, expected=401)


def test_learning_ai_explanation_and_hint_wire_with_disabled_llama(wire):
    practice = wire.call(
        "POST", wire.base + "/practices", wire.primary, expected=201, json={"title": "Geometry"}
    ).json()
    path = wire.base + "/practices/" + practice["practice_id"]
    explanation = "A square has four equal sides."
    question = {
        "id": "q1",
        "type": "single_choice",
        "prompt": "How many equal sides has a square?",
        "options": [{"id": "a", "text": "Four"}, {"id": "b", "text": "Two"}],
        "correctOptionId": "a",
        "explanation": explanation,
    }
    draft = wire.call(
        "PUT", path + "/draft", wire.primary, json={"expected_revision": 1, "questions": [question]}
    ).json()
    reviewed = wire.call(
        "POST",
        path + "/questions/q1/review",
        wire.primary,
        json={"expected_revision": int(draft["revision"]), "approved": True},
    ).json()
    wire.call(
        "POST",
        path + "/publish",
        wire.primary,
        json={"expected_revision": int(reviewed["revision"])},
    )
    started = wire.call(
        "POST", path + "/attempts", wire.student, expected=201, json={"intent": "start"}
    ).json()
    attempt = path + "/attempts/" + started["attempt_id"]
    wire.call(
        "POST",
        attempt + "/check",
        wire.student,
        json={"question_id": "q1", "answer": {"singleChoiceValue": "b"}},
    )
    result = wire.call(
        "POST", attempt + "/explain-mistake", wire.student, json={"question_id": "q1"}
    )
    upstream = wire.ai_responses[-1]
    assert upstream.request.url.path == "/internal/v1/explain-mistake"
    assert upstream.status_code == 200
    assert upstream.json() == {"explanation": explanation, "evidence": [], "fallback": True}
    assert result.json() == {"explanation": explanation, "citations": [], "fallback": True}

    path, assignment = publish_assignment(wire)
    revision = assignment["revision"]
    upload = upload_pdf(wire, path, revision)
    submission = submit_assignment(wire, path, revision, upload["upload_id"])
    grade_path = "/api/v1/submissions/" + submission["submission_id"]
    failed = wire.call("POST", grade_path + "/grade-hint", wire.assisting, expected=503)
    upstream = wire.ai_responses[-1]
    assert upstream.request.url.path == "/internal/v1/assignment/grade-hint"
    assert upstream.status_code == 503
    assert failed.json()["error"]["code"] == "AI_HINT_UNAVAILABLE"
    detail_path = path + f"/submissions/{submission['id']}/grading"
    assert wire.call("GET", detail_path, wire.assisting).json()["submission"]["grade"] is None
    grade = wire.call(
        "POST",
        grade_path + "/grade",
        wire.assisting,
        json={"expected_grade_version": 0, "score": 8, "feedback": "Good proof"},
    ).json()
    assert wire.call("GET", detail_path, wire.assisting).json()["submission"]["grade"] == grade


def test_ai_worker_retries_pending_export_into_learning_and_ended_student_reads_history(wire):
    # 1. Seed granted source on AI document client and insert an EXPORT_PENDING quiz draft
    doc_id, version_id = uuid4(), uuid4()
    job_id, draft_id = uuid4(), uuid4()
    wire.ai.app.state.document = wire.ai.document.LocalDevDocumentClient(
        {
            (wire.core.school_id, wire.core.class_id): wire.ai.document.GrantSnapshot(
                class_id=wire.core.class_id,
                school_id=wire.core.school_id,
                grants=(
                    wire.ai.document.ActiveGrant(
                        document_id=doc_id,
                        document_version_id=version_id,
                        source_id="textbook-source",
                    ),
                ),
            )
        },
        {},
    )
    with wire.ai.db.SessionLocal.begin() as ai_db:
        ai_db.add(
            wire.ai.quiz.QuizJob(
                id=job_id,
                school_id=wire.core.school_id,
                class_id=wire.core.class_id,
                requested_by=wire.primary.id,
                status=wire.ai.quiz.JobStatus.COMPLETED,
                spec_json={"purpose": "PRACTICE", "grade": 8, "scope": []},
                completed_items=1,
                total_items=1,
            )
        )
        ai_db.add(
            wire.ai.quiz.QuizItemDraft(
                id=draft_id,
                quiz_job_id=job_id,
                slot_id="r0-0",
                status=wire.ai.quiz.DraftStatus.EXPORT_PENDING,
                level="biet",
                seed=1,
                reviewer_id=wire.primary.id,
                item_json={
                    "type": "single_choice",
                    "stem": "Hình vuông có mấy cạnh bằng nhau?",
                    "correct_answer": "4",
                    "distractors": [{"text": "3"}, {"text": "5"}, {"text": "2"}],
                    "explanation": "Hình vuông có bốn cạnh bằng nhau.",
                    "evidence": [
                        {
                            "source_id": "textbook-source",
                            "source_spans": [{"start": 0, "end": 12}],
                        }
                    ],
                },
            )
        )

    counts = wire.run(
        wire.ai.worker.run_worker_cycle,
        wire.ai.db,
        wire.ai.app.state.runtime,
        wire.ai.app.state.core,
        wire.ai.app.state.document,
        wire.ai.client,
        str(uuid4()),
    )
    assert counts["exports_sent"] == 1
    with wire.ai.db.SessionLocal() as ai_db:
        exported = ai_db.get(wire.ai.quiz.QuizItemDraft, draft_id)
        assert exported.status == wire.ai.quiz.DraftStatus.EXPORTED
        item_id = str(exported.learning_item_id)
    bank = wire.call("GET", wire.base + "/question-bank", wire.primary).json()["items"]
    assert any(item["id"] == item_id for item in bank)

    # 2. Seed student's AI conversation & message, then end enrollment in Core:
    # owner may still read history via Core READ_OWN_HISTORY while blocked from sending new turns.
    conv_id = uuid4()
    with wire.ai.db.SessionLocal.begin() as ai_db:
        ai_db.add(
            wire.ai.tutor.Conversation(
                id=conv_id,
                user_id=wire.student.id,
                class_id=wire.core.class_id,
                school_id=wire.core.school_id,
                title="Ôn tập cũ",
                default_mode=wire.ai.tutor.TutorMode.QUICK_QA,
            )
        )
        ai_db.add(
            wire.ai.tutor.Message(
                id=uuid4(),
                conversation_id=conv_id,
                role=wire.ai.tutor.MessageRole.USER,
                mode=wire.ai.tutor.TutorMode.QUICK_QA,
                status=wire.ai.tutor.MessageStatus.COMPLETED,
                content="Câu hỏi cũ",
            )
        )
    with wire.core.sessions.begin() as core_db:
        membership = core_db.scalar(
            select(wire.core.memberships).where(
                wire.core.memberships.class_id == wire.core.class_id,
                wire.core.memberships.student_id == wire.student.id,
            )
        )
        membership.status = "ENDED"

    convs = wire.call(
        "GET",
        f"/api/v1/classes/{wire.core.class_id}/tutor/conversations",
        wire.student,
        client=wire.ai.http,
    ).json()
    assert [row["id"] for row in convs] == [str(conv_id)]
    msgs = wire.call(
        "GET",
        f"/api/v1/tutor/conversations/{conv_id}/messages",
        wire.student,
        client=wire.ai.http,
    ).json()
    assert msgs[0]["content"] == "Câu hỏi cũ"
    denied = wire.run(
        wire.ai.http.post,
        f"/api/v1/tutor/conversations/{conv_id}/messages",
        headers={
            "Authorization": f"Bearer {wire.core.token(wire.student.id, [wire.student.role], 1)}"
        },
        json={"text": "Hỏi thêm"},
    )
    assert denied.status_code in (403, 404)


def test_learning_document_http_active_grants_wire_and_fault_isolation(wire, monkeypatch):
    from app.integrations.document_client import HttpDocumentClient, optional_grants

    doc_id, version_id = uuid4(), uuid4()
    correlation_id = str(uuid4())
    captured = {}
    mode = "ok"

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["key"] = request.headers.get("x-service-key")
        if mode == "cross_school":
            return httpx.Response(
                200,
                json={
                    "class_id": str(wire.core.class_id),
                    "items": [
                        {
                            "document_id": str(doc_id),
                            "school_id": str(uuid4()),
                            "title": "Wrong school doc",
                            "format": "PDF",
                            "serving_version_id": str(version_id),
                            "serving_version_number": 1,
                        }
                    ],
                },
            )
        if mode == "down":
            return httpx.Response(503, json={"detail": "down"})
        return httpx.Response(
            200,
            json={
                "class_id": str(wire.core.class_id),
                "items": [
                    {
                        "document_id": str(doc_id),
                        "school_id": str(wire.core.school_id),
                        "title": "Đề cương Toán 9",
                        "format": "PDF",
                        "serving_version_id": str(version_id),
                        "serving_version_number": 3,
                        "canonical_schema_version": "v1",
                        "canonical_sha256": "deadbeef",
                    }
                ],
            },
        )

    doc_http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://document.test"
    )
    doc_settings = Settings(
        document_mode="http",
        document_api_url="http://document.test",
        document_service_key="doc-wire-secret",
    )
    client = HttpDocumentClient(doc_http, doc_settings)
    monkeypatch.setattr(wire.client.app.state, "document", client)
    try:
        practice = wire.call(
            "POST", wire.base + "/practices", wire.primary, expected=201, json={"title": "Wire Doc"}
        ).json()
        editor = wire.call(
            "GET",
            f"{wire.base}/practices/{practice['practice_id']}/editor",
            wire.primary,
        ).json()
        assert captured == {
            "path": f"/internal/v1/classes/{wire.core.class_id}/documents/active-grants",
            "params": {"school_id": str(wire.core.school_id)},
            "key": "doc-wire-secret",
        }
        assert editor["editor"]["availableClassSources"] == [
            {
                "id": str(doc_id),
                "title": "Đề cương Toán 9",
                "version": 3,
                "isReady": True,
                "documentVersionId": str(version_id),
                "sourceId": str(version_id),
            }
        ]

        mode = "cross_school"
        assert (
            wire.run(optional_grants, client, wire.core.class_id, wire.core.school_id, correlation_id)
            is None
        )
        mode = "down"
        degraded = wire.call(
            "GET",
            f"{wire.base}/practices/{practice['practice_id']}/editor",
            wire.primary,
        ).json()
        assert degraded["editor"]["availableClassSources"] == []
    finally:
        wire.run(doc_http.aclose)


def test_learning_practice_published_and_deadline_kind_notifications_wire(wire):
    publish_assessment(wire)
    publish_assignment(wire)
    practice = wire.call(
        "POST", wire.base + "/practices", wire.primary, expected=201, json={"title": "Hàm số bậc hai"}
    ).json()
    path = wire.base + "/practices/" + practice["practice_id"]
    question = {
        "id": "q1",
        "type": "single_choice",
        "prompt": "1 + 1 bằng mấy?",
        "options": [{"id": "a", "text": "2"}, {"id": "b", "text": "3"}],
        "correctOptionId": "a",
        "explanation": "1 + 1 = 2.",
    }
    draft = wire.call(
        "PUT", path + "/draft", wire.primary, json={"expected_revision": 1, "questions": [question]}
    ).json()
    reviewed = wire.call(
        "POST",
        path + "/questions/q1/review",
        wire.primary,
        json={"expected_revision": int(draft["revision"]), "approved": True},
    ).json()
    wire.call(
        "POST",
        path + "/publish",
        wire.primary,
        json={"expected_revision": int(reviewed["revision"])},
    )

    counts = wire.run(run_worker_cycle, wire.database, wire.core_client, str(uuid4()))
    assert counts["deadlines_sent"] == 2
    assert counts["notifications_sent"] == 1
    assert wire.core.drain(session_factory=wire.core.sessions) == 3

    student_inbox = wire.call("GET", "/api/v1/notifications", wire.student, client=wire.core_http).json()
    teacher_inbox = wire.call("GET", "/api/v1/notifications", wire.primary, client=wire.core_http).json()

    practice_notifs = [item for item in student_inbox["items"] if item["type"] == "PRACTICE_PUBLISHED"]
    assert len(practice_notifs) == 1
    assert practice_notifs[0]["payload_json"]["resource_id"] == practice["practice_id"]
    assert practice_notifs[0]["payload_json"]["resource_type"] == "practice"
    assert not any(item["type"] == "PRACTICE_PUBLISHED" for item in teacher_inbox["items"])

    deadline_kinds = {
        item["payload_json"].get("activity_kind")
        for item in student_inbox["items"]
        if item["type"] == "CALENDAR_DEADLINE_CHANGED"
    }
    assert deadline_kinds == {"ASSESSMENT", "ASSIGNMENT"}
