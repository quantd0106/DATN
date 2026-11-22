import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from conftest import (
    INTERNAL,
    assessment_settings,
    expire_attempt,
    publish_assessment,
    standard_question,
    start_attempt,
)
from sqlalchemy import select

from app.background_worker import run_worker_cycle
from app.common.db import utcnow
from app.common.outbox import OutboxEvent
from app.modules.assessment.models import AssessmentAttempt
from app.modules.bank.models import ActivityVersion
from app.modules.grading.models import GradeRevision


def test_draft_sources_follow_current_grants_and_document_availability(world):
    created = world.call(
        "POST", world.base + "/assessments", world.primary, expected=201, json={}
    ).json()
    path = f"{world.base}/assessments/{created['assessment_id']}/draft"
    grant = world.document.grant
    sources = [
        {
            "document_id": str(grant.document_id),
            "document_version_id": str(grant.document_version_id),
            "source_id": grant.source_id,
            "title": grant.title,
            "version": grant.version_number,
            "locator": "",
            "accessible": True,
        }
    ]
    assert world.call("GET", path, world.assisting).json()["available_sources"] == sources
    saved = world.call(
        "PUT",
        path,
        world.primary,
        json={"expected_revision": 1, "settings": assessment_settings(), "questions": []},
    ).json()
    assert saved["available_sources"] == sources

    world.document.offline = True
    assert world.call("GET", path, world.assisting).json()["available_sources"] == []


def test_primary_publication_and_pinned_version(world):
    body = world.call(
        "POST", world.base + "/assessments", world.assisting, expected=201, json={}
    ).json()
    path = world.base + "/assessments/" + body["assessment_id"]
    question = {**standard_question(), "reviewed": True}
    settings = assessment_settings()
    draft = world.call(
        "PUT",
        path + "/draft",
        world.assisting,
        json={"expected_revision": 1, "settings": settings, "questions": [question]},
    ).json()
    world.call(
        "POST",
        path + "/propose",
        world.assisting,
        expected=422,
        json={"expected_revision": draft["revision"]},
    )
    reviewed = world.call(
        "POST",
        path + "/questions/q1/review",
        world.assisting,
        json={"expected_revision": draft["revision"]},
    ).json()
    pending = world.call(
        "POST", path + "/propose", world.assisting, json={"expected_revision": reviewed["revision"]}
    ).json()
    decision = {"expected_revision": pending["revision"], "decision": "APPROVE", "reason": ""}
    world.call("POST", path + "/decision", world.assisting, expected=403, json=decision)
    first = world.call("POST", path + "/decision", world.primary, json=decision).json()
    world.call(
        "PUT",
        path + "/draft",
        world.primary,
        expected=409,
        json={
            "expected_revision": first["revision"],
            "settings": settings,
            "questions": [question],
        },
    )

    attempt, started = start_attempt(world, path)
    server_now = datetime.fromisoformat(started["server_now"])
    assert datetime.fromisoformat(started["ends_at"]) == server_now + timedelta(minutes=15)
    with world.database.SessionLocal() as session:
        frozen = session.get(ActivityVersion, UUID(first["version_id"]))
        original = frozen.content_snapshot_json, frozen.content_sha256
        assert frozen.deadline_version == 1

    draft = world.call("POST", path + "/versions", world.assisting, expected=201, json={}).json()
    v2 = world.call(
        "PUT",
        path + "/draft",
        world.assisting,
        json={
            "expected_revision": draft["revision"],
            "settings": settings,
            "questions": [{**question, "prompt": "A revised prompt"}],
        },
    ).json()
    reviewed = world.call(
        "POST",
        path + "/questions/q1/review",
        world.assisting,
        json={"expected_revision": v2["revision"]},
    ).json()
    pending = world.call(
        "POST", path + "/propose", world.assisting, json={"expected_revision": reviewed["revision"]}
    ).json()
    published = world.call(
        "POST",
        path + "/decision",
        world.primary,
        json={
            "expected_revision": pending["revision"],
            "decision": "APPROVE",
        },
    ).json()
    assert published["version_number"] == 2
    detail = world.call("GET", attempt, world.student).json()
    assert detail["version_id"] == first["version_id"]
    assert detail["questions"][0]["prompt"] == standard_question()["prompt"]
    assert "key" not in detail["questions"][0]
    assert "explanation" not in detail["questions"][0]
    with world.database.SessionLocal() as session:
        frozen = session.get(ActivityVersion, UUID(first["version_id"]))
        assert (frozen.content_snapshot_json, frozen.content_sha256) == original
        assert session.get(ActivityVersion, UUID(published["version_id"])).deadline_version == 2


