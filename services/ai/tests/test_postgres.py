import asyncio
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from rag.citations.span_mapping import quote_spans
from rag.generation.pipeline import TaskResult
from rag.generation.policy.config import TutorConfig
from rag.indexing.rag_corpus import TextDocument, build_corpus
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.common.db import Database
from app.common.errors import AppError
from app.config import Settings
from app.integrations.core_client import CoreClient, CurrentUser
from app.integrations.document_client import ActiveGrant, GrantSnapshot, LocalDevDocumentClient
from app.integrations.learning_client import HttpLearningClient
from app.main import create_app
from app.model_registry import load_metadata
from app.modules.indexing.models import IndexedDocumentVersion
from app.modules.indexing.service import publish_generation, record_failed_build
from app.modules.quiz.models import DraftStatus, QuizItemDraft, QuizJob
from app.modules.quiz.service import export_draft, review_draft
from app.modules.tutor.models import Conversation, Message, MessageRole, MessageStatus, TutorMode
from app.modules.tutor.service import set_message_terminal_status, stop_turn, store_tutor_config
from app.modules.tutor.streaming import TurnResult, stream_turn
from app.runtime.models import GenerationProvenance
from app.runtime.provenance import save_provenance
from app.runtime.rag_runtime import RagRuntime

AI_HOME = Path(__file__).resolve().parents[1]


def test_offline_migration_only_mentions_ai_schema(monkeypatch):
    monkeypatch.setenv("AI_DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1/test")
    output = StringIO()
    config = Config(AI_HOME / "alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert sql.index("CREATE SCHEMA IF NOT EXISTS ai") < sql.index(
        "CREATE TABLE ai.alembic_version"
    )
    assert "ALTER TABLE ai.quiz_item_drafts ADD COLUMN slot_meta_json" in sql
    for table in load_metadata().tables:
        assert f"CREATE TABLE {table} (" in sql
    assert "core." not in sql and "public." not in sql
    assert all(
        fk.column.table.schema == "ai"
        for table in load_metadata().tables.values()
        for fk in table.foreign_keys
    )


