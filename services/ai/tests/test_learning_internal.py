import json
from threading import current_thread
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from rag.generation.pipeline import GenerationUnavailable, GroundedTaskPipeline, SchedulerBusy
from sqlalchemy import select

from app.common.errors import AppError
from app.config import Settings
from app.integrations.document_client import ActiveGrant, GrantSnapshot
from app.main import create_app
from app.modules.indexing import corpora
from app.modules.tutor.learning_internal import GRADE_HINT_SPEC
from app.runtime.models import GenerationProvenance

EXPLAIN = "/internal/v1/explain-mistake"
GRADE_HINT = "/internal/v1/assignment/grade-hint"
HEADERS = {"X-Service-Key": "learning-test-key"}
QUOTE = "A square has four equal sides."


@pytest.fixture
def scope():
    return GrantSnapshot(
        school_id=uuid4(),
        class_id=uuid4(),
        grants=(
            ActiveGrant(document_id=uuid4(), document_version_id=uuid4(), source_id="geometry"),
        ),
    )


@pytest.fixture
def mistake(scope):
    grant = scope.grants[0]
    return {
        "school_id": str(scope.school_id),
        "class_id": str(scope.class_id),
        "grade_level": 6,
        "question": "How many equal sides does a square have?",
        "student_choice": "Two",
        "correct_answer": "Four",
        "misconception": "Confuses a square with an oblong rectangle.",
        "approved_explanation": "A square has four equal sides.",
        "pinned_evidence": [
            {
                "source_id": grant.source_id,
                "source_spans": [{"source_id": grant.source_id, "start": 4, "end": 4 + len(QUOTE)}],
                "document_id": str(grant.document_id),
                "document_version_id": str(grant.document_version_id),
                "locator": "",
            }
        ],
    }


@pytest.fixture
def assignment(scope):
    return {
        "school_id": str(scope.school_id),
        "class_id": str(scope.class_id),
        "assignment_title": "Geometry exercises",
        "instructions": "Show your work.",
        "submission_note": "I attached my solutions.",
    }


@pytest.fixture
def local_app(monkeypatch, tmp_path, scope):
    app = create_app(
        Settings(
            ai_internal_service_secret=HEADERS["X-Service-Key"],
            llama_mode="local",
            llama_model_path="test.gguf",
            llama_server_path="llama-server",
            llama_model_repo="fixture",
            llama_model_revision="fixture",
            llama_model_variant="fixture",
            llama_model_sha256="a" * 64,
        )
    )
    with TestClient(app) as client:
        monkeypatch.setattr(app.state, "database", object())
        monkeypatch.setattr(app.state.document, "active_grants", AsyncMock(return_value=scope))

        def published(_database, _runtime, scoped):
            for grant in scoped.grants:
                yield grant, uuid4(), tmp_path

        monkeypatch.setattr(corpora, "_published_corpora", published)
        monkeypatch.setattr(
            corpora,
            "load_index_bundle",
            lambda _directory: {"sources": {"geometry": f"1.  {QUOTE} Another sentence."}},
        )
        yield app, client


def test_learning_routes_require_service_key_and_reject_extra_fields(mistake, assignment):
    app = create_app(Settings(ai_internal_service_secret=HEADERS["X-Service-Key"]))
    with TestClient(app) as client:
        for path, body in ((EXPLAIN, mistake), (GRADE_HINT, assignment)):
            assert client.post(path, json=body).status_code == 401
            assert (
                client.post(path, json=body, headers={"X-Service-Key": "wrong"}).status_code == 401
            )
            response = client.post(path, json=body | {"student_id": str(uuid4())}, headers=HEADERS)
            assert response.status_code == 422


def test_disabled_generation_uses_approved_explanation_and_disables_grade_hint(mistake, assignment):
    app = create_app(Settings(ai_internal_service_secret=HEADERS["X-Service-Key"]))
    with TestClient(app) as client:
        response = client.post(EXPLAIN, json=mistake, headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "explanation": mistake["approved_explanation"],
            "evidence": [],
            "fallback": True,
        }
        assert client.post(GRADE_HINT, json=assignment, headers=HEADERS).status_code == 503


@pytest.mark.parametrize("grant_revoked", [False, True])
def test_explain_mistake_runs_grounded_task_on_executor(
    local_app, monkeypatch, scope, mistake, grant_revoked
):
    app, client = local_app

    class Generator:
        context_tokens = 4096

        def count_messages(self, _messages):
            return 100

        def generate(self, messages, _schema, **_kwargs):
            assert current_thread().name.startswith("ai-rag")
            user_payload = json.loads(messages[1]["content"])
            assert user_payload["question"] == mistake["question"]
            assert user_payload["student_choice"] == mistake["student_choice"]
            assert user_payload["correct_answer"] == mistake["correct_answer"]
            assert user_payload["misconception"] == mistake["misconception"]
            assert user_payload["evidence"][0]["text"] == QUOTE
            assert str(scope.school_id) not in messages[1]["content"]
            return {
                "text": json.dumps(
                    {
                        "explanation": "All four sides are equal.",
                        "evidence": [{"id": "S1", "quote": QUOTE}],
                    }
                ),
                "finish_reason": "stop",
            }

    pipeline = GroundedTaskPipeline(Generator())
    monkeypatch.setattr(app.state.runtime, "pipeline", lambda: pipeline)
    if grant_revoked:
        app.state.document.active_grants.side_effect = [
            scope,
            scope.model_copy(update={"grants": ()}),
        ]
    response = client.post(EXPLAIN, json=mistake, headers=HEADERS)
    assert response.status_code == 200
    assert app.state.document.active_grants.await_count == 2
    if grant_revoked:
        assert response.json() == {
            "explanation": mistake["approved_explanation"],
            "evidence": [],
            "fallback": True,
        }
        return
    assert response.json() == {
        "explanation": "All four sides are equal.",
        "evidence": [
            {
                "id": "S1",
                "quote": QUOTE,
                "source_id": "geometry",
                "source_spans": [{"source_id": "geometry", "start": 4, "end": 4 + len(QUOTE)}],
            }
        ],
        "fallback": False,
    }


