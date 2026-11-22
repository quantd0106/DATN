from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
from fastapi.testclient import TestClient
from rag.generation.runtime.scheduler import P1_TEACHER
from rag.generation.tasks.quiz.knowledge_units import KnowledgeUnit
from sqlalchemy import select

from app.background_worker import recover_stale_state, run_worker_cycle
from app.config import Settings
from app.integrations.core_client import ClassAction, CoreClient
from app.integrations.document_client import HttpDocumentClient
from app.integrations.learning_client import HttpLearningClient, QuizExportReceipt
from app.main import create_app
from app.modules.quiz import service as quiz_service
from app.modules.quiz.models import DraftStatus, JobStatus, QuizItemDraft, QuizJob
from app.modules.tutor.models import Conversation, Message, MessageRole, MessageStatus, TutorMode

JWT_SECRET = "quiz-lifecycle-test-jwt-secret-0001"
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


def _setup_app(sqlite_db):
    school_id = uuid4()
    class_id = uuid4()
    teacher_id = uuid4()
    student_id = uuid4()
    doc_id = uuid4()
    version_id = uuid4()
    state = {"teacher_allowed": True, "grant_active": True, "receipt": uuid4()}
    exported_receipts = []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/users/me":
            uid = teacher_id if state["current_role"] == "LECTURER" else student_id
            roles = [state["current_role"]]
            return httpx.Response(
                200,
                json={
                    "id": str(uid),
                    "school_id": str(school_id),
                    "roles": roles,
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
                    "teaching_role": "PRIMARY",
                },
            )
        if request.url.path == f"/internal/v1/classes/{class_id}/access":
            uid = request.url.params["user_id"]
            action = request.url.params["action"]
            allowed = (
                uid == str(teacher_id)
                and action == ClassAction.PARTICIPATE_CURRENT_CLASS.value
                and state["teacher_allowed"]
            )
            return httpx.Response(
                200,
                json={
                    "allowed": allowed,
                    "class_id": str(class_id),
                    "user_id": uid,
                    "action": action,
                    "school_id": str(school_id),
                    "class_status": "ACTIVE",
                    "teaching_role": "PRIMARY" if allowed else None,
                },
            )
        if request.url.path == f"/internal/v1/classes/{class_id}/documents/active-grants":
            grants = (
                [
                    {
                        "document_id": str(doc_id),
                        "document_version_id": str(version_id),
                        "source_id": "textbook",
                    }
                ]
                if state["grant_active"]
                else []
            )
            return httpx.Response(
                200, json={"class_id": str(class_id), "school_id": str(school_id), "grants": grants}
            )
        if request.url.path == "/internal/test/export":
            payload = httpx.Response(200)
            _ = payload
            exported_receipts.append(request.headers["Idempotency-Key"])
            return httpx.Response(
                201,
                json=QuizExportReceipt(
                    class_id=class_id,
                    school_id=school_id,
                    idempotency_key=request.headers["Idempotency-Key"],
                    learning_item_id=state["receipt"],
                ).model_dump(mode="json"),
            )
        return httpx.Response(404)

    settings = Settings(
        core_jwt_secret=JWT_SECRET,
        core_service_key=CORE_KEY,
        core_api_url="http://core.test",
        document_mode="http",
        document_api_url="http://document.test",
        document_service_key=DOC_KEY,
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_service_key=LEARN_KEY,
        learning_quiz_export_path="/internal/test/export",
    )
    app = create_app(settings)
    state["current_role"] = "LECTURER"
    http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app.state.database = sqlite_db
    app.state.core = CoreClient(http, settings)
    app.state.document = HttpDocumentClient(http, settings)
    app.state.learning = HttpLearningClient(http, settings)
    return SimpleNamespace(
        app=app,
        settings=settings,
        http=http,
        school_id=school_id,
        class_id=class_id,
        teacher_id=teacher_id,
        student_id=student_id,
        doc_id=doc_id,
        version_id=version_id,
        state=state,
        exported_receipts=exported_receipts,
        teacher_headers={"Authorization": f"Bearer {_token(teacher_id, ('LECTURER',))}"},
        student_headers={"Authorization": f"Bearer {_token(student_id, ('STUDENT',))}"},
    )