@pytest.fixture(scope="module")
def postgres():
    url = os.getenv("AI_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AI_TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    config = Config(AI_HOME / "alembic.ini")
    other_schema = f"ai_test_neighbor_{uuid4().hex}"
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("AI_DATABASE_URL", url)
        database = Database(Settings())
        with database.engine.begin() as connection:
            if inspect(connection).has_schema("ai"):
                pytest.fail("AI_TEST_DATABASE_URL must point to a fresh database")
            connection.execute(text(f'CREATE SCHEMA "{other_schema}"'))
            connection.execute(text(f'CREATE TABLE "{other_schema}".sentinel (id integer)'))
        try:
            command.upgrade(config, "head")
            yield database, config, other_schema
        finally:
            command.downgrade(config, "base")
            with database.engine.begin() as connection:
                assert inspect(connection).has_table("sentinel", schema=other_schema)
                connection.execute(text(f'DROP TABLE "{other_schema}".sentinel'))
                connection.execute(text(f'DROP SCHEMA "{other_schema}"'))
                connection.execute(text("DROP TABLE IF EXISTS ai.alembic_version"))
                connection.execute(text("DROP SCHEMA IF EXISTS ai"))
            database.close()


def test_online_migration_and_readiness(postgres):
    database, config, neighbor = postgres
    command.check(config)
    with database.engine.connect() as connection:
        assert inspect(connection).get_table_names(schema=neighbor) == ["sentinel"]
        assert sorted(inspect(connection).get_table_names(schema="ai")) == sorted(
            [
                "alembic_version",
                "conversations",
                "generation_provenance",
                "indexed_document_versions",
                "messages",
                "quiz_item_drafts",
                "quiz_jobs",
                "tutor_configs",
            ]
        )
    with TestClient(create_app(Settings())) as client:
        result = client.get("/health/ready")
        assert result.status_code == 200
        assert result.json()["db"] == "connected"


def test_optimistic_tutor_config_and_terminal_status(postgres):
    database, _, _ = postgres
    school_id, class_id, user_id = uuid4(), uuid4(), uuid4()
    values = {
        "class_id": class_id,
        "school_id": school_id,
        "editor_id": user_id,
        "config": TutorConfig(),
    }
    with database.SessionLocal.begin() as session:
        assert store_tutor_config(session, expected_version=None, **values) == 1
    with database.SessionLocal.begin() as session:
        assert store_tutor_config(session, expected_version=1, **values) == 2
    with database.SessionLocal.begin() as session, pytest.raises(AppError) as conflict:
        store_tutor_config(session, expected_version=1, **values)
    assert conflict.value.status_code == 409

    def conversation():
        return Conversation(
            user_id=user_id,
            class_id=class_id,
            school_id=school_id,
            title="Private",
            default_mode=TutorMode.QUICK_QA,
        )

    with database.SessionLocal.begin() as session:
        first, second = conversation(), conversation()
        session.add_all([first, second])
        session.flush()
        message = Message(
            conversation_id=first.id,
            mode=TutorMode.QUICK_QA,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.STREAMING,
        )
        session.add(message)
        session.flush()

    with database.SessionLocal.begin() as session:
        assert not set_message_terminal_status(
            session, second.id, message.id, MessageStatus.STOPPED
        )
        assert set_message_terminal_status(session, first.id, message.id, MessageStatus.STOPPED)
        assert not set_message_terminal_status(
            session, first.id, message.id, MessageStatus.COMPLETED, content="late answer"
        )

    with pytest.raises(IntegrityError), database.SessionLocal.begin() as session:
        session.add(
            Message(
                conversation_id=second.id,
                mode=TutorMode.QUICK_QA,
                role=MessageRole.ASSISTANT,
                status=MessageStatus.STREAMING,
                retry_of_message_id=message.id,
            )
        )


def test_invalid_quiz_approval_and_provenance_redaction(postgres):
    database, _, _ = postgres
    with database.SessionLocal.begin() as session:
        job = QuizJob(class_id=uuid4(), school_id=uuid4(), requested_by=uuid4(), spec_json={})
        session.add(job)
        session.flush()
    with pytest.raises(IntegrityError), database.SessionLocal.begin() as session:
        session.add(
            QuizItemDraft(
                quiz_job_id=job.id,
                slot_id="slot",
                status=DraftStatus.ACCEPTED,
                level="biet",
                item_json={"item": "unreviewed"},
            )
        )

    digest = sha256(b"fixture").hexdigest()
    with database.SessionLocal.begin() as session:
        save_provenance(
            session,
            [
                {
                    "task": "qa.quick",
                    "task_version": "qa-quick-1",
                    "attempt": 1,
                    "outcome": "rejected",
                    "prompt_sha256": digest,
                    "evidence_sha256": digest,
                    "flags": ["private chat"],
                    "error_code": "secret token",
                    "raw_response_sha256": "raw response",
                    "prompt": "private",
                    "completion_tokens": 5,
                    "queue_wait_seconds": 0.03,
                    "sampling": {"seed": 4},
                }
            ],
        )
    with database.SessionLocal() as session:
        saved = session.scalars(select(GenerationProvenance)).one()
        assert saved.flags_json == ["unclassified"] and saved.error_code == "unclassified"
        assert saved.raw_response_sha256 is None
        assert saved.prompt_sha256 == digest and saved.seed == 4 and saved.waited_ms == 30


@pytest.fixture
def fixture_encoder():
    numpy = pytest.importorskip("numpy")

    class Counter:
        identity: ClassVar = {"unit": "tokens", "kind": "test"}

        def count(self, value):
            return len(value.split())

    class Encoder:
        identity: ClassVar = {"kind": "test"}
        counter = Counter()

        def encode(self, values, **kwargs):
            return numpy.tile(numpy.float32([1, 0]), (len(values), 1)), {}

    return Encoder()


def test_failed_and_stale_index_builds_never_replace_ready_generation(
    postgres, tmp_path, fixture_encoder
):
    database, _, _ = postgres
    source = TextDocument("textbook", "# Chapter\n\nPlants convert light into chemical energy.")
    digest = sha256(source.text.encode()).hexdigest()
    version, first, next_generation, failed = (uuid4() for _ in range(4))
    for generation in (first, next_generation):
        build_corpus([source], tmp_path / str(generation), encoder=fixture_encoder)
    (tmp_path / str(failed)).mkdir()

    def publish(generation, previous):
        with database.SessionLocal.begin() as session:
            publish_generation(
                session,
                artifact_root=tmp_path,
                document_version_id=version,
                generation_id=generation,
                source_id=source.source_id,
                source_sha256=digest,
                expected_generation_id=previous,
                encoder=fixture_encoder,
            )

    publish(first, None)
    with pytest.raises(AppError):
        publish(failed, first)
    with database.SessionLocal.begin() as session:
        record_failed_build(session, version, digest)
        assert session.get(IndexedDocumentVersion, version).corpus_generation_id == first
    publish(next_generation, first)
    publish(next_generation, first)  # A retry of the same publication is idempotent.
    with pytest.raises(AppError):
        publish(first, None)
    with database.SessionLocal() as session:
        index = session.get(IndexedDocumentVersion, version)
        assert index.corpus_generation_id == next_generation
        assert Path(index.artifact_path).is_dir()


def test_stopping_stream_never_runs_the_producer(postgres):
    database, _, _ = postgres
    user = CurrentUser(id=uuid4(), school_id=uuid4(), status="ACTIVE", roles=("STUDENT",))
    settings = Settings(
        core_service_key="test-key",
        learning_service_key="test-key",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_active_assessment_path="/internal/test/assessment",
    )
    with database.SessionLocal.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            school_id=user.school_id,
            class_id=uuid4(),
            title="Test",
            default_mode=TutorMode.QUICK_QA,
        )
        session.add(conversation)
        session.flush()

    snapshot = GrantSnapshot(
        class_id=conversation.class_id,
        school_id=user.school_id,
        grants=(),
    )
    document = LocalDevDocumentClient({(user.school_id, conversation.class_id): snapshot}, {})

    def upstream(request):
        user_fields = {"user_id": str(user.id), "school_id": str(user.school_id)}
        if request.url.host == "learning.test":
            return httpx.Response(200, json={**user_fields, "active_official_assessment": False})
        return httpx.Response(
            200,
            json={
                **user_fields,
                "allowed": True,
                "class_id": str(conversation.class_id),
                "class_status": "ACTIVE",
                "action": request.url.params["action"],
            },
        )

    calls = []

    def generate(*args):
        calls.append(args)
        return TurnResult(rendered={"status": "insufficient_evidence", "claims": []})

    async def run():
        runtime = RagRuntime(settings)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
                core, learning = CoreClient(http, settings), HttpLearningClient(http, settings)
                correlation = str(uuid4())
                chunks = stream_turn(
                    database,
                    runtime,
                    core,
                    document,
                    learning,
                    user,
                    conversation.id,
                    "Test turn",
                    TutorMode.QUICK_QA,
                    correlation,
                    generate,
                )
                announcement = (await anext(chunks)).decode()
                message_id = json.loads(announcement.split("data: ", 1)[1])["message_id"]
                assert await stop_turn(
                    core,
                    user,
                    conversation,
                    UUID(message_id),
                    correlation,
                    database,
                    runtime.cancellations,
                )
                remaining = [chunk async for chunk in chunks]
                assert len(remaining) == 1 and b'"status":"STOPPED"' in remaining[0]
        finally:
            await runtime.close()

    asyncio.run(run())
    assert not calls


