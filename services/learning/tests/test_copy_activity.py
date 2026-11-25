from datetime import timedelta
from uuid import uuid4

from conftest import (
    assessment_settings,
    publish_assessment,
    publish_assignment,
    standard_question,
    start_attempt,
    submit_attempt,
    submit_assignment,
    upload_pdf,
)

from app.common.db import utcnow
from app.integrations.document_client import GrantSnapshot


def _grant_source_for_target(world, target_class):
    original_active_grants = world.document.active_grants

    async def scoped_grants(class_id, school_id, correlation_id):
        if class_id == target_class:
            return GrantSnapshot(school_id=school_id, class_id=class_id, grants=())
        return await original_active_grants(class_id, school_id, correlation_id)

    return scoped_grants


def test_copy_practice_preserves_reviewed_and_filters_target_grants(world):
    target_class = world.core.other_class
    world.core.teachers[(target_class, world.primary.id)] = "PRIMARY"

    created = world.call(
        "POST",
        f"{world.base}/practices",
        world.primary,
        expected=201,
        json={"title": "Luyện tập Hàm số", "topic": "Hàm số bậc hai"},
    ).json()
    practice_id = created["practice_id"]
    path = f"{world.base}/practices/{practice_id}"

    grant = world.document.grant
    q_reviewed = {
        "id": "q-1",
        "type": "single_choice",
        "prompt": "Câu 1 đã duyệt",
        "points": 1,
        "options": [{"id": "a", "text": "Đúng"}, {"id": "b", "text": "Sai"}],
        "correctOptionId": "a",
        "explanation": "Giải thích",
        "sourceRef": {
            "documentId": str(grant.document_id),
            "documentVersionId": str(grant.document_version_id),
            "source_id": grant.source_id,
            "versionNumber": grant.version_number,
            "documentTitle": grant.title,
            "locator": "Trang 2",
        },
    }
    q_unreviewed = {
        "id": "q-2",
        "type": "single_choice",
        "prompt": "Câu 2 chưa duyệt",
        "points": 1,
        "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
        "correctOptionId": "a",
        "explanation": "Giải thích",
    }

    draft = world.call(
        "PUT",
        f"{path}/draft",
        world.primary,
        json={
            "expected_revision": int(created["revision"]),
            "title": "Luyện tập Hàm số",
            "topic": "Hàm số bậc hai",
            "questions": [q_reviewed, q_unreviewed],
        },
    ).json()
    draft = world.call(
        "POST",
        f"{path}/questions/q-1/review",
        world.primary,
        json={"expected_revision": int(draft["revision"]), "approved": True},
    ).json()

    # Copy when target class does NOT have document grant -> sourceRef stripped on q-1, reviewed preserved
    world.document.active_grants = _grant_source_for_target(world, target_class)
    copied = world.call(
        "POST",
        f"{world.base}/activities/{practice_id}/copy",
        world.primary,
        expected=201,
        json={"target_class_id": str(target_class), "title": "Luyện tập Hàm số (10A2)"},
    ).json()
    assert copied["status"] == "DRAFT"
    assert copied["kind"] == "PRACTICE"
    assert copied["class_id"] == str(target_class)
    assert copied["title"] == "Luyện tập Hàm số (10A2)"
    assert copied["stripped_source_count"] == 1
    assert copied["edit_href"] == f"/teacher/classes/{target_class}/practice/{copied['activity_id']}/edit"

    target_editor = world.call(
        "GET",
        f"/api/v1/classes/{target_class}/practices/{copied['activity_id']}/editor",
        world.primary,
    ).json()
    qs = target_editor["editor"]["questions"]
    assert len(qs) == 2
    assert qs[0]["isReviewed"] is True
    assert "sourceRef" not in qs[0]
    assert qs[1]["isReviewed"] is False
    assert target_editor["editor"]["reviewedCount"] == 1
    assert target_editor["editor"]["isDraft"] is True


