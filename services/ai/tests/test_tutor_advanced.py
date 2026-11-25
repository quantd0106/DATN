import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi.testclient import TestClient
from rag.generation.pipeline import TaskResult
from sqlalchemy import select

from app.config import Settings
from app.integrations.core_client import ClassAction, CoreClient
from app.integrations.document_client import HttpDocumentClient
from app.integrations.learning_client import HttpLearningClient
from app.main import create_app
from app.modules.indexing import corpora
from app.modules.tutor import generation
from app.modules.tutor.models import Conversation, Message, MessageRole, TutorConfigRow

JWT_SECRET = "tutor-advanced-test-jwt-secret-0001"
CORE_KEY = "core-key"
DOC_KEY = "doc-key"
LEARN_KEY = "learn-key"


def _token(user_id, roles):
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "roles": list(roles),
            "session_version": 1,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _sse_events(response) -> list[tuple[str, dict]]:
    events = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        event_name = lines[0].split("event: ", 1)[1]
        data = json.loads(lines[1].split("data: ", 1)[1])
        events.append((event_name, data))
    return events


@contextmanager
def _make_harness(sqlite_db, monkeypatch, tmp_path):
    school_id = uuid4()
    class_id = uuid4()
    student_id = uuid4()
    doc_id = uuid4()
    version_id = uuid4()
    second_doc_id = uuid4()
    second_version_id = uuid4()
    state = {"active_sources": ["textbook"], "allow_general": False}
    pipeline_calls: list[tuple[str, dict]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/users/me":
            return httpx.Response(
                200,
                json={
                    "id": str(student_id),
                    "school_id": str(school_id),
                    "roles": ["STUDENT"],
                    "status": "ACTIVE",
                },
            )
        if request.url.path == f"/api/v1/classes/{class_id}":
            return httpx.Response(
                200,
                json={
                    "id": str(class_id),
                    "school_id": str(school_id),
                    "status": "ACTIVE",
                    "grade_level": 8,
                    "teaching_role": None,
                },
            )
        if request.url.path == f"/internal/v1/classes/{class_id}/access":
            action = request.url.params["action"]
            allowed = action in {
                ClassAction.USE_AI_TUTOR.value,
                ClassAction.READ_OWN_HISTORY.value,
                ClassAction.READ_DOCUMENT_SOURCE.value,
            }
            return httpx.Response(
                200,
                json={
                    "allowed": allowed,
                    "class_id": str(class_id),
                    "user_id": str(student_id),
                    "action": action,
                    "school_id": str(school_id),
                    "class_status": "ACTIVE",
                    "teaching_role": None,
                },
            )
        if request.url.path == f"/internal/v1/classes/{class_id}/documents/active-grants":
            grants = []
            if "textbook" in state["active_sources"]:
                grants.append(
                    {
                        "document_id": str(doc_id),
                        "document_version_id": str(version_id),
                        "source_id": "textbook",
                    }
                )
            if "notes" in state["active_sources"]:
                grants.append(
                    {
                        "document_id": str(second_doc_id),
                        "document_version_id": str(second_version_id),
                        "source_id": "notes",
                    }
                )
            return httpx.Response(
                200,
                json={"class_id": str(class_id), "school_id": str(school_id), "grants": grants},
            )
        if request.url.path == "/internal/test/locate":
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "class_id": str(class_id),
                    "school_id": str(school_id),
                    "document_version_id": body["document_version_id"],
                    "source_id": body["source_id"],
                    "location": {"document_name": "Sinh học 8", "page": 12},
                },
            )
        if request.url.path == "/internal/test/assessment":
            return httpx.Response(
                200,
                json={
                    "user_id": str(student_id),
                    "school_id": str(school_id),
                    "active_official_assessment": False,
                },
            )
        return httpx.Response(404)

    settings = Settings(
        core_jwt_secret=JWT_SECRET,
        core_service_key=CORE_KEY,
        core_api_url="http://core.test",
        document_mode="http",
        document_api_url="http://document.test",
        document_service_key=DOC_KEY,
        document_locate_path="/internal/test/locate",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_service_key=LEARN_KEY,
        learning_active_assessment_path="/internal/test/assessment",
        ai_index_root=tmp_path,
    )
    app = create_app(settings)

    class FakeRetriever:
        def __init__(self, source_id: str, text: str):
            self.source_id = source_id
            self.text = text
            self.children = {f"{source_id}-c1": {"source_id": source_id}}

        def retrieve(self, _request, authorize=None):
            ids = authorize() if authorize else {f"{self.source_id}-c1"}
            assert ids == {f"{self.source_id}-c1"}
            return {
                "context": {
                    "items": [
                        {
                            "chunk_id": f"{self.source_id}-c1",
                            "source_id": self.source_id,
                            "section_path": ["Chương 1"],
                            "text": self.text,
                            "source_spans": [
                                {
                                    "start": 0,
                                    "end": len(self.text),
                                    "source_start": 0,
                                    "source_end": len(self.text),
                                }
                            ],
                        }
                    ]
                }
            }

    texts = {
        "textbook": "SECRET_REVOKED_QUOTE Quang hợp chuyển hóa ánh sáng thành năng lượng hóa học.",
        "notes": "Ghi chú bổ sung về hô hấp tế bào tạo năng lượng ATP.",
    }

    def fake_corpora(_db, _rt, snapshot):
        return iter(
            (grant, FakeRetriever(grant.source_id, texts[grant.source_id]))
            for grant in snapshot.grants
            if grant.source_id in texts
        )

    monkeypatch.setattr(corpora, "active_corpora", fake_corpora)
    monkeypatch.setattr(generation, "active_corpora", fake_corpora)

    with TestClient(app) as client:
        http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app.state.database = sqlite_db
        app.state.core = CoreClient(http, settings)
        app.state.document = HttpDocumentClient(http, settings)
        app.state.learning = HttpLearningClient(http, settings)
        yield SimpleNamespace(
            app=app,
            client=client,
            db=sqlite_db,
            school_id=school_id,
            class_id=class_id,
            student_id=student_id,
            state=state,
            pipeline_calls=pipeline_calls,
            headers={"Authorization": f"Bearer {_token(student_id, ('STUDENT',))}"},
        )
        client.portal.call(http.aclose)