def test_ack_conflict_lazy_auto_submit_and_d6(world):
    closes_at = utcnow() + timedelta(minutes=5)
    path, _ = publish_assessment(
        world, settings=assessment_settings(closes_at=closes_at.isoformat())
    )
    attempt, started = start_attempt(world, path)
    assert datetime.fromisoformat(started["ends_at"]) == closes_at
    lock_path = "/internal/v1/assessment-lock"
    params = {"user_id": str(world.student.id), "school_id": str(world.core.school_id)}
    assert world.call("GET", lock_path, headers=INTERNAL, params=params).json() == {
        **params,
        "active_official_assessment": True,
    }
    assert not world.call(
        "GET",
        lock_path,
        headers=INTERNAL,
        params={
            **params,
            "school_id": str(world.core.other_school),
        },
    ).json()["active_official_assessment"]
    saved = world.call(
        "PUT",
        attempt + "/answers",
        world.student,
        json={
            "expected_revision": 0,
            "answers": {"q1": {"value": "a"}},
        },
    ).json()
    assert saved["saved_revision"] == 1
    invalid = {"expected_revision": 0, "answers": {"q1": {"value": "b"}}}
    world.call("PUT", attempt + "/answers", world.student, expected=409, json=invalid)
    assert world.call("GET", attempt + "/reconcile", world.student).json()["state"] == "IN_PROGRESS"
    expire_attempt(world.database, started["attempt_id"])
    expired = world.call(
        "PUT",
        attempt + "/answers",
        world.student,
        expected=409,
        json={
            **invalid,
            "expected_revision": 1,
        },
    ).json()
    receipt = expired["receipt"]
    assert (
        receipt["reason"] == "TIMEOUT"
        and receipt["saved_revision"] == 1
        and receipt["saved_answer_count"] == 1
    )
    assert not world.call("GET", lock_path, headers=INTERNAL, params=params).json()[
        "active_official_assessment"
    ]
    reconciled = world.call("GET", attempt + "/reconcile", world.student).json()
    assert reconciled == {"state": "CONFIRMED", "receipt": receipt}
    assert world.call("GET", attempt, world.student).json()["saved_answers"]["q1"]["value"] == "a"
    with world.database.SessionLocal() as session:
        row = session.get(AssessmentAttempt, UUID(started["attempt_id"]))
        assert row.status == "AUTO_SUBMITTED"
        rev = session.scalar(select(GradeRevision))
        assert rev.change_kind == "INITIAL_AUTO" and rev.total_score == 5


def test_d6_itself_lazy_closes_expired_attempt(world):
    path, _ = publish_assessment(world)
    attempt, started = start_attempt(world, path)
    expire_attempt(world.database, started["attempt_id"])
    lock = world.call(
        "GET",
        "/internal/v1/assessment-lock",
        headers=INTERNAL,
        params={
            "user_id": str(world.student.id),
            "school_id": str(world.core.school_id),
        },
    ).json()
    assert lock["active_official_assessment"] is False
    assert world.call("GET", attempt, world.student).json()["status"] == "AUTO_SUBMITTED"


def test_worker_sweeps_and_retries_deadline_dispatch(world):
    path, _ = publish_assessment(world)
    _, started = start_attempt(world, path)
    expire_attempt(world.database, started["attempt_id"])
    correlation = str(uuid4())
    world.core.deadlines_offline = True
    first = asyncio.run(run_worker_cycle(world.database, world.core, correlation))
    assert first == {
        "expired_attempts": 1,
        "deadlines_sent": 0,
        "deadlines_failed": 1,
        "notifications_sent": 0,
        "notifications_failed": 0,
    }
    with world.database.SessionLocal.begin() as session:
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "calendar.deadline.upsert")
        )
        assert event.retry_count == 1 and event.status == "PENDING"
        event.next_retry_at = utcnow() - timedelta(seconds=1)
    world.core.deadlines_offline = False
    assert (
        asyncio.run(run_worker_cycle(world.database, world.core, correlation))["deadlines_sent"]
        == 1
    )
    call = world.core.deadline_calls[-1]
    assert call["correlation_id"] == correlation and call["version"] == 1
    assert call["external_ref"] == "learning:activity:" + path.rsplit("/", 1)[-1]
    assert (
        asyncio.run(run_worker_cycle(world.database, world.core, correlation))["deadlines_sent"]
        == 0
    )
    assert len(world.core.deadline_calls) == 2


def test_existing_attempt_after_transfer_uses_configured_policy(world):
    path, _ = publish_assessment(world)
    attempt, _ = start_attempt(world, path)
    key = world.core.class_id, world.student.id
    world.core.students.remove(key)
    world.core.former_students.add(key)
    world.call("POST", path + "/attempts", world.student, expected=403, json={})
    assert world.call("GET", attempt, world.student).json()["status"] == "IN_PROGRESS"
    world.client.app.state.settings.transfer_pending_attempt_policy = "auto_submit_on_transfer"
    assert world.call("GET", attempt, world.student).json()["status"] == "AUTO_SUBMITTED"