def test_copy_assignment_creates_clean_draft_without_submissions_or_audience(world):
    target_class = world.core.other_class
    world.core.teachers[(target_class, world.primary.id)] = "PRIMARY"

    path, published = publish_assignment(
        world,
        policy={"max_versions": 2, "allow_late": True, "new_enrollment": "REVIEW"},
        student_ids=[world.student.id],
    )
    assignment_id = published["assignment_id"]
    grant = world.document.grant
    updated = world.call(
        "PUT",
        f"{path}/draft",
        world.primary,
        json={
            "expected_revision": published["revision"],
            "title": "Bài tập Hình học",
            "topic": "Hình học",
            "instructions": "Trình bày chi tiết",
            "due_at": (utcnow() + timedelta(hours=5)).isoformat(),
            "policy": {
                "max_versions": 2,
                "allow_late": True,
                "late_until": (utcnow() + timedelta(hours=6)).isoformat(),
                "new_enrollment": "REVIEW",
            },
            "source": {
                "id": str(grant.document_id),
                "title": grant.title,
                "version": grant.version_number,
            },
            "publish": True,
        },
    ).json()
    upload = upload_pdf(world, path, updated["revision"])
    submit_assignment(world, path, updated["revision"], upload["upload_id"])

    # Keep grant for target class -> source should be retained
    copied = world.call(
        "POST",
        f"{world.base}/activities/{assignment_id}/copy",
        world.primary,
        expected=201,
        json={"target_class_id": str(target_class)},
    ).json()
    assert copied["status"] == "DRAFT"
    assert copied["kind"] == "ASSIGNMENT"
    assert copied["stripped_source_count"] == 0

    target_detail = world.call(
        "GET",
        f"/api/v1/classes/{target_class}/assignments/{copied['activity_id']}",
        world.primary,
    ).json()
    target_def = target_detail["assignment"]
    assert target_def["status"] == "DRAFT"
    assert target_def["title"] == "Bài tập Hình học"
    assert target_def["due_at"] is None
    assert target_def["policy"]["late_until"] is None
    assert target_def["policy"]["resubmit_until"] is None
    assert target_def["policy"]["new_enrollment"] == "AUTO"
    assert target_def["source"]["document_id"] == str(grant.document_id)
    assert target_detail["submissions"] == []


def test_copy_assessment_preserves_reviewed_and_requires_new_schedule(world):
    target_class = world.core.other_class
    world.core.teachers[(target_class, world.primary.id)] = "PRIMARY"

    q = {
        **standard_question(),
        "source": {
            "id": str(world.document.grant.document_id),
            "title": world.document.grant.title,
            "version": world.document.grant.version_number,
            "locator": "Chương 1",
        },
    }
    path, _published = publish_assessment(world, questions=[q])
    assessment_id = path.rsplit("/", 1)[-1]
    attempt_path, _ = start_attempt(world, path)
    submit_attempt(world, attempt_path)

    world.document.active_grants = _grant_source_for_target(world, target_class)
    copied = world.call(
        "POST",
        f"{world.base}/activities/{assessment_id}/copy",
        world.primary,
        expected=201,
        json={"target_class_id": str(target_class), "title": "Kiểm tra 15p - 10A2"},
    ).json()
    assert copied["status"] == "DRAFT"
    assert copied["kind"] == "ASSESSMENT"
    assert copied["stripped_source_count"] == 1

    target_base = f"/api/v1/classes/{target_class}/assessments/{copied['activity_id']}"
    draft = world.call("GET", f"{target_base}/draft", world.primary).json()
    assert draft["status"] == "DRAFT"
    assert draft["attempt_count"] == 0
    assert draft["settings"]["title"] == "Kiểm tra 15p - 10A2"
    assert draft["settings"]["opens_at"] is None
    assert draft["settings"]["closes_at"] is None
    assert draft["settings"]["code"] == ""
    assert draft["questions"][0]["reviewed"] is True
    assert draft["questions"][0]["source"] is None

    # Propose must fail until schedule & code are saved
    world.call(
        "POST",
        f"{target_base}/propose",
        world.primary,
        expected=422,
        json={"expected_revision": draft["revision"]},
    )

    # Saving new schedule preserves reviewed=True on unmodified questions
    saved = world.call(
        "PUT",
        f"{target_base}/draft",
        world.primary,
        json={
            "expected_revision": draft["revision"],
            "settings": assessment_settings(title="Kiểm tra 15p - 10A2", code="10A2-KT1"),
            "questions": draft["questions"],
        },
    ).json()
    assert saved["questions"][0]["reviewed"] is True


def test_copy_activity_permission_and_same_class_guards(world):
    created = world.call(
        "POST",
        f"{world.base}/practices",
        world.primary,
        expected=201,
        json={"title": "Luyện tập Gốc", "topic": "Đại số"},
    ).json()
    practice_id = created["practice_id"]

    # Teacher not assigned to target_class -> 403
    world.call(
        "POST",
        f"{world.base}/activities/{practice_id}/copy",
        world.primary,
        expected=403,
        json={"target_class_id": str(world.core.other_class)},
    )

    # Assign teacher to target class, check same-class copy -> 422
    world.core.teachers[(world.core.other_class, world.primary.id)] = "PRIMARY"
    world.call(
        "POST",
        f"{world.base}/activities/{practice_id}/copy",
        world.primary,
        expected=422,
        json={"target_class_id": str(world.core.class_id)},
    )

    # Archived target class -> 409 CONFLICT ("Lớp đã lưu trữ...")
    world.core.archived.add(world.core.other_class)
    world.call(
        "POST",
        f"{world.base}/activities/{practice_id}/copy",
        world.primary,
        expected=409,
        json={"target_class_id": str(world.core.other_class)},
    )

    # Cross-school target class -> 404 fail closed
    other_school_class = uuid4()
    world.core.classes[other_school_class] = world.core.other_school
    world.core.teachers[(other_school_class, world.primary.id)] = "PRIMARY"
    world.call(
        "POST",
        f"{world.base}/activities/{practice_id}/copy",
        world.primary,
        expected=404,
        json={"target_class_id": str(other_school_class)},
    )