def test_multiturn_condense_masks_revoked_quotes_and_persists_metadata(
    sqlite_db, monkeypatch, tmp_path
):
    with _make_harness(sqlite_db, monkeypatch, tmp_path) as h:
        intent_state = {"next": "DOC_QUESTION", "general_only": False}

        def run(spec, task, evidence, **_kwargs):
            h.pipeline_calls.append((spec.name, task))
            if spec.name == "qa.condense":
                return TaskResult(
                    spec.name,
                    spec.version,
                    ok=True,
                    value={
                        "standalone_question": f"Câu hỏi độc lập: {task['question']}",
                        "intent": intent_state["next"],
                        "needs_retrieval": not intent_state["general_only"],
                    },
                )
            if spec.name == "qa.step_plan":
                quote = evidence[0]["text"]
                return TaskResult(
                    spec.name,
                    spec.version,
                    ok=True,
                    value={
                        "status": "planned",
                        "problem": task["problem"],
                        "final_answer": "Kết quả ẩn.",
                        "steps": [
                            {
                                "goal": "Xác định nguồn năng lượng",
                                "expected": "ATP",
                                "hint": "Xem vai trò ATP.",
                                "hint_more": "Ghi chú ATP.",
                                "evidence": [
                                    {
                                        "source_id": evidence[0]["record"]["source_id"],
                                        "quote": quote,
                                        "source_spans": [{"start": 0, "end": len(quote)}],
                                    }
                                ],
                            }
                        ],
                    },
                )
            assert spec.name == "qa.quick"
            if intent_state["general_only"]:
                return TaskResult(
                    spec.name,
                    spec.version,
                    ok=True,
                    value={
                        "status": "answered",
                        "claims": [
                            {
                                "text": "Kiến thức phổ thông cơ bản.",
                                "basis": "general",
                                "evidence": [],
                            }
                        ],
                    },
                )
            quote = evidence[0]["text"]
            return TaskResult(
                spec.name,
                spec.version,
                ok=True,
                value={
                    "status": "answered",
                    "claims": [
                        {
                            "text": f"Tóm tắt từ nguồn: {quote}",
                            "basis": "source",
                            "evidence": [
                                {
                                    "source_id": evidence[0]["record"]["source_id"],
                                    "quote": quote,
                                    "source_spans": [{"start": 0, "end": len(quote)}],
                                }
                            ],
                        }
                    ],
                },
            )

        monkeypatch.setattr(h.app.state.runtime, "pipeline", lambda: SimpleNamespace(run=run))
        conv_resp = h.client.post(
            f"/api/v1/classes/{h.class_id}/tutor/conversations",
            headers=h.headers,
            json={"title": "Sinh học", "default_mode": "QUICK_QA"},
        )
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]
        conv_uuid = UUID(conv_id)
        msg_url = f"/api/v1/tutor/conversations/{conv_id}/messages"

        # Turn 1: no prior history -> condense must NOT run.
        r1 = h.client.post(
            msg_url,
            headers=h.headers,
            json={"text": "Quang hợp là quá trình sinh học gì ở thực vật?"},
        )
        assert r1.status_code == 200
        assert _sse_events(r1)[-1][1]["status"] == "COMPLETED"
        assert [name for name, _ in h.pipeline_calls] == ["qa.quick"]

        with h.db.SessionLocal() as session:
            m1 = session.scalars(
                select(Message)
                .where(Message.conversation_id == conv_uuid, Message.role == MessageRole.ASSISTANT)
                .order_by(Message.created_at)
            ).all()
            assert len(m1) == 1
            assert m1[0].knowledge_type == "CLASS_SOURCE"
            assert m1[0].step_number is None

        # Turn 2: deictic follow-up -> condense runs and sets context_hint on qa.quick.
        h.pipeline_calls.clear()
        r2 = h.client.post(msg_url, headers=h.headers, json={"text": "Còn vai trò của nó thì sao?"})
        assert r2.status_code == 200
        assert [name for name, _ in h.pipeline_calls] == ["qa.condense", "qa.quick"]
        assert "context_hint" in h.pipeline_calls[1][1]
        assert h.pipeline_calls[1][1]["context_hint"].startswith("Câu hỏi độc lập:")

        # Revoke textbook, grant notes -> Turn 3 must NOT leak textbook quote into condense history.
        h.state["active_sources"] = ["notes"]
        h.pipeline_calls.clear()
        r3 = h.client.post(
            msg_url, headers=h.headers, json={"text": "Điều đó liên quan hô hấp thế nào?"}
        )
        assert r3.status_code == 200
        condense_task = h.pipeline_calls[0][1]
        assert h.pipeline_calls[0][0] == "qa.condense"
        condense_history_text = json.dumps(condense_task["history"], ensure_ascii=False)
        assert "SECRET_REVOKED_QUOTE" not in condense_history_text

        # Intent routing: OUT_OF_SCOPE -> DECLINE; META -> fixed reply; EXERCISE_HELP -> STEP_BY_STEP
        intent_state["next"] = "OUT_OF_SCOPE"
        out_events = _sse_events(
            h.client.post(msg_url, headers=h.headers, json={"text": "Nó giá bao nhiêu?"})
        )
        assert out_events[-1][1]["rendered"]["status"] == "insufficient_evidence"
        assert "ngoài phạm vi" in (out_events[-1][1]["rendered"]["notice"] or "")

        intent_state["next"] = "META"
        meta_events = _sse_events(
            h.client.post(msg_url, headers=h.headers, json={"text": "Cậu là ai vậy?"})
        )
        assert "Athena" in meta_events[-1][1]["rendered"]["claims"][0]["text"]

        # EXERCISE_HELP in QUICK_QA switches to STEP_BY_STEP and saves step_session + metadata
        intent_state["next"] = "EXERCISE_HELP"
        h.pipeline_calls.clear()
        step_events = _sse_events(
            h.client.post(
                msg_url,
                headers=h.headers,
                json={"text": "Giúp mình làm bài đó với", "mode": "QUICK_QA"},
            )
        )
        assert [name for name, _ in h.pipeline_calls] == ["qa.condense", "qa.step_plan"]
        assert step_events[-1][1]["rendered"]["step"] == {
            "state": "HINT",
            "step": 1,
            "total_steps": 1,
        }

        with h.db.SessionLocal() as session:
            conv = session.get(Conversation, conv_uuid)
            assert conv.step_session_json is not None
            latest = session.scalars(
                select(Message)
                .where(Message.conversation_id == conv_uuid, Message.role == MessageRole.ASSISTANT)
                .order_by(Message.created_at.desc())
            ).first()
            assert latest.step_number == 1 and latest.total_steps == 1
            assert latest.knowledge_type == "CLASS_SOURCE"

        # Enable allow_general_knowledge and check GENERAL_KNOWLEDGE metadata persistence
        with h.db.SessionLocal.begin() as session:
            session.add(
                TutorConfigRow(
                    class_id=h.class_id,
                    school_id=h.school_id,
                    allow_general_knowledge=True,
                    hint_reveal_after=3,
                    allow_solution_reveal=True,
                    guidance_level="balanced",
                    max_answer_words=150,
                    updated_by=uuid4(),
                )
            )
        intent_state["next"] = "GENERAL"
        intent_state["general_only"] = True
        assert (
            h.client.post(
                msg_url, headers=h.headers, json={"text": "Giải thích thêm điều đó"}
            ).status_code
            == 200
        )
        with h.db.SessionLocal() as session:
            latest = session.scalars(
                select(Message)
                .where(Message.conversation_id == conv_uuid, Message.role == MessageRole.ASSISTANT)
                .order_by(Message.created_at.desc())
            ).first()
            assert latest.knowledge_type == "GENERAL_KNOWLEDGE"


