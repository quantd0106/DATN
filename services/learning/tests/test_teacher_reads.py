from datetime import timedelta
from uuid import uuid4

from conftest import (
    assessment_settings,
    publish_assessment,
    publish_assignment,
    standard_question,
    start_attempt,
    submit_assignment,
    submit_attempt,
    upload_pdf,
)

from app.common.db import utcnow
from app.modules.appeals import service as appeals
from app.modules.grading import service as grading


def test_assessment_grading_reads_submitted_version_and_current_evidence(world, monkeypatch):
    settings = assessment_settings()
    manual = {
        "id": "manual",
        "type": "short_answer",
        "prompt": "Explain the proof",
        "points": 3,
        "rubric": "Use the theorem",
        "manual_grading_required": True,
    }
    path, _ = publish_assessment(world, settings=settings, questions=[standard_question(), manual])
    attempt, _ = start_attempt(world, path)
    endpoint = attempt + "/grading"
    world.call("GET", endpoint, world.primary, expected=404)
    submit_attempt(world, attempt)
    original = world.call("GET", endpoint, world.assisting).json()
    assert original["student_id"] == str(world.student.id)
    assert original["student_name"] == world.student.display_name
    assert original["class_id"] == str(world.core.class_id)
    assert original["title"] == settings["title"] and original["version_number"] == 1
    assert original["released"] is False and original["grade_version"] == 1
    assert original["can_approve"] is False and original["pending_proposal"] is None
    assert original["questions"][0]["question"]["key"] == "a"
    assert original["questions"][0]["answer"] == {"question_id": "q1", "value": "a"}
    assert original["questions"][1]["question"]["rubric"] == manual["rubric"]
    assert original["questions"][1]["score"] is None
    assert original["questions"][1]["can_grade"] is True

    world.call(
        "POST",
        attempt + "/grade",
        world.assisting,
        json={"expected_grade_version": 1, "scores": {"manual": 1}, "feedback": "Good proof"},
    )
    later = utcnow() + timedelta(hours=1)
    monkeypatch.setattr(grading, "utcnow", lambda: later)
    monkeypatch.setattr(appeals, "utcnow", lambda: later)
    assert world.call("POST", path + "/release-results", world.primary, json={}).json() == {
        "released_count": 1
    }
    appeal = world.call(
        "POST",
        world.base + "/appeals",
        world.student,
        expected=201,
        json={
            "target_type": "ASSESSMENT_ATTEMPT",
            "target_id": original["attempt_id"],
            "reason": "Check proof",
        },
    ).json()
    revision_path = f"/api/v1/grades/{original['grade_id']}/revisions"
    draft = world.call(
        "POST",
        revision_path,
        world.assisting,
        expected=201,
        json={
            "expected_grade_version": 2,
            "scores": {"manual": 2},
            "reason": "Review",
            "appeal_id": appeal["id"],
        },
    ).json()
    read = world.call("GET", endpoint, world.primary).json()
    assert read["released"] is True and read["can_approve"] is True
    assert read["grade_version"] == 2 and read["effective_grade"]["score"] == 6
    assert read["pending_proposal"]["id"] == draft["id"]
    assert read["pending_proposal"]["score"] == 7
    assert read["appeal_id"] == appeal["id"] and read["appeal_reason"] == appeal["reason"]
    assert [row["score"] for row in read["grade_history"]] == [5, 6]
    assert world.call("GET", endpoint, world.assisting).json()["can_approve"] is False
    queue = world.call("GET", world.base + "/grading", world.primary).json()["items"][0]
    assert queue["released"] is True and queue["effective_score"] == 6
    assert queue["feedback"] == "Good proof"
    assert queue["grade_history"] == read["grade_history"]
    assert queue["appeal_id"] == appeal["id"] and queue["appeal_reason"] == appeal["reason"]

    own = world.call(
        "POST",
        revision_path,
        world.primary,
        expected=201,
        json={"expected_grade_version": 2, "total_score": 6, "reason": "Own review"},
    ).json()
    read = world.call("GET", endpoint, world.primary).json()
    assert read["pending_proposal"]["id"] == own["id"] and read["can_approve"] is False


