import asyncio
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import httpx
import pytest

from app.common.errors import AppError
from app.config import Settings
from app.integrations.core_client import ClassAction, CoreClient, CurrentUser
from app.integrations.document_client import HttpDocumentClient, LocalDevDocumentClient
from app.integrations.learning_client import HttpLearningClient
from app.modules.analytics_export.service import TopicParticipation, summarize_topics
from app.modules.tutor.models import Conversation, Message, MessageRole, MessageStatus, TutorMode
from app.modules.tutor.service import (
    authorize_new_turn,
    history_sources,
    project_history_message,
    sse_message,
)
from app.runtime.executor import RagExecutor
from app.runtime.rag_runtime import CancelRegistry


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("clear", None),
        ("active", 403),
        ("malformed", 503),
        ("mismatch", 503),
        ("down", 503),
        ("archived", 403),
    ],
)
def test_new_tutor_turn_is_fail_closed(decision, expected):
    user = CurrentUser(id=uuid4(), school_id=uuid4(), status="ACTIVE", roles=("STUDENT",))
    class_id, doc_id, version = uuid4(), uuid4(), uuid4()
    touched = []
    settings = Settings(
        core_service_key="core-key",
        document_mode="http",
        document_api_url="http://document.test",
        document_service_key="doc-key",
        learning_mode="http",
        learning_api_url="http://learning.test",
        learning_service_key="learn-key",
        learning_active_assessment_path="/internal/test/assessment",
    )

    def upstream(request):
        touched.append(request)
        assert request.headers.get("Authorization") is None
        if request.url.path.endswith("/access"):
            assert request.url.params["action"] == ClassAction.USE_AI_TUTOR
            assert request.headers["X-Service-Key"] == "core-key"
            return httpx.Response(
                200,
                json={
                    "allowed": decision != "archived",
                    "class_id": str(class_id),
                    "user_id": str(user.id),
                    "school_id": str(user.school_id),
                    "class_status": "ARCHIVED" if decision == "archived" else "ACTIVE",
                    "action": ClassAction.USE_AI_TUTOR.value,
                },
            )
        if request.url.host == "learning.test":
            assert request.headers["X-Service-Key"] == "learn-key"
            if decision == "down":
                raise httpx.ConnectError("host unavailable")
            return httpx.Response(
                200,
                json={
                    "user_id": str(user.id if decision != "mismatch" else uuid4()),
                    "school_id": str(user.school_id),
                    "active_official_assessment": "false"
                    if decision == "malformed"
                    else decision == "active",
                },
            )
        assert request.url.path == f"/internal/v1/classes/{class_id}/documents/active-grants"
        return httpx.Response(
            200,
            json={
                "class_id": str(class_id),
                "school_id": str(user.school_id),
                "grants": [
                    {
                        "document_id": str(doc_id),
                        "document_version_id": str(version),
                        "source_id": "source",
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
            args = (
                CoreClient(http, settings),
                HttpDocumentClient(http, settings),
                HttpLearningClient(http, settings),
                user,
                class_id,
                str(uuid4()),
            )
            if expected is None:
                snapshot = await authorize_new_turn(*args)
                assert snapshot.source_ids == {"source"}
                assert snapshot.grants[0].document_version_id == version
            else:
                with pytest.raises(AppError) as error:
                    await authorize_new_turn(*args)
                assert error.value.status_code == expected
                assert all(req.url.host != "document.test" for req in touched)

    asyncio.run(run())


def test_private_history_masks_revoked_sources_and_step_state():
    owner = CurrentUser(id=uuid4(), school_id=uuid4(), status="ACTIVE", roles=("STUDENT",))
    admin = CurrentUser(id=uuid4(), school_id=owner.school_id, status="ACTIVE", roles=("ADMIN",))
    conversation = Conversation(
        id=uuid4(),
        user_id=owner.id,
        school_id=owner.school_id,
        class_id=uuid4(),
        step_session_json={"plan": "final answer"},
        title="Title",
        default_mode=TutorMode.STEP_BY_STEP,
    )
    message = Message(
        id=uuid4(),
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        mode=TutorMode.STEP_BY_STEP,
        status=MessageStatus.COMPLETED,
        created_at=datetime.now(UTC),
        content="revoked quote",
        pedagogical_hint="final answer",
        rendered_payload_json={
            "status": "answered",
            "missing_parts": ["revoked quote"],
            "claims": [
                {
                    "text": "revoked quote",
                    "basis": "source",
                    "citations": [
                        {"source_id": "old", "quote": "revoked quote", "location": {"page": 2}}
                    ],
                },
                {
                    "text": "current quote",
                    "basis": "source",
                    "citations": [
                        {"source_id": "new", "quote": "current quote", "location": {"page": 3}}
                    ],
                },
            ],
        },
    )
    settings = Settings(core_service_key="core-key")

    def check(request):
        action = request.url.params["action"]
        assert action in (ClassAction.READ_OWN_HISTORY, ClassAction.READ_DOCUMENT_SOURCE)
        return httpx.Response(
            200,
            json={
                "allowed": action == ClassAction.READ_OWN_HISTORY,
                "class_id": str(conversation.class_id),
                "user_id": str(owner.id),
                "school_id": str(owner.school_id),
                "action": action,
                "class_status": "ARCHIVED",
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(check)) as http:
            core = CoreClient(http, settings)
            doc = LocalDevDocumentClient({}, {})
            for caller in (admin, owner.model_copy(update={"school_id": uuid4()})):
                with pytest.raises(AppError) as error:
                    await history_sources(core, doc, caller, conversation, str(uuid4()))
                assert error.value.status_code == 404
            assert not await history_sources(core, doc, owner, conversation, str(uuid4()))

    asyncio.run(run())
    wire = sse_message(project_history_message(message, frozenset({"new"}))).decode()
    assert "revoked quote" not in wire
    assert "final answer" not in wire
    assert "current quote" in wire and "source_access_revoked" in wire
    assert wire.startswith("event: message\ndata: ") and wire.endswith("\n\n")
    assert message.rendered_payload_json["claims"][0]["text"] == "revoked quote"


def test_topic_summary_deduplication_and_complementary_suppression():
    school, klass, small, overlapping, safe = (uuid4() for _ in range(5))
    students = [uuid4() for _ in range(10)]
    rows = [TopicParticipation(school, klass, student, overlapping) for student in students[:5]]
    rows.extend(TopicParticipation(school, klass, students[0], small) for _ in range(10))
    rows.extend(TopicParticipation(school, klass, student, safe) for student in students[5:])
    result = summarize_topics(rows, school_id=school, class_id=klass)
    assert [(item.topic_id, item.students) for item in result] == [(safe, 5)]
    with pytest.raises(ValueError):
        summarize_topics(rows, school_id=school, class_id=uuid4())


def test_executor_retains_capacity_until_cancelled_request_worker_finishes():
    started, release = Event(), Event()

    def blocking():
        started.set()
        if not release.wait(5):
            raise RuntimeError("test worker timed out")

    async def run():
        executor = RagExecutor(1, 0.05)
        try:
            task = asyncio.create_task(executor.run(blocking))
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(AppError) as error:
                await executor.run(lambda: None)
            assert error.value.status_code == 503
            release.set()
            assert await executor.run(lambda: 42) == 42
        finally:
            release.set()
            await executor.close()

    asyncio.run(run())


def test_runtime_cancellation_is_scoped_to_active_operations():
    registry = CancelRegistry()
    active, unrelated = uuid4(), uuid4()
    with registry.track(active) as cancelled:
        assert not registry.cancel(unrelated) and not cancelled.is_set()
        assert registry.cancel(active) and cancelled.is_set()
    assert not registry.cancel(active)