def test_retry_of_message_id_reuses_user_message_and_validates_target(
    sqlite_db, monkeypatch, tmp_path
):
    with _make_harness(sqlite_db, monkeypatch, tmp_path) as h:
        seen_calls = []

        def run(spec, task, evidence, **_kwargs):
            seen_calls.append(spec.name)
            quote = evidence[0]["text"]
            return TaskResult(
                spec.name,
                spec.version,
                ok=True,
                value={
                    "status": "answered",
                    "claims": [
                        {
                            "text": "Quang hợp diễn ra ở lục lạp.",
                            "basis": "source",
                            "evidence": [
                                {
                                    "source_id": evidence[0]["record"]["source_id"],
                                    "quote": quote,
                                    "source_spans": [{"start": 0, "end": len(quote)}],
                                }
                            ],
                        }
                    ],
                },
            )

        monkeypatch.setattr(h.app.state.runtime, "pipeline", lambda: SimpleNamespace(run=run))
        conv_id = h.client.post(
            f"/api/v1/classes/{h.class_id}/tutor/conversations",
            headers=h.headers,
            json={"title": "Retry"},
        ).json()["id"]
        conv_uuid = UUID(conv_id)
        msg_url = f"/api/v1/tutor/conversations/{conv_id}/messages"

        first = h.client.post(msg_url, headers=h.headers, json={"text": "Quang hợp diễn ra ở đâu?"})
        assert first.status_code == 200
        first_assistant_id = _sse_events(first)[-1][1]["id"]

        with h.db.SessionLocal() as session:
            user_msgs = session.scalars(
                select(Message).where(
                    Message.conversation_id == conv_uuid, Message.role == MessageRole.USER
                )
            ).all()
            assert len(user_msgs) == 1
            u1_id = user_msgs[0].id

        # Non-ASK retry must fail validation (422)
        assert (
            h.client.post(
                msg_url,
                headers=h.headers,
                json={
                    "operation": "HINT",
                    "mode": "STEP_BY_STEP",
                    "retry_of_message_id": str(u1_id),
                },
            ).status_code
            == 422
        )
        # Missing / ASSISTANT target must return 404
        assert (
            h.client.post(
                msg_url,
                headers=h.headers,
                json={"retry_of_message_id": str(uuid4())},
            ).status_code
            == 404
        )
        assert (
            h.client.post(
                msg_url,
                headers=h.headers,
                json={"retry_of_message_id": first_assistant_id},
            ).status_code
            == 404
        )

        # Valid retry must not create duplicate USER message, links retry_of_message_id, skips condense
        seen_calls.clear()
        retried = h.client.post(
            msg_url, headers=h.headers, json={"retry_of_message_id": str(u1_id)}
        )
        assert retried.status_code == 200
        retry_assistant_id = _sse_events(retried)[-1][1]["id"]
        assert seen_calls == ["qa.quick"]

        with h.db.SessionLocal() as session:
            user_msgs = session.scalars(
                select(Message).where(
                    Message.conversation_id == conv_uuid, Message.role == MessageRole.USER
                )
            ).all()
            assert len(user_msgs) == 1
            retry_assistant = session.get(Message, UUID(retry_assistant_id))
            assert retry_assistant.retry_of_message_id == u1_id
