from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

from conftest import publish_assessment, start_attempt
from sqlalchemy import func, select

from app.modules.assessment.models import AssessmentAttempt
from app.modules.assessment.schemas import SubmitRequest
from app.modules.assessment.service import submit
from app.modules.grading.models import Grade, GradeRevision


def test_retry_storm_confirms_one_receipt_and_one_grade(world):
    path, published = publish_assessment(world)
    attempt, started = start_attempt(world, path)
    world.call(
        "PUT",
        attempt + "/answers",
        world.student,
        json={
            "expected_revision": 0,
            "answers": {"q1": {"value": "a"}},
        },
    )
    scope = world.core.school_id, world.core.class_id, UUID(published["assessment_id"])
    attempt_id = UUID(started["attempt_id"])
    same_operation = uuid4()
    workers = 12
    start = Barrier(workers)

    def submit_many(worker):
        with world.database.SessionLocal() as session:
            start.wait(timeout=15)
            return [
                submit(
                    session,
                    scope,
                    attempt_id,
                    world.student.id,
                    SubmitRequest(
                        expected_revision=1,
                        operation_id=same_operation if (worker + i) % 2 else uuid4(),
                    ),
                )
                for i in range(84)
            ]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        receipts = [r for batch in executor.map(submit_many, range(workers)) for r in batch]
    assert len(receipts) == 1008
    assert all(receipt == receipts[0] for receipt in receipts)
    assert receipts[0]["receipt"]["saved_revision"] == 1
    assert world.call("GET", attempt + "/reconcile", world.student).json() == receipts[0]
    with world.database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(AssessmentAttempt)) == 1
        assert session.scalar(select(func.count()).select_from(Grade)) == 1
        assert session.scalar(select(func.count()).select_from(GradeRevision)) == 1
        assert session.get(AssessmentAttempt, attempt_id).receipt_json == receipts[0]["receipt"]
