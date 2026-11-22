from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from app.common.db import Database, utcnow
from app.common.errors import AppError
from app.config import Settings
from app.integrations.ai_client import GradeHintResponse
from app.integrations.core_client import (
    AccessDecision,
    ClassAction,
    ClassInfo,
    CurrentUser,
    DeadlineProjectionReceipt,
    NotificationDispatchReceipt,
)
from app.integrations.document_client import ActiveGrant, GrantSnapshot
from app.main import create_app
from app.model_registry import load_metadata

JWT_KEY = "learning-fixture-signing-key-for-tests"
INTERNAL = {"X-Service-Key": "ai-fixture-key"}
ANALYTICS = {"X-Service-Key": "analytics-fixture-key"}


class FakeCore:
    def __init__(self):
        self.school_id, self.other_school = uuid4(), uuid4()
        self.class_id, self.other_class = uuid4(), uuid4()
        self.classes = {self.class_id: self.school_id, self.other_class: self.school_id}
        self.users = {}
        self.teachers = {}
        self.students = set()
        self.former_students = set()
        self.archived = set()
        self.locked = set()
        self.offline = False
        self.deadlines_offline = False
        self.deadlines = {}
        self.deadline_calls = []
        self.notification_ids = set()

    def add_user(self, role, teaching_role=None, *, school_id=None, current=True):
        user = CurrentUser(
            id=uuid4(),
            school_id=school_id or self.school_id,
            roles=(role,),
            status="ACTIVE",
            display_name=teaching_role or role,
        )
        self.users[user.id] = user
        if teaching_role:
            self.teachers[self.class_id, user.id] = teaching_role
        if role == "STUDENT" and current:
            self.students.add((self.class_id, user.id))
        return user

    async def verify_session(self, token, user_id, correlation_id):
        if self.offline:
            raise AppError(503)
        if user_id in self.locked or user_id not in self.users:
            raise AppError(401)
        return self.users[user_id]

    async def get_class(self, class_id, user, token, correlation_id):
        if self.classes.get(class_id) != user.school_id:
            raise AppError(404)
        return ClassInfo(
            id=class_id,
            school_id=user.school_id,
            name="Class 10",
            grade_level=10,
            status="ARCHIVED" if class_id in self.archived else "ACTIVE",
            teaching_role=self.teachers.get((class_id, user.id)),
        )

    async def check_access(self, class_id, user, action, correlation_id):
        if self.offline:
            raise AppError(503)
        school = self.classes.get(class_id)
        role = self.teachers.get((class_id, user.id))
        key = class_id, user.id
        active = class_id not in self.archived
        allowed = {
            ClassAction.PARTICIPATE_CURRENT_CLASS: active
            and (key in self.students or role is not None),
            ClassAction.READ_CLASS_CONTENT: key in self.students or role is not None,
            ClassAction.READ_OWN_HISTORY: key in self.students or key in self.former_students,
            ClassAction.GRADE_SUBMITTED_WORK: active and role is not None,
        }.get(action, False)
        scoped = school is not None and school == user.school_id
        return AccessDecision(
            allowed=scoped and allowed,
            class_id=class_id,
            user_id=user.id,
            action=action,
            school_id=school if scoped else None,
            teaching_role=role if scoped else None,
            class_status=("ACTIVE" if active else "ARCHIVED") if scoped else None,
        )

    async def upsert_learning_deadline(self, **kwargs):
        self.deadline_calls.append(kwargs)
        if self.deadlines_offline:
            raise AppError(503)
        key = kwargs["external_ref"]
        old = self.deadlines.get(key)
        if old is None or kwargs["version"] > old.version:
            self.deadlines[key] = DeadlineProjectionReceipt(
                event_id=uuid4(), version=kwargs["version"]
            )
        return self.deadlines[key]

    async def publish_learning_notification(self, *, source_event_id, **kwargs):
        deduped = source_event_id in self.notification_ids
        self.notification_ids.add(source_event_id)
        return NotificationDispatchReceipt(outbox_event_id=source_event_id, deduped=deduped)


class FakeDocument:
    def __init__(self):
        self.revoked = False
        self.offline = False
        self.grant = ActiveGrant(
            document_id=uuid4(),
            document_version_id=uuid4(),
            source_id="learning-test-source",
            title="Shared material",
            version_number=1,
        )

    async def active_grants(self, class_id, school_id, correlation_id):
        if self.offline:
            raise AppError(503)
        return GrantSnapshot(
            school_id=school_id,
            class_id=class_id,
            grants=() if self.revoked else (self.grant,),
        )


class FakeAi:
    def __init__(self):
        self.hints = []
        self.explanations = []

    async def explain_mistake(self, **kwargs):
        self.explanations.append(kwargs)
        raise AppError(503)

    async def grade_hint(self, **kwargs):
        self.hints.append(kwargs)
        return GradeHintResponse(feedback="Add evidence", limitations="Reviewed by a teacher")


def authorization(user, *, signed_roles=None, secret=JWT_KEY, expired=False):
    now = utcnow()
    claims = {
        "sub": str(user.id),
        "roles": signed_roles or list(user.roles),
        "session_version": 0,
        "iat": now - timedelta(minutes=5),
        "exp": now + timedelta(minutes=-1 if expired else 5),
    }
    return {"Authorization": "Bearer " + jwt.encode(claims, secret, algorithm="HS256")}