def test_list_jobs_and_regenerate_draft_endpoint(sqlite_db, monkeypatch):
    ctx = _setup_app(sqlite_db)
    unit = KnowledgeUnit(
        id="ku-1",
        document_version_id=str(ctx.version_id),
        source_id="textbook",
        section_path=("Chương 1",),
        text="Một hình vuông có bốn cạnh bằng nhau.",
        record={"source_id": "textbook"},
        token_count=10,
    )
    monkeypatch.setattr(quiz_service, "load_quiz_units", lambda *_a, **_kw: [unit])

    def fake_extract_facts(_pipeline, _unit, *, cache, priority, **_kw):
        assert priority == P1_TEACHER
        cache["ku-1"] = [
            {"id": "f1", "statement": "Hình vuông có 4 cạnh bằng nhau", "level_hint": "biet"}
        ]
        return {"facts": cache["ku-1"], "provenance": []}

    def fake_regenerate(_pipeline, _spec, draft, _unit, _facts):
        assert draft["status"] == "REGENERATE"
        assert draft["regenerations"] == 0
        return {
            "id": draft["id"],
            "job_id": draft["job_id"],
            "slot_id": draft["slot_id"],
            "type": draft["type"],
            "level": draft["level"],
            "ku_id": "ku-1",
            "fact_ids": ["f1"],
            "seed": 99,
            "status": "PASSED",
            "regenerations": 1,
            "flags": [],
            "generation_records": [],
            "item": {
                "type": "single_choice",
                "prompt": "Câu hỏi mới?",
                "options": [{"id": "a", "text": "Bốn"}, {"id": "b", "text": "Hai"}],
                "correct": "a",
                "evidence": [{"source_id": "textbook", "source_spans": [[0, 10]]}],
            },
        }

    monkeypatch.setattr(quiz_service, "extract_facts", fake_extract_facts)
    monkeypatch.setattr(quiz_service.rag_job, "regenerate", fake_regenerate)

    job1_id, job2_id, draft_id = uuid4(), uuid4(), uuid4()
    t0 = datetime.now(UTC) - timedelta(minutes=2)
    with sqlite_db.SessionLocal.begin() as session:
        session.add(
            QuizJob(
                id=job1_id,
                school_id=ctx.school_id,
                class_id=ctx.class_id,
                requested_by=ctx.teacher_id,
                status=JobStatus.COMPLETED,
                spec_json={
                    "class_id": str(ctx.class_id),
                    "purpose": "PRACTICE",
                    "grade": 8,
                    "subject": "Toán",
                    "language": "vi",
                    "scope": [{"document_version_id": str(ctx.version_id), "section_paths": []}],
                    "matrix": [
                        {
                            "section": "Chương 1",
                            "type": "single_choice",
                            "level": "biet",
                            "count": 1,
                        }
                    ],
                },
                completed_items=1,
                total_items=1,
                created_at=t0,
                updated_at=t0,
            )
        )
        session.add(
            QuizJob(
                id=job2_id,
                school_id=ctx.school_id,
                class_id=ctx.class_id,
                requested_by=ctx.teacher_id,
                status=JobStatus.QUEUED,
                spec_json={"scope": []},
                completed_items=0,
                total_items=1,
                created_at=t0 + timedelta(minutes=1),
                updated_at=t0 + timedelta(minutes=1),
            )
        )
        session.add(
            QuizItemDraft(
                id=draft_id,
                quiz_job_id=job1_id,
                slot_id="r0-0",
                status=DraftStatus.PASSED,
                level="biet",
                seed=7,
                item_json={
                    "type": "single_choice",
                    "evidence": [{"source_id": "textbook", "source_spans": [[0, 10]]}],
                },
                slot_meta_json={
                    "type": "single_choice",
                    "ku_id": "ku-1",
                    "fact_ids": ["f1"],
                    "regenerations": 0,
                },
                reviewer_id=ctx.teacher_id,
            )
        )

    with TestClient(ctx.app) as client:
        # Restore monkeypatched dependencies overwritten by lifespan
        ctx.app.state.database = sqlite_db
        ctx.app.state.core = CoreClient(ctx.http, ctx.settings)
        ctx.app.state.document = HttpDocumentClient(ctx.http, ctx.settings)
        ctx.app.state.learning = HttpLearningClient(ctx.http, ctx.settings)
        monkeypatch.setattr(
            ctx.app.state.runtime, "pipeline", lambda: SimpleNamespace(generator=None)
        )

        jobs = client.get(
            f"/api/v1/classes/{ctx.class_id}/quiz/jobs?limit=10", headers=ctx.teacher_headers
        )
        assert jobs.status_code == 200
        assert [row["id"] for row in jobs.json()] == [str(job2_id), str(job1_id)]

        assert (
            client.get(
                f"/api/v1/classes/{ctx.class_id}/quiz/jobs?limit=51", headers=ctx.teacher_headers
            ).status_code
            == 422
        )

        ctx.state["current_role"] = "STUDENT"
        assert (
            client.get(
                f"/api/v1/classes/{ctx.class_id}/quiz/jobs", headers=ctx.student_headers
            ).status_code
            == 403
        )
        ctx.state["current_role"] = "LECTURER"

        regen_url = f"/api/v1/classes/{ctx.class_id}/quiz/items/{draft_id}/regenerate"
        # Not in REGENERATE status -> 409
        assert client.post(regen_url, headers=ctx.teacher_headers).status_code == 409

        # Review as regenerate first, then regenerate
        assert (
            client.post(
                f"/api/v1/classes/{ctx.class_id}/quiz/items/{draft_id}/review",
                headers=ctx.teacher_headers,
                json={"decision": "regenerate", "reason": "ambiguous"},
            ).status_code
            == 200
        )
        regen_resp = client.post(regen_url, headers=ctx.teacher_headers)
        assert regen_resp.status_code == 200
        assert regen_resp.json()["status"] == "PASSED"
        assert regen_resp.json()["item"]["prompt"] == "Câu hỏi mới?"

        with sqlite_db.SessionLocal() as session:
            updated = session.get(QuizItemDraft, draft_id)
            assert updated.reviewer_id is None
            assert updated.review_reason is None
            assert updated.slot_meta_json["regenerations"] == 1
            job = session.get(QuizJob, job1_id)
            assert "ku-1" in job.fact_cache

        client.portal.call(ctx.http.aclose)


