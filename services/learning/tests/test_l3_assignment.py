import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from conftest import publish_assignment, submit_assignment, upload_pdf
from sqlalchemy import func, select

from app.background_worker import run_worker_cycle
from app.common.db import utcnow
from app.common.outbox import OutboxEvent
from app.modules.assignment.models import AssignmentUpload
from app.modules.assignment.service import discard_expired_uploads
from app.modules.grading.models import Grade, GradeRevision


def test_audience_and_blob_validation(world):
    path, definition = publish_assignment(world, student_ids=[world.student.id])
    revision = definition["revision"]
    files = {"file": ("test.pdf", b"%PDF-1.0", "application/pdf")}
    world.call(
        "POST",
        path + "/uploads",
        world.other_student,
        expected=403,
        files=files,
        data={"expected_revision": revision},
    )
    world.call("GET", path, world.other_student, expected=403)
    assert world.call("GET", world.base + "/assignments", world.other_student).json()["items"] == []
    for content, expected in ((b"not a pdf", 422), (b"%PDF-" + b"a" * 1024, 413)):
        world.call(
            "POST",
            path + "/uploads",
            world.student,
            expected=expected,
            files={"file": ("test.pdf", content, "application/pdf")},
            data={"expected_revision": revision},
        )
    upload = upload_pdf(world, path, revision)
    assert upload["preview_only"] is True
    assert upload["file"]["type"] == "application/pdf"
    fallback_pdf = world.call(
        "POST",
        path + "/uploads",
        world.student,
        expected=201,
        files={"file": ("fallback.pdf", b"%PDF-1.7\nFallback", "application/octet-stream")},
        data={"expected_revision": revision},
    ).json()
    assert fallback_pdf["file"]["type"] == "application/pdf"
    fallback_md = world.call(
        "POST",
        path + "/uploads",
        world.student,
        expected=201,
        files={"file": ("notes.md", b"# Heading\nSolution", "application/octet-stream")},
        data={"expected_revision": revision},
    ).json()
    assert fallback_md["file"]["type"] == "text/markdown"
    row = submit_assignment(world, path, revision, upload["upload_id"])
    assert row["is_late"] is False
    download = f"/api/v1/submissions/{row['id']}/files/{upload['upload_id']}"
    for user in (world.student, world.assisting):
        response = world.call("GET", download, user)
        assert response.content == b"%PDF-1.7\nTest"
        assert response.headers["content-disposition"].startswith("attachment")
        assert response.headers["x-content-type-options"] == "nosniff"
    world.call("GET", download, world.other_student, expected=404)
    world.call("GET", download, world.admin, expected=404)


@pytest.mark.parametrize("graded_policy", ["FIRST", "LATEST"])
def test_deadlines_count_and_grading_version(world, graded_policy):
    now = utcnow()
    path, data = publish_assignment(
        world,
        due_at=now - timedelta(hours=1),
        policy={
            "max_versions": 2,
            "allow_late": True,
            "late_until": (now + timedelta(minutes=30)).isoformat(),
            "resubmit_until": (now + timedelta(hours=2)).isoformat(),
            "graded_version": graded_policy,
        },
    )
    revision = data["revision"]
    upload1, upload2, upload3 = [upload_pdf(world, path, revision) for _ in range(3)]
    operation = uuid4()
    first = submit_assignment(world, path, revision, upload1["upload_id"], operation)
    assert first["is_late"] is True
    assert (
        submit_assignment(world, path, revision, upload1["upload_id"], operation)["id"]
        == first["id"]
    )
    world.call(
        "POST",
        path + "/submissions",
        world.student,
        expected=409,
        json={
            "expected_revision": revision,
            "operation_id": str(operation),
            "upload_id": upload2["upload_id"],
        },
    )
    url = path + "/due-overrides"
    world.call(
        "POST",
        url,
        world.primary,
        expected=422,
        json={
            "new_due_at": (now - timedelta(minutes=10)).isoformat(),
            "reason": "Past extension",
        },
    )
    individual = world.call(
        "POST",
        url,
        world.assisting,
        expected=201,
        json={
            "student_id": str(world.student.id),
            "new_due_at": (now + timedelta(hours=1)).isoformat(),
            "reason": "Individual extension",
        },
    ).json()
    assert individual["deadline_version"] == 2
    other = world.call("GET", path, world.other_student).json()["assignment"]
    assert other["due_at"] == data["due_at"]
    second = submit_assignment(world, path, revision, upload2["upload_id"])
    assert second["submission_number"] == 2 and second["is_late"] is False
    world.call(
        "POST",
        path + "/submissions",
        world.student,
        expected=409,
        json={
            "expected_revision": revision,
            "operation_id": str(uuid4()),
            "upload_id": upload3["upload_id"],
        },
    )
    original = world.call(
        "GET",
        path + "/submissions/reconcile",
        world.student,
        params={"operation_id": str(operation)},
    ).json()
    assert original["is_late"] is True and original["submitted_at"] == first["submitted_at"]
    assert original["is_graded_version"] == (graded_policy == "FIRST")
    assert second["is_graded_version"] == (graded_policy == "LATEST")
    target = f"/api/v1/submissions/{first['id']}"
    hint = world.call("POST", target + "/grade-hint", world.assisting, json={}).json()
    assert hint["feedback"] == "Add evidence" and "score" not in hint
    with world.database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Grade)) == 0
    grade = world.call(
        "POST",
        target + "/grade",
        world.assisting,
        json={"expected_grade_version": 0, "score": 9.5, "feedback": "Great work"},
    ).json()
    assert grade["score"] == 9.5 and grade["grade_version"] == 1
    world.call(
        "POST",
        target + "/grade",
        world.assisting,
        expected=409,
        json={"expected_grade_version": 0, "score": 2},
    )
    with world.database.SessionLocal() as session:
        assert session.scalar(select(GradeRevision)).change_kind == "INITIAL_TEACHER"
        events = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "assignment.deadline.extended")
            )
        )
        assert events[0].payload_json["student_id"] == str(world.student.id)
        graded_notices = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "ASSIGNMENT_GRADED")
            )
        )
        assert any(
            evt.payload_json.get("resource_type") == "assignment_submission"
            and evt.payload_json.get("resource_id") == first["id"]
            and evt.payload_json.get("student_id") == str(world.student.id)
            for evt in graded_notices
        )
    override = world.call(
        "POST",
        url,
        world.primary,
        expected=201,
        json={
            "new_due_at": (now + timedelta(hours=2)).isoformat(),
            "reason": "Class extension",
        },
    ).json()
    assert override["deadline_version"] == 3
    asyncio.run(run_worker_cycle(world.database, world.core, str(uuid4())))
    assert [call["version"] for call in world.core.deadline_calls] == [1, 3]
    assert all(call["starts_at"] < call["ends_at"] for call in world.core.deadline_calls)


