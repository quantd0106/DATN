from datetime import timedelta
from uuid import UUID

import pytest
from conftest import (
    ANALYTICS,
    assessment_settings,
    publish_assessment,
    standard_question,
    start_attempt,
    submit_attempt,
)
from sqlalchemy import select

from app.common.audit import AuditEvent
from app.common.db import utcnow
from app.common.outbox import OutboxEvent
from app.modules.appeals import service as appeals
from app.modules.bank.models import ActivityVersion
from app.modules.grading import service as grading
from app.modules.grading.models import GradeRevision


def release(world, path, monkeypatch):
    later = utcnow() + timedelta(hours=2)
    monkeypatch.setattr(grading, "utcnow", lambda: later)
    monkeypatch.setattr(appeals, "utcnow", lambda: later)
    return world.call("POST", path + "/release-results", world.primary, json={}).json()


def test_release_closes_expired_attempt_and_publishes_its_grade(world, monkeypatch):
    now = utcnow()
    path, _ = publish_assessment(
        world,
        settings=assessment_settings(
            closes_at=(now + timedelta(minutes=30)).isoformat(),
            results_at=(now + timedelta(hours=1)).isoformat(),
        ),
    )
    attempt, _ = start_attempt(world, path)
    monkeypatch.setattr(grading, "utcnow", lambda: now + timedelta(minutes=45))
    assert world.call("POST", path + "/release-results", world.primary, json={}).json() == {
        "released_count": 0,
    }
    assert release(world, path, monkeypatch) == {"released_count": 1}
    result = world.call("GET", attempt + "/result", world.student).json()
    assert result["receipt"]["reason"] == "TIMEOUT"
    assert result["effective_grade"]["grade_version"] == 1
    assert result["effective_grade"]["score"] == 0


def test_subjective_release_and_correction(world, monkeypatch):
    manual = {
        "id": "q2",
        "type": "short_answer",
        "prompt": "Show work",
        "points": 5,
        "manual_grading_required": True,
        "rubric": "Explain the theorem",
        "explanation": "Solution",
    }
    path, _ = publish_assessment(world, questions=[standard_question(), manual])
    attempt, _ = start_attempt(world, path)
    submit_attempt(world, attempt)
    hidden = world.call("GET", attempt + "/result", world.student).json()
    assert hidden["effective_grade"] is None and hidden["questions"] == []
    world.call("POST", path + "/release-results", world.assisting, expected=403, json={})
    assert release(world, path, monkeypatch) == {"released_count": 0}
    world.call(
        "POST",
        attempt + "/grade",
        world.assisting,
        expected=422,
        json={"expected_grade_version": 1, "scores": {"q1": 1.0}},
    )
    graded = world.call(
        "POST",
        attempt + "/grade",
        world.assisting,
        json={"expected_grade_version": 1, "scores": {"q2": 3}, "feedback": "Good"},
    ).json()
    assert graded["effective_grade"]["score"] == 8
    assert release(world, path, monkeypatch) == {"released_count": 1}
    assert release(world, path, monkeypatch) == {"released_count": 0}
    world.call(
        "POST",
        attempt + "/grade",
        world.assisting,
        expected=409,
        json={"expected_grade_version": 2, "scores": {"q2": 5}},
    )
    result = world.call("GET", attempt + "/result", world.student).json()
    assert result["released"] is True
    assert result["effective_grade"]["score"] == 8
    assert result["questions"][1]["released_solution"] == "Solution"
    assert "Explain the theorem" not in str(result)
    endpoint = "/api/v1/grades/" + graded["grade_id"] + "/revisions"
    proposal = world.call(
        "POST",
        endpoint,
        world.primary,
        expected=201,
        json={"expected_grade_version": 2, "total_score": 7, "reason": "Review"},
    ).json()
    decision = {"expected_grade_version": 2, "decision": "APPROVE", "reason": "Verified"}
    world.call(
        "POST",
        f"/api/v1/grade-revisions/{proposal['id']}/decision",
        world.primary,
        expected=403,
        json=decision,
    )
    proposal = world.call(
        "POST",
        endpoint,
        world.assisting,
        expected=201,
        json={"expected_grade_version": 2, "scores": {"q2": 4}, "reason": "Review"},
    ).json()
    decide = f"/api/v1/grade-revisions/{proposal['id']}/decision"
    world.call("POST", decide, world.assisting, expected=403, json=decision)
    world.call(
        "POST", decide, world.primary, expected=409, json={**decision, "expected_grade_version": 1}
    )
    approved = world.call("POST", decide, world.primary, json=decision).json()
    assert approved["status"] == "APPROVED" and approved["grade_version"] > 2
    queue = world.call("GET", world.base + "/grading", world.primary).json()
    assert queue["items"][0]["grade_version"] == approved["grade_version"]
    assert queue["items"][0]["graded"] is True
    analytics = f"/internal/v1/classes/{world.core.class_id}/grades/confirmed"
    params = {"school_id": str(world.core.school_id)}
    items = world.call("GET", analytics, headers=ANALYTICS, params=params).json()["items"]
    assert len(items) == 1 and items[0]["topic"] == "Geometry" and items[0]["total_score"] == 9
    assert items[0]["grade_version"] == approved["grade_version"]
    assert (
        world.call(
            "GET",
            analytics,
            headers=ANALYTICS,
            params={
                **params,
                "since_version": approved["grade_version"],
            },
        ).json()["items"]
        == []
    )
    with world.database.SessionLocal() as session:
        events = list(
            session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "grade.revision.confirmed",
                )
                .order_by(OutboxEvent.aggregate_version)
            )
        )
        assert [event.payload_json["grade_version"] for event in events] == [
            2,
            approved["grade_version"],
        ]