def test_assignment_grading_reads_pinned_definition_and_only_the_selected_students_work(world):
    path, definition = publish_assignment(
        world, policy={"max_versions": 3, "graded_version": "FIRST"}
    )
    revision = definition["revision"]
    first = submit_assignment(world, path, revision, upload_pdf(world, path, revision)["upload_id"])
    second = submit_assignment(
        world, path, revision, upload_pdf(world, path, revision)["upload_id"]
    )
    other = upload_pdf(world, path, revision, world.other_student)
    world.call(
        "POST",
        path + "/submissions",
        world.other_student,
        expected=201,
        json={
            "expected_revision": revision,
            "upload_id": other["upload_id"],
            "operation_id": str(uuid4()),
        },
    )
    grade = world.call(
        "POST",
        f"/api/v1/submissions/{first['id']}/grade",
        world.assisting,
        json={"expected_grade_version": 0, "score": 8, "feedback": "Good report"},
    ).json()
    appeal = world.call(
        "POST",
        world.base + "/appeals",
        world.student,
        expected=201,
        json={
            "target_type": "ASSIGNMENT_SUBMISSION",
            "target_id": first["id"],
            "reason": "Check report",
        },
    ).json()
    updated = {
        key: definition[key] for key in ("title", "topic", "instructions", "due_at", "policy")
    }
    world.call(
        "PUT",
        path + "/draft",
        world.primary,
        json={**updated, "title": "Updated report", "expected_revision": revision, "publish": True},
    )
    grade_path = path + f"/submissions/{first['id']}/grading"
    detail = world.call("GET", grade_path, world.assisting).json()
    assert detail["assignment"]["title"] == definition["title"]
    assert detail["assignment"]["version_id"] == definition["version_id"]
    assert detail["teaching_role"] == "ASSISTING"
    assert [row["id"] for row in detail["submissions"]] == [first["id"], second["id"]]
    assert detail["submission"]["file"] == first["file"]
    assert detail["submission"]["grade"] == grade
    assert detail["submission"]["grade_history"] == [grade]
    assert detail["appeals"] == [appeal]
    queue = world.call("GET", world.base + "/grading", world.primary).json()["items"]
    items = {row["id"]: row for row in queue}
    assert items[first["id"]]["released"] is True
    assert items[first["id"]]["effective_score"] == 8
    assert items[second["id"]]["released"] is False
    assert items[second["id"]]["effective_score"] is None
    assert items[second["id"]]["feedback"] == ""
    assert items[second["id"]]["grade_history"] == []


def test_recipients_reports_automatic_explicit_and_empty_audiences(world):
    path, definition = publish_assignment(world)
    endpoint = path + "/recipients"
    initial = world.call("GET", endpoint, world.assisting).json()
    assert initial == {
        "assignment_id": definition["id"],
        "class_id": str(world.core.class_id),
        "revision": definition["revision"],
        "audience": [],
        "default_assigned": True,
    }
    for ids in ([str(world.student.id)], []):
        world.call(
            "PUT",
            endpoint,
            world.primary,
            expected=204,
            json={"expected_revision": initial["revision"], "student_ids": ids},
        )
        current = world.call("GET", endpoint, world.assisting).json()
        assert current["revision"] == initial["revision"] + 1
        assert current["audience"] == [{"user_id": user_id, "assigned": True} for user_id in ids]
        assert current["default_assigned"] is False
        initial = current


def test_teacher_reads_enforce_roles_and_the_complete_resource_path(world):
    assessment, _ = publish_assessment(world)
    attempt, _ = start_attempt(world, assessment)
    submit_attempt(world, attempt)
    assignment, definition = publish_assignment(world)
    rev = definition["revision"]
    submission = submit_assignment(
        world, assignment, rev, upload_pdf(world, assignment, rev)["upload_id"]
    )
    unassigned = world.core.add_user("LECTURER")
    other_school = world.core.add_user("LECTURER", school_id=world.core.other_school)
    world.core.teachers[world.core.other_class, world.primary.id] = "PRIMARY"
    paths = [
        attempt + "/grading",
        assignment + f"/submissions/{submission['id']}/grading",
        assignment + "/recipients",
    ]
    for path in paths:
        world.call("GET", path, world.student, expected=403)
        world.call("GET", path, unassigned, expected=403)
        world.call("GET", path, other_school, expected=404)
        world.call(
            "GET",
            path.replace(str(world.core.class_id), str(world.core.other_class)),
            world.primary,
            expected=404,
        )
    world.call(
        "GET",
        paths[0].replace(assessment.split("/")[-1], str(uuid4())),
        world.primary,
        expected=404,
    )
    world.call("GET", paths[1].replace(definition["id"], str(uuid4())), world.primary, expected=404)