@pytest.mark.parametrize("error", [SchedulerBusy(), GenerationUnavailable(), AppError(503)])
def test_explain_mistake_falls_back_when_generation_unavailable(
    local_app, monkeypatch, mistake, error
):
    app, client = local_app

    def unavailable():
        raise error

    monkeypatch.setattr(app.state.runtime, "pipeline", unavailable)
    response = client.post(EXPLAIN, json=mistake, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "explanation": mistake["approved_explanation"],
        "evidence": [],
        "fallback": True,
    }


def test_explain_mistake_falls_back_when_llama_tokenization_fails(local_app, monkeypatch, mistake):
    app, client = local_app

    class Generator:
        context_tokens = 4096

        def count_messages(self, _messages):
            raise ConnectionError("Local llama unavailable")

    pipeline = GroundedTaskPipeline(Generator())
    monkeypatch.setattr(app.state.runtime, "pipeline", lambda: pipeline)
    response = client.post(EXPLAIN, json=mistake, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "explanation": mistake["approved_explanation"],
        "evidence": [],
        "fallback": True,
    }


def test_missing_or_ungranted_evidence_skips_generation(local_app, scope, mistake):
    app, client = local_app
    response = client.post(
        EXPLAIN, json=mistake | {"pinned_evidence": [], "approved_explanation": ""}, headers=HEADERS
    )
    assert response.json() == {"explanation": None, "evidence": [], "fallback": True}
    app.state.document.active_grants.assert_not_awaited()
    app.state.document.active_grants.return_value = scope.model_copy(update={"grants": ()})
    response = client.post(EXPLAIN, json=mistake, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "explanation": mistake["approved_explanation"],
        "evidence": [],
        "fallback": True,
    }


def test_grade_hint_only_returns_advisory_text_and_reports_unavailability(
    local_app, sqlite_db, monkeypatch, assignment
):
    app, client = local_app
    probe = AsyncMock(return_value="connected")
    monkeypatch.setattr(app.state.runtime, "probe_llama", probe)
    app.state.database = sqlite_db

    class AdvisoryGenerator:
        context_tokens = 4096

        def count_messages(self, _messages):
            return 80

        def generate(self, messages, _schema, **kwargs):
            assert current_thread().name.startswith("ai-rag")
            assert kwargs.get("priority", 1) == 1
            payload = json.loads(messages[1]["content"])
            assert payload["assignment_title"] == assignment["assignment_title"]
            return {
                "text": json.dumps(
                    {
                        "feedback": "Kiểm tra lại các bước lập luận hình học trong bài nộp.",
                        "limitations": "Mới chỉ đọc ghi chú nộp bài, giáo viên cần xem file đính kèm.",
                    }
                ),
                "finish_reason": "stop",
            }

    pipeline = GroundedTaskPipeline(AdvisoryGenerator())
    monkeypatch.setattr(app.state.runtime, "pipeline", lambda: pipeline)
    response = client.post(GRADE_HINT, json=assignment, headers=HEADERS)
    assert response.status_code == 200
    assert set(response.json()) == {"feedback", "limitations"}
    assert "lập luận" in response.json()["feedback"]

    with sqlite_db.SessionLocal() as session:
        rows = session.scalars(
            select(GenerationProvenance).where(
                GenerationProvenance.task_name == GRADE_HINT_SPEC.name
            )
        ).all()
        assert len(rows) == 1 and rows[0].outcome == "ok"

    # Output containing numeric scores violates Rule D5 and must fail closed (503)
    class ScoreLeakGenerator(AdvisoryGenerator):
        def generate(self, _messages, _schema, **_kwargs):
            return {
                "text": json.dumps(
                    {
                        "feedback": "Bài làm đạt 9 điểm rất tốt.",
                        "limitations": "Cần kiểm tra lại bài nộp.",
                    }
                ),
                "finish_reason": "stop",
            }

    monkeypatch.setattr(
        app.state.runtime, "pipeline", lambda: GroundedTaskPipeline(ScoreLeakGenerator())
    )
    assert client.post(GRADE_HINT, json=assignment, headers=HEADERS).status_code == 503

    # SchedulerBusy / GenerationUnavailable returns 503
    class BusyPipeline:
        def run(self, *_args, **_kwargs):
            raise SchedulerBusy("busy")

    monkeypatch.setattr(app.state.runtime, "pipeline", lambda: BusyPipeline())
    assert client.post(GRADE_HINT, json=assignment, headers=HEADERS).status_code == 503

    probe.side_effect = AppError(503)
    assert client.post(GRADE_HINT, json=assignment, headers=HEADERS).status_code == 503