@pytest.mark.parametrize(
    ("action", "payload", "scores", "maximum"),
    [
        ("VOID_QUESTION", {}, [0, 0, 0], 0),
        ("GRANT_FULL_CREDIT", {}, [5, 5, 5], 5),
        ("ACCEPT_ADDITIONAL_KEY", {"key": "b"}, [5, 5, 5], 5),
    ],
)
def test_m4_regrades_all_submitted_and_future_attempts(
    world,
    monkeypatch,
    action,
    payload,
    scores,
    maximum,
):
    path, data = publish_assessment(world)
    attempt1, _ = start_attempt(world, path)
    submit_attempt(world, attempt1, "a")
    attempt2, _ = start_attempt(world, path)
    submit_attempt(world, attempt2, "b")
    version_id = UUID(data["version_id"])
    with world.database.SessionLocal() as session:
        version = session.get(ActivityVersion, version_id)
        frozen = version.content_snapshot_json, version.content_sha256
        aid = version.activity_id
        assert (
            grading.compute_effective_assessment_score(session, aid, world.student.id, "BEST") == 5
        )
        assert (
            grading.compute_effective_assessment_score(session, aid, world.student.id, "LATEST")
            == 0
        )
        assert (
            grading.compute_effective_assessment_score(session, aid, world.student.id, "AVERAGE")
            == 2.5
        )
    adjust = world.call(
        "POST",
        world.base + f"/activity-versions/{version_id}/scoring-adjustments",
        world.assisting,
        expected=201,
        json={
            "question_id": "q1",
            "action": action,
            "payload": payload,
            "reason": "Defective item",
        },
    ).json()
    decision_path = f"/api/v1/scoring-adjustments/{adjust['id']}/decision"
    decision = {"decision": "APPROVE", "reason": "Verified defect"}
    world.call("POST", decision_path, world.assisting, expected=403, json=decision)
    assert (
        world.call("POST", decision_path, world.primary, json=decision).json()["regraded_count"]
        == 2
    )
    attempt3, _ = start_attempt(world, path)
    submit_attempt(world, attempt3, "b")
    with world.database.SessionLocal() as session:
        version = session.get(ActivityVersion, version_id)
        assert (version.content_snapshot_json, version.content_sha256) == frozen
        revisions = list(
            session.scalars(
                select(GradeRevision).where(GradeRevision.change_kind == "SCORING_ADJUSTMENT")
            )
        )
        assert len(revisions) == 2
        assert sorted(float(rev.total_score) for rev in revisions) == sorted(scores[:2])
        assert all(rev.max_score == maximum for rev in revisions)
        assert (
            grading.compute_effective_assessment_score(session, aid, world.student.id, "LATEST")
            == scores[2]
        )
    assert release(world, path, monkeypatch) == {"released_count": 3}
    result = world.call("GET", attempt2 + "/result", world.student).json()
    assert result["questions"][0]["maximum"] == maximum
    assert result["effective_grade"]["score"] == scores[1]
    with world.database.SessionLocal() as session:
        events = list(
            session.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "grade.revision.confirmed",
                )
            )
        )
        assert (
            len({(event.aggregate_id, event.aggregate_version) for event in events})
            == len(events)
            == 3
        )