def test_late_and_resubmission_cutoffs_and_unused_upload_cleanup(world):
    now = utcnow()
    past, definition = publish_assignment(world, due_at=now - timedelta(minutes=10))
    world.call(
        "POST",
        past + "/uploads",
        world.student,
        expected=409,
        files={"file": ("report.pdf", b"%PDF-1.0", "application/pdf")},
        data={"expected_revision": definition["revision"]},
    )
    path, data = publish_assignment(
        world,
        policy={
            "max_versions": 2,
            "resubmit_until": (now - timedelta(minutes=1)).isoformat(),
        },
    )
    rev = data["revision"]
    used, unused = upload_pdf(world, path, rev), upload_pdf(world, path, rev)
    submit_assignment(world, path, rev, used["upload_id"])
    world.call(
        "POST",
        path + "/submissions",
        world.student,
        expected=409,
        json={
            "expected_revision": rev,
            "operation_id": str(uuid4()),
            "upload_id": unused["upload_id"],
        },
    )
    with world.database.SessionLocal.begin() as session:
        for upload in (used, unused):
            session.get(AssignmentUpload, UUID(upload["upload_id"])).expires_at = now - timedelta(
                seconds=1
            )
    with world.database.SessionLocal() as session:
        assert discard_expired_uploads(session, world.client.app.state.blob_store) == 1
        assert session.get(AssignmentUpload, UUID(used["upload_id"])) is not None
        assert session.get(AssignmentUpload, UUID(unused["upload_id"])) is None


def test_assignment_document_attachment_masking_and_draft_delete(world):
    entry = world.call("POST", world.base + "/assignments", world.primary, expected=201, json={}).json()
    draft_path = f"{world.base}/assignments/{entry['assignment_id']}"
    detail = world.call("GET", draft_path, world.primary).json()
    assert len(detail["available_sources"]) == 1
    assert detail["available_sources"][0]["id"] == str(world.document.grant.document_id)

    world.call(
        "PUT",
        draft_path + "/draft",
        world.primary,
        expected=403,
        json={
            "expected_revision": entry["revision"],
            "title": "Homework",
            "topic": "Algebra",
            "instructions": "Read attached PDF",
            "due_at": (utcnow() + timedelta(hours=3)).isoformat(),
            "policy": {},
            "source": {"id": str(uuid4()), "title": "Unauthorized", "version": 1},
            "publish": False,
        },
    )

    saved = world.call(
        "PUT",
        draft_path + "/draft",
        world.primary,
        json={
            "expected_revision": entry["revision"],
            "title": "Draft only",
            "topic": "Algebra",
            "instructions": "Read doc",
            "due_at": (utcnow() + timedelta(hours=3)).isoformat(),
            "policy": {},
            "source": {
                "id": str(world.document.grant.document_id),
                "title": "Override",
                "version": world.document.grant.version_number,
            },
            "publish": False,
        },
    ).json()
    assert saved["source"]["source_id"] == world.document.grant.source_id
    world.call(
        "DELETE",
        f"{world.base}/activities/{entry['assignment_id']}",
        world.primary,
        expected=204,
    )
    world.call("GET", draft_path, world.primary, expected=404)

    pub_entry = world.call("POST", world.base + "/assignments", world.primary, expected=201, json={}).json()
    pub_path = f"{world.base}/assignments/{pub_entry['assignment_id']}"
    world.call(
        "PUT",
        pub_path + "/draft",
        world.primary,
        json={
            "expected_revision": pub_entry["revision"],
            "title": "Published HW",
            "topic": "Geometry",
            "instructions": "Read source doc",
            "due_at": (utcnow() + timedelta(hours=4)).isoformat(),
            "policy": {},
            "source": {
                "id": str(world.document.grant.document_id),
                "title": world.document.grant.title,
                "version": world.document.grant.version_number,
            },
            "publish": True,
        },
    )
    world.call(
        "DELETE",
        f"{world.base}/activities/{pub_entry['assignment_id']}",
        world.primary,
        expected=409,
    )
    student_view = world.call("GET", pub_path, world.student).json()
    assert student_view["assignment"]["source"]["isAccessible"] is True
    assert student_view["assignment"]["source"]["documentId"] == str(world.document.grant.document_id)

    world.document.revoked = True
    masked_view = world.call("GET", pub_path, world.student).json()
    assert masked_view["assignment"]["source"]["isAccessible"] is False
