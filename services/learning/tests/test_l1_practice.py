from uuid import UUID, uuid4

from conftest import INTERNAL
from sqlalchemy import func, select

from app.modules.bank.models import ActivityVersion, QuestionItem
from app.modules.grading.models import Grade


def test_quiz_import_wire_and_idempotency(world):
    draft_id = str(uuid4())
    grant = world.document.grant
    body = {
        "school_id": str(world.core.school_id),
        "class_id": str(world.core.class_id),
        "idempotency_key": draft_id,
        "approved_by": str(world.primary.id),
        "item": {
            "type": "true_false_4",
            "context": "Classify these claims",
            "statements": [{"text": f"Claim {i}", "truth": i % 2 == 0} for i in range(4)],
        },
        "level": "easy",
        "generated_by": "AI",
        "edited_after_generation": False,
        "provenance": {"job_id": str(uuid4()), "item_draft_id": draft_id},
        "sources": [{"source_id": grant.source_id, "source_spans": [[0, 5]]}],
    }
    path = "/internal/v1/quiz-items/import"
    headers = {**INTERNAL, "Idempotency-Key": draft_id}
    first = world.call("POST", path, expected=201, headers=headers, json=body).json()
    replay = world.call("POST", path, headers=headers, json=body).json()
    assert (
        first
        == replay
        == {
            "school_id": body["school_id"],
            "class_id": body["class_id"],
            "idempotency_key": draft_id,
            "learning_item_id": first["learning_item_id"],
        }
    )
    world.call("POST", path, expected=409, headers=headers, json={**body, "level": "hard"})
    world.call(
        "POST", path, expected=409, headers={**headers, "Idempotency-Key": str(uuid4())}, json=body
    )
    world.call("POST", path, expected=422, headers=INTERNAL, json=body)
    with world.database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(QuestionItem)) == 1
    items = world.call("GET", world.base + "/question-bank", world.primary).json()["items"]
    assert items[0]["id"] == first["learning_item_id"]
    assert items[0]["type"] == "true_false"
    assert items[0]["question"]["isReviewed"] is True


def test_practice_review_four_types_solutions_and_no_official_grade(world):
    entry = world.call(
        "POST",
        world.base + "/practices",
        world.primary,
        expected=201,
        json={"title": "Review", "topic": "Fractions"},
    ).json()
    path = world.base + "/practices/" + entry["practice_id"]
    grant = world.document.grant
    questions = [
        {
            "id": "single",
            "type": "single_choice",
            "prompt": "Pick one",
            "options": [{"id": "a", "text": "First"}, {"id": "b", "text": "Second"}],
            "correctOptionId": "a",
            "explanation": "Teacher explanation",
            "isReviewed": True,
            "sourceRef": {
                "source_id": grant.source_id,
                "document_id": str(grant.document_id),
                "version": grant.version_number,
                "locator": "page 2",
                "title": grant.title,
            },
        },
        {
            "id": "multi",
            "type": "multiple_choice",
            "prompt": "Pick two",
            "options": [{"id": "a", "text": "First"}, {"id": "b", "text": "Second"}],
            "correctOptionIds": ["a", "b"],
        },
        {
            "id": "truth",
            "type": "true_false",
            "prompt": "True or false",
            "statements": [{"id": "s1", "text": "A fact", "isCorrect": True}],
        },
        {
            "id": "short",
            "type": "short_answer",
            "prompt": "Enter a fraction",
            "expectedAnswer": "3/4",
        },
    ]
    saved = world.call(
        "PUT",
        path + "/draft",
        world.primary,
        json={
            "expected_revision": int(entry["revision"]),
            "questions": questions,
        },
    ).json()
    revision = int(saved["revision"])
    world.call(
        "POST", path + "/publish", world.primary, expected=422, json={"expected_revision": revision}
    )
    for q in questions:
        reviewed = world.call(
            "POST",
            path + "/questions/" + q["id"] + "/review",
            world.primary,
            json={"expected_revision": revision, "approved": True},
        ).json()
        revision = int(reviewed["revision"])
    published = world.call(
        "POST", path + "/publish", world.primary, json={"expected_revision": revision}
    ).json()
    version_id = UUID(published["published_version_id"])
    with world.database.SessionLocal() as session:
        before = session.get(ActivityVersion, version_id).content_sha256
    started = world.call(
        "POST", path + "/attempts", world.student, expected=201, json={"intent": "start"}
    ).json()
    attempt = path + "/attempts/" + started["attempt_id"]
    detail = world.call("GET", attempt, world.student).json()
    assert {q["type"] for q in detail["questions"]} == {
        "single_choice",
        "multiple_choice",
        "true_false",
        "short_answer",
    }
    assert detail["questions"][0]["sourceRef"]["accessible"] is True
    assert "correctOptionId" not in str(detail["questions"])
    assert "isCorrect" not in str(detail["questions"])
    assert "expectedAnswer" not in str(detail["questions"])
    assert "Teacher explanation" not in str(detail)
    world.document.grant = grant.model_copy(update={"version_number": 2})
    assert not world.call("GET", attempt, world.student).json()["questions"][0]["sourceRef"][
        "accessible"
    ]
    world.document.grant = grant
    wrong = world.call(
        "POST",
        attempt + "/check",
        world.student,
        json={"question_id": "single", "answer": {"singleChoiceValue": "b"}},
    ).json()
    assert wrong["isCorrect"] is False
    world.document.revoked = True
    masked = world.call("GET", attempt, world.student).json()
    assert masked["questions"][0]["sourceRef"]["accessible"] is False
    assert "page 2" not in str(masked)
    explanation = world.call(
        "POST", attempt + "/explain-mistake", world.student, json={"question_id": "single"}
    ).json()
    assert explanation == {"explanation": "Teacher explanation", "citations": [], "fallback": True}
    assert world.ai.explanations == []
    world.document.revoked = False
    world.call("POST", attempt + "/explain-mistake", world.student, json={"question_id": "single"})
    assert len(world.ai.explanations) == 1
    assert "student_id" not in world.ai.explanations[0]
    for qid, value in (
        ("single", "a"),
        ("multi", ["b", "a"]),
        ("truth", {"s1": True}),
        ("short", "0.75"),
    ):
        feedback = world.call(
            "POST",
            attempt + "/check",
            world.student,
            json={"question_id": qid, "answer": {"value": value}},
        ).json()
        assert feedback["isCorrect"] is True
    world.call(
        "POST",
        attempt + "/explain-mistake",
        world.student,
        expected=409,
        json={"question_id": "single"},
    )
    world.call("POST", attempt + "/finish", world.student, json={})
    world.call(
        "PUT",
        path + "/draft",
        world.primary,
        expected=409,
        json={"expected_revision": revision + 1, "questions": questions},
    )
    world.call("POST", path + "/versions", world.primary, expected=201, json={})
    assert world.call("GET", attempt, world.student).json()["publishedVersionNumber"] == 1
    with world.database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Grade)) == 0
        assert session.get(ActivityVersion, version_id).content_sha256 == before