def test_recover_stale_state_and_worker_cycle(sqlite_db, monkeypatch):
    ctx = _setup_app(sqlite_db)
    old = datetime.now(UTC) - timedelta(seconds=600)
    fresh = datetime.now(UTC) - timedelta(seconds=10)
    conv_id = uuid4()
    stale_msg_id, fresh_msg_id = uuid4(), uuid4()
    stale_job_id, fresh_job_id = uuid4(), uuid4()

    with sqlite_db.SessionLocal.begin() as session:
        session.add(
            Conversation(
                id=conv_id,
                user_id=ctx.student_id,
                class_id=ctx.class_id,
                school_id=ctx.school_id,
                title="Stale",
                default_mode=TutorMode.QUICK_QA,
            )
        )
        session.add_all(
            [
                Message(
                    id=stale_msg_id,
                    conversation_id=conv_id,
                    role=MessageRole.ASSISTANT,
                    mode=TutorMode.QUICK_QA,
                    status=MessageStatus.STREAMING,
                    content="partial",
                    created_at=old,
                ),
                Message(
                    id=fresh_msg_id,
                    conversation_id=conv_id,
                    role=MessageRole.ASSISTANT,
                    mode=TutorMode.QUICK_QA,
                    status=MessageStatus.STREAMING,
                    content="active",
                    created_at=fresh,
                ),
                QuizJob(
                    id=stale_job_id,
                    school_id=ctx.school_id,
                    class_id=ctx.class_id,
                    requested_by=ctx.teacher_id,
                    status=JobStatus.RUNNING,
                    spec_json={
                        "class_id": str(ctx.class_id),
                        "purpose": "PRACTICE",
                        "grade": 8,
                        "subject": "Toán",
                        "language": "vi",
                        "scope": [
                            {"document_version_id": str(ctx.version_id), "section_paths": []}
                        ],
                        "matrix": [
                            {
                                "section": "Chương 1",
                                "type": "single_choice",
                                "level": "biet",
                                "count": 2,
                            }
                        ],
                    },
                    completed_items=1,
                    total_items=2,
                    created_at=old,
                    updated_at=old,
                ),
                QuizJob(
                    id=fresh_job_id,
                    school_id=ctx.school_id,
                    class_id=ctx.class_id,
                    requested_by=ctx.teacher_id,
                    status=JobStatus.RUNNING,
                    spec_json={"scope": []},
                    completed_items=0,
                    total_items=1,
                    created_at=fresh,
                    updated_at=fresh,
                ),
                QuizItemDraft(
                    id=uuid4(),
                    quiz_job_id=stale_job_id,
                    slot_id="r0-0",
                    status=DraftStatus.PASSED,
                    level="biet",
                    seed=1,
                    item_json={
                        "type": "single_choice",
                        "evidence": [{"source_id": "textbook", "source_spans": [[0, 5]]}],
                    },
                    slot_meta_json={
                        "type": "single_choice",
                        "ku_id": "ku-1",
                        "fact_ids": [],
                        "regenerations": 0,
                        "flags": [],
                    },
                ),
            ]
        )

    recovered = recover_stale_state(sqlite_db, stale_after_seconds=300)
    assert recovered == {"stale_messages": 1, "recovered_jobs": 1}
    with sqlite_db.SessionLocal() as session:
        assert session.get(Message, stale_msg_id).status == MessageStatus.FAILED
        assert session.get(Message, stale_msg_id).content is None
        assert session.get(Message, fresh_msg_id).status == MessageStatus.STREAMING
        assert session.get(QuizJob, stale_job_id).status == JobStatus.QUEUED
        assert session.get(QuizJob, fresh_job_id).status == JobStatus.RUNNING

    # Run worker cycle to resume stale_job_id (preserving r0-0, adding r0-1) + pending exports
    monkeypatch.setattr(quiz_service, "load_quiz_units", lambda *_a, **_kw: [])

    def fake_run_job(_pipeline, _spec, _units, *, completed, **_kw):
        assert "r0-0" in completed
        return {
            "drafts": [
                completed["r0-0"],
                {
                    "generation_records": [],
                    "slot_id": "r0-1",
                    "status": "PASSED",
                    "level": "biet",
                    "type": "single_choice",
                    "ku_id": "ku-1",
                    "fact_ids": ["f1"],
                    "regenerations": 0,
                    "flags": [],
                    "seed": 2,
                    "item": {
                        "type": "single_choice",
                        "prompt": "Resumed question?",
                        "options": [{"id": "a", "text": "1"}, {"id": "b", "text": "2"}],
                        "correct": "a",
                        "evidence": [{"source_id": "textbook", "source_spans": [[0, 5]]}],
                    },
                },
            ]
        }

    monkeypatch.setattr(quiz_service, "run_job", fake_run_job)

    valid_export_id = uuid4()
    revoked_export_id = uuid4()
    with sqlite_db.SessionLocal.begin() as session:
        session.add_all(
            [
                QuizItemDraft(
                    id=valid_export_id,
                    quiz_job_id=fresh_job_id,
                    slot_id="r0-0",
                    status=DraftStatus.EXPORT_PENDING,
                    level="biet",
                    seed=1,
                    reviewer_id=ctx.teacher_id,
                    item_json={
                        "type": "single_choice",
                        "prompt": "Export ready",
                        "options": [{"id": "a", "text": "1"}],
                        "correct": "a",
                        "evidence": [{"source_id": "textbook", "source_spans": [[0, 4]]}],
                    },
                ),
                QuizItemDraft(
                    id=revoked_export_id,
                    quiz_job_id=fresh_job_id,
                    slot_id="r0-1",
                    status=DraftStatus.EXPORT_PENDING,
                    level="biet",
                    seed=2,
                    reviewer_id=ctx.teacher_id,
                    item_json={
                        "type": "single_choice",
                        "prompt": "Revoked export",
                        "options": [{"id": "a", "text": "1"}],
                        "correct": "a",
                        "evidence": [{"source_id": "missing-source", "source_spans": [[0, 4]]}],
                    },
                ),
            ]
        )

    with TestClient(ctx.app) as client:
        ctx.app.state.database = sqlite_db
        ctx.app.state.core = CoreClient(ctx.http, ctx.settings)
        ctx.app.state.document = HttpDocumentClient(ctx.http, ctx.settings)
        ctx.app.state.learning = HttpLearningClient(ctx.http, ctx.settings)
        monkeypatch.setattr(
            ctx.app.state.runtime, "pipeline", lambda: SimpleNamespace(generator=None)
        )
        monkeypatch.setattr(
            ctx.app.state.runtime, "pipeline", lambda: SimpleNamespace(generator=None)
        )
        counts = client.portal.call(
            run_worker_cycle,
            sqlite_db,
            ctx.app.state.runtime,
            ctx.app.state.core,
            ctx.app.state.document,
            ctx.app.state.learning,
            "corr-worker-1",
        )
        assert counts == {
            "stale_messages": 0,
            "recovered_jobs": 0,
            "jobs_completed": 1,
            "jobs_failed": 0,
            "exports_sent": 1,
            "exports_blocked": 1,
            "exports_failed": 0,
        }

        with sqlite_db.SessionLocal() as session:
            job = session.get(QuizJob, stale_job_id)
            assert job.status == JobStatus.COMPLETED
            assert job.completed_items == 2
            drafts = session.scalars(
                select(QuizItemDraft).where(QuizItemDraft.quiz_job_id == stale_job_id)
            ).all()
            assert {d.slot_id for d in drafts} == {"r0-0", "r0-1"}
            r01 = next(d for d in drafts if d.slot_id == "r0-1")
            assert r01.slot_meta_json["ku_id"] == "ku-1"
            assert session.get(QuizItemDraft, valid_export_id).status == DraftStatus.EXPORTED
            assert session.get(QuizItemDraft, revoked_export_id).status == DraftStatus.BLOCKED

        # Now queue a job for a revoked teacher -> worker marks job FAILED cleanly without crashing
        revoked_job_id = uuid4()
        ctx.state["teacher_allowed"] = False
        with sqlite_db.SessionLocal.begin() as session:
            session.add(
                QuizJob(
                    id=revoked_job_id,
                    school_id=ctx.school_id,
                    class_id=ctx.class_id,
                    requested_by=ctx.teacher_id,
                    status=JobStatus.QUEUED,
                    spec_json={
                        "class_id": str(ctx.class_id),
                        "purpose": "PRACTICE",
                        "grade": 8,
                        "subject": "Toán",
                        "language": "vi",
                        "scope": [
                            {"document_version_id": str(ctx.version_id), "section_paths": []}
                        ],
                        "matrix": [
                            {
                                "section": "Chương 1",
                                "type": "single_choice",
                                "level": "biet",
                                "count": 1,
                            }
                        ],
                    },
                    completed_items=0,
                    total_items=1,
                )
            )
        revoked_counts = client.portal.call(
            run_worker_cycle,
            sqlite_db,
            ctx.app.state.runtime,
            ctx.app.state.core,
            ctx.app.state.document,
            ctx.app.state.learning,
            "corr-worker-2",
        )
        assert revoked_counts["jobs_failed"] == 1
        with sqlite_db.SessionLocal() as session:
            assert session.get(QuizJob, revoked_job_id).status == JobStatus.FAILED
            assert session.get(QuizJob, revoked_job_id).error_code == "QUIZ_JOB_FAILED"

        client.portal.call(ctx.http.aclose)