def test_historical_appeal_admin_audit_and_tenant_boundaries(world, monkeypatch):
    path, _ = publish_assessment(world, settings=assessment_settings(appeal_window_days=3))
    attempt, started = start_attempt(world, path)
    submit_attempt(world, attempt)
    release(world, path, monkeypatch)
    core = world.core
    core.students.remove((core.class_id, world.student.id))
    core.former_students.add((core.class_id, world.student.id))
    result_path = f"/api/v1/history/results/{started['attempt_id']}"
    result = world.call("GET", result_path, world.student).json()
    assert result["limited_history"] and result["can_appeal"]
    queue = world.call("GET", world.base + "/grading", world.primary).json()
    assert queue["items"][0]["membership_ended"] is True
    foreign = core.add_user("ADMIN", school_id=core.other_school)
    world.call("GET", result_path, world.other_student, expected=404)
    world.call("GET", result_path, foreign, expected=404)
    wrong_class = path.replace(str(core.class_id), str(core.other_class))
    core.teachers[(core.other_class, world.primary.id)] = "PRIMARY"
    world.call("GET", wrong_class + "/draft", world.primary, expected=404)
    grade_id = result["effective_grade"]["grade_id"]
    appeal_path = world.base + "/appeals"
    body = {
        "target_type": "ASSESSMENT_ATTEMPT",
        "target_id": started["attempt_id"],
        "reason": "Please review",
    }
    world.call("POST", appeal_path, world.other_student, expected=404, json=body)
    appeal = world.call("POST", appeal_path, world.student, expected=201, json=body).json()
    proposal_path = f"/api/v1/grades/{grade_id}/revisions"
    proposal_body = {
        "expected_grade_version": 1,
        "total_score": 4,
        "reason": "Review",
        "appeal_id": appeal["id"],
    }
    world.call("POST", proposal_path, world.admin, expected=400, json=proposal_body)
    world.call(
        "POST",
        proposal_path,
        foreign,
        expected=404,
        headers={"X-Admin-Reason": "USER_REQUEST"},
        json=proposal_body,
    )
    proposal = world.call(
        "POST",
        proposal_path,
        world.admin,
        expected=201,
        headers={"X-Admin-Reason": "USER_REQUEST"},
        json=proposal_body,
    ).json()
    assert proposal["change_kind"] == "ADMIN_EXCEPTION"
    resolve_path = f"{appeal_path}/{appeal['id']}/resolve"
    resolve_body = {"resolution_note": "Reviewed the work", "linked_revision_id": proposal["id"]}
    world.call("POST", resolve_path, world.assisting, expected=409, json=resolve_body)
    world.call(
        "POST",
        f"/api/v1/grade-revisions/{proposal['id']}/decision",
        world.primary,
        json={"expected_grade_version": 1, "decision": "APPROVE", "reason": "Verified"},
    )
    assert (
        world.call("POST", resolve_path, world.assisting, json=resolve_body).json()["status"]
        == "RESOLVED"
    )
    core.teachers.pop((core.class_id, world.assisting.id))
    world.call("GET", world.base + "/grading", world.assisting, expected=403)
    with world.database.SessionLocal() as session:
        audit = session.scalar(select(AuditEvent).where(AuditEvent.reason_code == "USER_REQUEST"))
        assert audit.actor_id == world.admin.id
        assert audit.target_id == proposal["id"] and audit.action == "GRADE_REVISION_PROPOSED"
    monkeypatch.setattr(appeals, "utcnow", lambda: utcnow() + timedelta(days=4))
    world.call("POST", appeal_path, world.student, expected=403, json=body)