@dataclass
class World:
    client: TestClient
    database: Database
    core: FakeCore
    document: FakeDocument
    ai: FakeAi
    primary: CurrentUser
    assisting: CurrentUser
    student: CurrentUser
    other_student: CurrentUser
    admin: CurrentUser

    @property
    def base(self):
        return f"/api/v1/classes/{self.core.class_id}"

    def call(self, method, path, user=None, *, expected=200, headers=None, **kwargs):
        auth = authorization(user) if user else {}
        response = self.client.request(method, path, headers={**auth, **(headers or {})}, **kwargs)
        assert response.status_code == expected, response.text
        return response


@pytest.fixture
def database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'learning.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 20},
        execution_options={"schema_translate_map": {"learning": None}},
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    load_metadata().create_all(engine)
    db = Database(engine=engine)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def world(database, tmp_path):
    core, document, ai = FakeCore(), FakeDocument(), FakeAi()
    config = Settings(
        core_jwt_secret=JWT_KEY,
        learning_internal_service_secret=INTERNAL["X-Service-Key"],
        learning_analytics_service_secret=ANALYTICS["X-Service-Key"],
        blob_storage_dir=tmp_path / "blobs",
        assignment_max_upload_bytes=1024,
    )
    primary = core.add_user("LECTURER", "PRIMARY")
    assisting = core.add_user("LECTURER", "ASSISTING")
    student, other_student = core.add_user("STUDENT"), core.add_user("STUDENT")
    admin = core.add_user("ADMIN")
    app = create_app(config, database=database, core=core, document=document, ai=ai)
    with TestClient(app) as client:
        yield World(
            client, database, core, document, ai, primary, assisting, student, other_student, admin
        )


def standard_question():
    return {
        "id": "q1",
        "type": "single_choice",
        "prompt": "Pick the correct option",
        "points": 5,
        "options": [{"id": "a", "text": "First"}, {"id": "b", "text": "Second"}],
        "key": "a",
        "explanation": "Approved solution",
    }


def assessment_settings(**overrides):
    return {
        "title": "Timed assessment",
        "topic": "Geometry",
        "code": "G1",
        "opens_at": (utcnow() - timedelta(minutes=5)).isoformat(),
        "closes_at": (utcnow() + timedelta(minutes=40)).isoformat(),
        "duration_minutes": 15,
        "max_attempts": 3,
        "grading_policy": "BEST",
        **overrides,
    }


def publish_assessment(world, *, questions=None, settings=None):
    body = world.call(
        "POST", world.base + "/assessments", world.assisting, expected=201, json={}
    ).json()
    path = world.base + "/assessments/" + body["assessment_id"]
    questions = questions if questions is not None else [standard_question()]
    draft = world.call(
        "PUT",
        path + "/draft",
        world.assisting,
        json={
            "expected_revision": 1,
            "settings": settings or assessment_settings(),
            "questions": questions,
        },
    ).json()
    for question in questions:
        draft = world.call(
            "POST",
            path + "/questions/" + question["id"] + "/review",
            world.assisting,
            json={"expected_revision": draft["revision"]},
        ).json()
    pending = world.call(
        "POST", path + "/propose", world.assisting, json={"expected_revision": draft["revision"]}
    ).json()
    published = world.call(
        "POST",
        path + "/decision",
        world.primary,
        json={
            "expected_revision": pending["revision"],
            "decision": "APPROVE",
            "reason": "",
        },
    ).json()
    return path, published


def start_attempt(world, path, student=None):
    row = world.call(
        "POST", path + "/attempts", student or world.student, expected=201, json={}
    ).json()
    return path + "/attempts/" + row["attempt_id"], row


def submit_attempt(world, path, answer="a"):
    world.call(
        "PUT",
        path + "/answers",
        world.student,
        json={"expected_revision": 0, "answers": {"q1": {"value": answer}}},
    )
    return world.call(
        "POST",
        path + "/submit",
        world.student,
        json={
            "expected_revision": 1,
            "operation_id": str(uuid4()),
        },
    ).json()["receipt"]


def publish_assignment(world, *, policy=None, due_at=None, student_ids=None):
    entry = world.call(
        "POST", world.base + "/assignments", world.primary, expected=201, json={}
    ).json()
    path = world.base + "/assignments/" + entry["assignment_id"]
    rev = entry["revision"]
    if student_ids is not None:
        world.call(
            "PUT",
            path + "/recipients",
            world.primary,
            expected=204,
            json={
                "expected_revision": rev,
                "student_ids": [str(student_id) for student_id in student_ids],
            },
        )
        rev += 1
    payload = {
        "expected_revision": rev,
        "title": "Lab report",
        "topic": "Geometry",
        "instructions": "Show work",
        "due_at": (due_at or utcnow() + timedelta(hours=2)).isoformat(),
        "policy": policy or {},
        "publish": True,
    }
    return path, world.call("PUT", path + "/draft", world.primary, json=payload).json()


def upload_pdf(world, path, revision, student=None, *, content=b"%PDF-1.7\nTest"):
    return world.call(
        "POST",
        path + "/uploads",
        student or world.student,
        expected=201,
        files={"file": ("test.pdf", content, "application/pdf")},
        data={"expected_revision": revision},
    ).json()


def submit_assignment(world, path, revision, upload_id, operation_id=None):
    return world.call(
        "POST",
        path + "/submissions",
        world.student,
        expected=201,
        json={
            "expected_revision": revision,
            "upload_id": upload_id,
            "operation_id": str(operation_id or uuid4()),
            "note": "Report is ready",
        },
    ).json()


def expire_attempt(database, attempt_id):
    from app.modules.assessment.models import AssessmentAttempt

    with database.SessionLocal.begin() as session:
        session.get(AssessmentAttempt, UUID(attempt_id)).ends_at = utcnow() - timedelta(seconds=1)