def test_quiz_export_requires_approval_and_retries_the_same_item(postgres):
    database, _, _ = postgres
    user = CurrentUser(id=uuid4(), school_id=uuid4(), status="ACTIVE", roles=("LECTURER",))
    class_id, receipt_id = uuid4(), uuid4()
    snapshot = GrantSnapshot(
        school_id=user.school_id,
        class_id=class_id,
        grants=(ActiveGrant(document_id=uuid4(), document_version_id=uuid4(), source_id="source"),),
    )
    document = LocalDevDocumentClient({(user.school_id, class_id): snapshot}, {})
    settings = Settings(
        core_service_key="test-key",
        learning_service_key="test-key",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_quiz_export_path="/internal/test/export",
    )
    with database.SessionLocal.begin() as session:
        job = QuizJob(
            class_id=class_id, school_id=user.school_id, requested_by=user.id, spec_json={}
        )
        session.add(job)
        session.flush()
        draft = QuizItemDraft(
            quiz_job_id=job.id,
            slot_id="slot",
            status=DraftStatus.PASSED,
            level="biet",
            item_json={
                "type": "single_choice",
                "evidence": [
                    {"source_id": "source", "source_spans": [[0, 8]], "quote": "private passage"},
                ],
            },
        )
        session.add(draft)
        session.flush()

    exported_payloads = []

    def upstream(request):
        if request.url.host == "learning.test":
            assert request.headers["Idempotency-Key"] == str(draft.id)
            exported_payloads.append(json.loads(request.content))
            if len(exported_payloads) == 1:
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    "class_id": str(class_id),
                    "school_id": str(user.school_id),
                    "idempotency_key": str(draft.id),
                    "learning_item_id": str(receipt_id),
                },
            )
        return httpx.Response(
            200,
            json={
                "user_id": str(user.id),
                "school_id": str(user.school_id),
                "class_id": str(class_id),
                "class_status": "ACTIVE",
                "action": "participate_current_class",
                "teaching_role": "PRIMARY",
                "allowed": True,
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            core, learning = CoreClient(http, settings), HttpLearningClient(http, settings)
            token = str(uuid4())
            with pytest.raises(AppError) as unapproved:
                await export_draft(
                    database, core, document, learning, user, class_id, draft.id, token
                )
            assert unapproved.value.status_code == 409 and not exported_payloads
            assert (
                await review_draft(
                    database,
                    core,
                    document,
                    user,
                    class_id,
                    draft.id,
                    "accept",
                    token,
                )
                == DraftStatus.ACCEPTED
            )
            with pytest.raises(AppError) as unavailable:
                await export_draft(
                    database, core, document, learning, user, class_id, draft.id, token
                )
            assert unavailable.value.status_code == 503
            with database.SessionLocal() as session:
                assert session.get(QuizItemDraft, draft.id).status == DraftStatus.EXPORT_PENDING
            for _ in range(2):
                receipt = await export_draft(
                    database,
                    core,
                    document,
                    learning,
                    user,
                    class_id,
                    draft.id,
                    token,
                )
                assert receipt.learning_item_id == receipt_id

    asyncio.run(run())
    assert len(exported_payloads) == 2 and exported_payloads[0] == exported_payloads[1]
    assert "private passage" not in json.dumps(exported_payloads)


@pytest.fixture
def http_system(postgres, tmp_path, fixture_encoder, monkeypatch):
    secret = "test-core-jwt-secret-with-more-than-32"
    school, klass, student, teacher, admin, version, generation, receipt = (
        uuid4() for _ in range(8)
    )
    source = TextDocument("textbook", "# Chapter\n\nPlants convert light into chemical energy.")
    build_corpus([source], tmp_path / str(generation), encoder=fixture_encoder)
    flags = {"assessment": False, "document_down": False, "revoked": False, "learning_down": False}
    relevant = {
        "document_id": str(uuid4()),
        "document_version_id": str(version),
        "source_id": source.source_id,
    }
    roles = {student: "STUDENT", teacher: "LECTURER", admin: "ADMIN"}
    now = int(datetime.now(UTC).timestamp())
    by_token = {
        jwt.encode(
            {"sub": str(user), "roles": [role], "session_version": 1, "iat": now, "exp": now + 300},
            secret,
            algorithm="HS256",
        ): user
        for user, role in roles.items()
    }
    headers = {
        roles[user]: {"Authorization": f"Bearer {token}"} for token, user in by_token.items()
    }
    exported = []

    def upstream(request):
        path = request.url.path
        if request.url.host == "document.test":
            if flags["document_down"]:
                return httpx.Response(503)
            snapshot = {"class_id": str(klass), "school_id": str(school)}
            if path.endswith("/active-grants"):
                return httpx.Response(
                    200, json={**snapshot, "grants": [] if flags["revoked"] else [relevant]}
                )
            keys = json.loads(request.content)
            assert keys["source_spans"][0][0] < keys["source_spans"][0][1]
            return httpx.Response(
                200,
                json={
                    **snapshot,
                    **relevant,
                    "location": {"document_name": "Textbook", "page": 7},
                },
            )
        if request.url.host == "learning.test":
            if flags["learning_down"]:
                return httpx.Response(503)
            if path == "/internal/test/assessment":
                return httpx.Response(
                    200,
                    json={
                        "user_id": str(student),
                        "school_id": str(school),
                        "active_official_assessment": flags["assessment"],
                    },
                )
            exported.append(request.headers["Idempotency-Key"])
            return httpx.Response(
                201,
                json={
                    "school_id": str(school),
                    "class_id": str(klass),
                    "idempotency_key": exported[-1],
                    "learning_item_id": str(receipt),
                },
            )
        if path.endswith("/access"):
            user = UUID(request.url.params["user_id"])
            action = request.url.params["action"]
            class_id = path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "class_id": class_id,
                    "user_id": str(user),
                    "action": action,
                    "school_id": str(school),
                    "class_status": "ACTIVE",
                    "teaching_role": "PRIMARY" if user == teacher else None,
                    "allowed": class_id == str(klass) and user != admin,
                },
            )
        if path == "/api/v1/users/me":
            user = by_token[request.headers["Authorization"].split(" ", 1)[1]]
            return httpx.Response(
                200,
                json={
                    "id": str(user),
                    "school_id": str(school),
                    "roles": [roles[user]],
                    "status": "ACTIVE",
                },
            )
        assert path == f"/api/v1/classes/{klass}"
        return httpx.Response(
            200,
            json={
                "id": str(klass),
                "school_id": str(school),
                "status": "ACTIVE",
                "grade_level": 8,
            },
        )

    settings = Settings(
        core_jwt_secret=secret,
        core_service_key="core-test",
        ai_internal_service_secret="index-test",
        ai_index_root=tmp_path,
        document_mode="http",
        document_api_url="http://document.test",
        document_service_key="document-test",
        document_locate_path="/internal/test/locate",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_service_key="learning-test",
        learning_active_assessment_path="/internal/test/assessment",
        learning_quiz_export_path="/internal/test/export",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        from app.integrations.document_client import HttpDocumentClient

        http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        monkeypatch.setattr(app.state, "core", CoreClient(http, settings))
        monkeypatch.setattr(app.state, "document", HttpDocumentClient(http, settings))
        monkeypatch.setattr(app.state, "learning", HttpLearningClient(http, settings))
        runtime = app.state.runtime
        monkeypatch.setattr(runtime, "encoder", lambda: fixture_encoder)
        index_path = f"/internal/v1/document-versions/{version}/index"
        body = {
            "source_id": source.source_id,
            "source_sha256": sha256(source.text.encode()).hexdigest(),
            "generation_id": str(generation),
        }
        assert client.put(index_path, json=body).status_code == 401
        assert (
            client.put(index_path, json=body, headers={"X-Service-Key": "index-test"}).status_code
            == 204
        )
        try:
            yield SimpleNamespace(
                client=client,
                runtime=runtime,
                headers=headers,
                flags=flags,
                klass=klass,
                version=version,
                encoder=fixture_encoder,
                exported=exported,
                receipt=receipt,
            )
        finally:
            client.portal.call(http.aclose)


def test_tutor_api_sse_checks_learning_scope_and_history(http_system, monkeypatch):
    test = http_system
    student, teacher = test.headers["STUDENT"], test.headers["LECTURER"]

    def run(spec, task, evidence, **_):
        if spec.name == "qa.condense":
            return TaskResult(
                spec.name,
                spec.version,
                ok=True,
                value={
                    "standalone_question": task["question"],
                    "intent": "DOC_QUESTION",
                    "needs_retrieval": True,
                },
            )
        assert task["grade"] == 8
        assert {item["record"]["source_id"] for item in evidence} == {"textbook"}
        quote = "Plants convert light into chemical energy."
        citation = {
            "source_id": "textbook",
            "quote": quote,
            "source_spans": quote_spans(evidence[0], quote),
        }
        if spec.name == "qa.quick":
            value = {
                "status": "answered",
                "claims": [
                    {"text": "Plants use light.", "basis": "source", "evidence": [citation]},
                ],
            }
        else:
            assert spec.name == "qa.step_plan"
            value = {
                "status": "planned",
                "problem": "Plants",
                "final_answer": "protected-plan",
                "steps": [
                    {
                        "goal": "Plants",
                        "expected": "light",
                        "hint": "Think about light.",
                        "hint_more": "Consider sunlight.",
                        "evidence": [citation],
                    }
                ],
            }
        return TaskResult(spec.name, spec.version, ok=True, value=value)

    monkeypatch.setattr(test.runtime, "pipeline", lambda: SimpleNamespace(run=run))
    config_path = f"/api/v1/classes/{test.klass}/tutor/config"
    config = test.client.get(config_path, headers=teacher).json()
    assert config.pop("version") is None
    config["expected_version"] = None
    config["allow_solution_reveal"] = False
    assert test.client.put(config_path, headers=teacher, json=config).json()["version"] == 1
    assert test.client.put(config_path, headers=teacher, json=config).status_code == 409
    assert test.client.get(config_path, headers=student).status_code == 403

    conversations = f"/api/v1/classes/{test.klass}/tutor/conversations"
    response = test.client.post(conversations, headers=student, json={"title": "Biology"})
    assert response.status_code == 201
    conversation_id = response.json()["id"]
    assert test.client.get(conversations, headers=student).json()[0]["id"] == conversation_id
    path = f"/api/v1/tutor/conversations/{conversation_id}/messages"
    test.flags["assessment"] = True
    response = test.client.post(path, headers=student, json={"text": "What do plants use?"})
    assert response.status_code == 403
    assert test.client.get(path, headers=student).json() == []
    test.flags["assessment"] = False

    def send(payload):
        response = test.client.post(path, headers=student, json=payload)
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")
        start, final = (
            json.loads(frame.split("data: ", 1)[1]) for frame in response.text.strip().split("\n\n")
        )
        assert start["message_id"] == final["id"]
        return final

    completed = send({"text": "What do plants use?"})
    assert completed["status"] == "COMPLETED"
    assert completed["rendered"]["claims"][0]["citations"][0]["location"]["page"] == 7
    assert (
        send({"text": "What do plants use?", "mode": "STEP_BY_STEP"})["rendered"]["step"]["step"]
        == 1
    )
    assert (
        "Consider sunlight"
        in send({"operation": "HINT", "mode": "STEP_BY_STEP"})["rendered"]["claims"][0]["text"]
    )
    assert send({"operation": "REVEAL", "mode": "STEP_BY_STEP"})["status"] == "FAILED"
    assert test.client.get(path, headers=test.headers["ADMIN"]).status_code == 404
    assert "protected-plan" not in test.client.get(path, headers=student).text
    test.flags["revoked"] = True
    masked = test.client.get(path, headers=student).text
    assert "source_access_revoked" in masked
    assert "chemical energy" not in masked and "Consider sunlight" not in masked
    test.flags["learning_down"] = True
    assert test.client.post(path, headers=student, json={"text": "More?"}).status_code == 503
    test.flags["document_down"] = True
    assert test.client.get(path, headers=student).status_code == 200


def test_quiz_api_queues_lists_reviews_and_retries_export(http_system, monkeypatch):
    test = http_system
    teacher = test.headers["LECTURER"]
    from app.modules.quiz import service

    monkeypatch.setattr(
        test.runtime,
        "pipeline",
        lambda: SimpleNamespace(generator=SimpleNamespace(counter=test.encoder.counter)),
    )
    monkeypatch.setattr(
        service,
        "extract_facts",
        lambda *_args, **_kwargs: {
            "facts": [{"level_hint": "biet"}],
            "provenance": [],
        },
    )

    def run(_pipeline, spec, units, **kwargs):
        assert spec.grade == 8 and units[0].source_id == "textbook"
        kwargs["progress"]({"kind": "item_done", "done": 1})
        return {
            "drafts": [
                {
                    "generation_records": [],
                    "level": "biet",
                    "status": "PASSED",
                    "slot_id": "r0-0",
                    "seed": 0,
                    "item": {
                        "type": "single_choice",
                        "evidence": [{"source_id": "textbook", "source_spans": [[0, 8]]}],
                    },
                }
            ]
        }

    monkeypatch.setattr(service, "run_job", run)
    base = f"/api/v1/classes/{test.klass}/quiz"
    scope = [{"document_version_id": str(test.version)}]
    proposal = test.client.post(
        f"{base}/proposals", headers=teacher, json={"scope": scope, "total": 1}
    )
    assert proposal.status_code == 200 and proposal.json()[0]["type"] == "single_choice"
    submit = test.client.post(
        f"{base}/jobs", headers=teacher, json={"scope": scope, "matrix": proposal.json()}
    )
    assert submit.status_code == 202
    path = f"{base}/jobs/{submit.json()['id']}"
    assert test.client.get(path, headers=teacher).json()["status"] == "COMPLETED"
    assert test.client.get(path, headers=test.headers["STUDENT"]).status_code == 403
    items = test.client.get(f"{path}/items", headers=teacher).json()
    item_path = f"{base}/items/{items[0]['id']}"
    assert test.client.post(f"{item_path}/export", headers=teacher).status_code == 409
    assert (
        test.client.post(
            f"{item_path}/review", headers=teacher, json={"decision": "accept"}
        ).status_code
        == 200
    )
    test.flags["learning_down"] = True
    assert test.client.post(f"{item_path}/export", headers=teacher).status_code == 503
    assert test.client.get(f"{path}/items", headers=teacher).json()[0]["status"] == "EXPORT_PENDING"
    test.flags["learning_down"] = False
    assert test.client.post(f"{item_path}/export", headers=teacher).json()[
        "learning_item_id"
    ] == str(test.receipt)
    assert test.exported == [items[0]["id"]]
    test.flags["revoked"] = True
    assert test.client.get(f"{path}/items", headers=teacher).json()[0]["item"] is None
