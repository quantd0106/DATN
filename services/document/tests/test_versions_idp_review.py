import ast
import json
import logging
from datetime import datetime, timedelta
from functools import partial
from hashlib import sha256
from io import BytesIO
from uuid import UUID, uuid4

import pymupdf
import pytest
from conftest import DOCUMENTS, INTERNAL_KEY, SERVICE_ROOT, bearer, class_documents, internal_grants
from docx import Document as WordDocument
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from sqlalchemy import select

from app import worker
from app.common.config import Settings
from app.common.db import transaction, utc_now
from app.common.outbox import record_outbox_event
from app.common.storage import LocalVolumeStorage
from app.model_registry import AuditEvent, DocumentVersion, OutboxEvent
from app.modules.versions import processing
from app.modules.versions.idp_runner import IdpRunOutcome, _read_outcome, run_idp_for_version

PUBLISHED = "document.version.published"
LESSON = "The water cycle moves water through evaporation, condensation, and precipitation."
REVIEW_SUMMARY = {
    "review_item_count": 1,
    "flag_codes": ["TABLE_STRUCTURE_REVIEW"],
    "flagged_pages": [1],
}


@pytest.fixture(autouse=True)
def work_root(monkeypatch, tmp_path_factory):
    scratch = tmp_path_factory.mktemp("w")
    monkeypatch.setattr(processing.tempfile, "gettempdir", lambda: str(scratch))
    return scratch / "doc-worker"


@pytest.fixture
def storage(tmp_path_factory):
    return LocalVolumeStorage(tmp_path_factory.mktemp("v") / "volume")


def format_upload(file_type):
    stream = BytesIO()
    if file_type == "PDF":
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60, 85), LESSON)
            return "lesson.pdf", pdf.tobytes(), "application/pdf"
    if file_type == "PPTX":
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[1])
        slide.shapes.title.text = "The water cycle"
        slide.placeholders[1].text = LESSON
        image = BytesIO()
        Image.new("RGB", (40, 40), "blue").save(image, format="PNG")
        slide.shapes.add_picture(image, Inches(1), Inches(4))
        deck.save(stream)
        return (
            "lesson.pptx",
            stream.getvalue(),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    if file_type == "DOCX":
        book = WordDocument()
        book.add_heading("The water cycle", level=1)
        book.add_paragraph(LESSON)
        table = book.add_table(rows=2, cols=2)
        table.cell(0, 0).merge(table.cell(0, 1)).text = "Stages"
        table.cell(1, 0).text = "Evaporation"
        table.cell(1, 1).text = "Condensation"
        book.save(stream)
        return (
            "lesson.docx",
            stream.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if file_type == "MARKDOWN":
        return "lesson.markdown", f"# The water cycle\n\n{LESSON}\n".encode(), "text/markdown"
    return "lesson.txt", LESSON.encode(), "text/plain"


def published_events(db_session):
    with db_session() as session:
        return list(session.scalars(select(OutboxEvent).where(OutboxEvent.event_type == PUBLISHED)))


def get_version(db_session, version_id):
    with db_session() as session:
        return session.get(DocumentVersion, UUID(version_id))


def version_url(document_id, version_id):
    return f"{DOCUMENTS}/{document_id}/versions/{version_id}"


def append_version(client, document_id, content=b"Version two source"):
    response = client.post(
        f"{DOCUMENTS}/{document_id}/versions",
        files={"file": ("next.txt", content, "text/plain")},
        headers=bearer("primary"),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def grant_version(client, core, document_id):
    response = client.post(
        f"{class_documents(core.class_id)}/grants",
        json={"document_id": document_id},
        headers=bearer("primary"),
    )
    assert response.status_code == 201, response.text


def idp_outcome(status, text="Version two reader text"):
    if status == "FAILED":
        return IdpRunOutcome(
            "FAILED", error_code="IDP_QUALITY_FAILED", error_message="Tài liệu chưa đạt yêu cầu."
        )
    canonical = {
        "schema_version": "idp-canonical-2",
        "pages": [{"page_number": None, "page_id": 1, "blocks": [{"type": "Text", "text": text}]}],
    }
    return IdpRunOutcome(
        status,
        canonical_bytes=json.dumps(canonical).encode(),
        canonical_schema_version=canonical["schema_version"],
        page_count=1,
        review_summary=REVIEW_SUMMARY if status == "NEEDS_REVIEW" else None,
    )


@pytest.mark.parametrize("file_type", ["PDF", "PPTX", "DOCX", "TXT", "MARKDOWN"])
def test_doc_t03_real_idp_round_trip(
    file_type, upload, client, core, db_session, storage, work_root
):
    response = upload(*format_upload(file_type))
    assert response.status_code == 201, response.text
    document = response.json()
    doc_id, version_id = document["id"], document["latest_version"]["id"]
    output = {}

    def capture_real_idp(source, out_dir, **kwargs):
        outcome = run_idp_for_version(source, out_dir, **kwargs)
        if outcome.status != "FAILED":
            output["canonical"] = (out_dir / "canonical.json").read_bytes()
            output["assets"] = {
                path.relative_to(out_dir).as_posix(): path.read_bytes()
                for path in (out_dir / "assets").rglob("*")
                if path.is_file()
            }
        return outcome

    assert worker.process_next_version(db_session, storage, Settings(), runner=capture_real_idp)
    version = get_version(db_session, version_id)
    assert version.processing_status in {"SUCCESS", "NEEDS_REVIEW"}, (
        file_type,
        version.error_code,
        version.error_message,
    )
    raw = output["canonical"]
    key = f"canonical/{doc_id}/{version_id}"
    assert version.canonical_storage_key == f"{key}.json"
    assert version.canonical_sha256 == sha256(raw).hexdigest()
    assert storage.read_bytes(version.canonical_storage_key) == raw
    canonical = json.loads(raw)
    assert version.canonical_schema_version == canonical["schema_version"]
    assert version.page_count == len(canonical["pages"])
    if file_type == "PPTX":
        assert output["assets"]
    for relative, data in output["assets"].items():
        assert storage.read_bytes(f"{key}/{relative}") == data
    if version.processing_status == "NEEDS_REVIEW":
        assert published_events(db_session) == []
        accepted = client.post(
            f"{version_url(doc_id, version_id)}/accept", headers=bearer("primary")
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["acceptance_mode"] == "OWNER_ACCEPTED"
    else:
        assert version.acceptance_mode == "AUTO_PUBLISHED"
    events = published_events(db_session)
    assert len(events) == 1
    assert events[0].payload_json["canonical_sha256"] == sha256(raw).hexdigest()

    grant_version(client, core, doc_id)
    reader = client.get(
        f"{class_documents(core.class_id)}/{doc_id}/reader", headers=bearer("student")
    )
    assert reader.status_code == 200, reader.text
    pages = reader.json()["pages"]
    assert len(pages) == version.page_count
    assert any(LESSON in paragraph for page in pages for paragraph in page["paragraphs"])
    artifact = client.get(
        f"/internal/v1/documents/{doc_id}/versions/{version_id}/canonical", headers=INTERNAL_KEY
    )
    assert artifact.status_code == 200
    assert artifact.content == raw
    assert artifact.json() == canonical
    assert not (work_root / version_id).exists()


def assert_v1_serves(client, core, doc_id, v1_id, v2_id):
    root = class_documents(core.class_id)
    headers = bearer("student")
    listing = client.get(root, headers=headers)
    assert listing.status_code == 200
    assert [(row["id"], row["serving_version"]) for row in listing.json()["items"]] == [(doc_id, 1)]
    reader = client.get(f"{root}/{doc_id}/reader", headers=headers)
    assert reader.status_code == 200
    assert reader.json()["viewing_version"] == reader.json()["serving_version"] == 1
    assert "Canonical lesson" in reader.json()["pages"][0]["paragraphs"]
    download = client.get(f"{root}/{doc_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.content == b"Teaching material"
    grants = client.get(internal_grants(core.class_id), headers=INTERNAL_KEY)
    assert grants.status_code == 200
    assert grants.json()["items"][0]["serving_version_id"] == v1_id
    detail = client.get(f"{DOCUMENTS}/{doc_id}", headers=bearer("primary"))
    assert detail.status_code == 200
    assert [(v["id"], v["is_serving"], v["is_superseded"]) for v in detail.json()["versions"]] == [
        (v2_id, False, False),
        (v1_id, True, False),
    ]


@pytest.mark.parametrize("status", ["FAILED", "NEEDS_REVIEW"])
def test_doc_t05_previous_success_serves_until_owner_accepts(
    status, client, core, shared, db_session, storage
):
    doc_id, v1_id = shared["id"], shared["latest_version"]["id"]
    v2_id = append_version(client, doc_id)
    outcome = idp_outcome(status)
    assert worker.process_next_version(
        db_session, storage, Settings(), runner=lambda *a, **kw: outcome
    )
    version = get_version(db_session, v2_id)
    assert version.processing_status == status
    assert published_events(db_session) == []
    assert_v1_serves(client, core, doc_id, v1_id, v2_id)
    if status == "FAILED":
        assert version.error_code == "IDP_QUALITY_FAILED"
        return

    assert version.review_summary_json == REVIEW_SUMMARY
    assert storage.read_bytes(
        version.canonical_storage_key, expected_sha256=version.canonical_sha256
    )
    path = f"{version_url(doc_id, v2_id)}/accept"
    request_id = str(uuid4())
    accepted = client.post(
        path, headers={**bearer("primary"), "X-Correlation-ID": request_id}, json={}
    )
    assert accepted.status_code == 200, accepted.text
    dto = accepted.json()
    assert dto["processing_status"] == "SUCCESS"
    assert dto["acceptance_mode"] == "OWNER_ACCEPTED"
    assert dto["accepted_by_user_id"] == str(core.users["primary"].id)
    assert dto["accepted_at"] == dto["updated_at"]
    assert dto["is_serving"] is True
    assert client.post(path, headers=bearer("primary")).status_code == 409
    detail = client.get(f"{DOCUMENTS}/{doc_id}", headers=bearer("primary")).json()
    assert [(v["id"], v["is_serving"], v["is_superseded"]) for v in detail["versions"]] == [
        (v2_id, True, False),
        (v1_id, False, True),
    ]
    with db_session() as session:
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "DOCUMENT_VERSION_ACCEPTED")
        ).one()
        assert audit.target_type == "document_version"
        assert audit.target_id == UUID(v2_id)
        assert audit.actor_id == core.users["primary"].id
        assert audit.correlation_id == UUID(request_id)
        assert audit.metadata_json == {"version_number": 2}
    (event,) = published_events(db_session)
    assert event.aggregate_id == UUID(doc_id)
    assert event.aggregate_version == 2
    assert datetime.fromisoformat(event.payload_json["published_at"]) == datetime.fromisoformat(
        dto["accepted_at"]
    )
    assert {
        field: value for field, value in event.payload_json.items() if field != "published_at"
    } == {
        "event_id": str(event.id),
        "schema_version": 1,
        "document_id": doc_id,
        "school_id": str(core.school_id),
        "version_id": v2_id,
        "version_number": 2,
        "acceptance_mode": "OWNER_ACCEPTED",
        "canonical_sha256": version.canonical_sha256,
        "canonical_schema_version": "idp-canonical-2",
    }


@pytest.mark.parametrize(("action", "state"), [("accept", "NEEDS_REVIEW"), ("retry", "FAILED")])
def test_version_actions_require_owner_lecturer(
    action, state, client, core, shared, db_session, storage
):
    doc_id = shared["id"]
    v2_id = append_version(client, doc_id)
    outcome = idp_outcome(state)
    assert worker.process_next_version(
        db_session, storage, Settings(), runner=lambda *a, **kw: outcome
    )
    core.users["other-lecturer"] = core.users["assisting"].model_copy(update={"id": uuid4()})
    for token, status in (
        ("assisting", 404),
        ("other-lecturer", 404),
        ("other-school-teacher", 404),
        ("student", 403),
        ("admin", 403),
        ("schoolless", 403),
    ):
        response = client.post(f"{version_url(doc_id, v2_id)}/{action}", headers=bearer(token))
        assert response.status_code == status, (token, response.text)
    for path in (version_url(doc_id, uuid4()), version_url(uuid4(), v2_id)):
        assert client.post(f"{path}/{action}", headers=bearer("primary")).status_code == 404
    assert get_version(db_session, v2_id).processing_status == state
    assert published_events(db_session) == []


@pytest.mark.parametrize(
    ("action", "state"),
    [
        ("accept", "PENDING"),
        ("accept", "PROCESSING"),
        ("accept", "SUCCESS"),
        ("accept", "FAILED"),
        ("retry", "PENDING"),
        ("retry", "PROCESSING"),
        ("retry", "SUCCESS"),
        ("retry", "NEEDS_REVIEW"),
    ],
)
def test_version_actions_reject_wrong_state(action, state, client, upload, set_version_state):
    doc = upload().json()
    version_id = doc["latest_version"]["id"]
    set_version_state(version_id, state)
    response = client.post(
        f"{version_url(doc['id'], version_id)}/{action}", headers=bearer("primary")
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


@pytest.mark.parametrize("damage", ["deleted", "checksum", "missing-metadata"])
def test_accept_fails_closed_if_canonical_unavailable(damage, client, upload, db_session, storage):
    doc = upload().json()
    version_id = doc["latest_version"]["id"]
    assert worker.process_next_version(
        db_session, storage, Settings(), runner=lambda *a, **kw: idp_outcome("NEEDS_REVIEW")
    )
    version = get_version(db_session, version_id)
    if damage == "deleted":
        storage.delete(version.canonical_storage_key)
    elif damage == "checksum":
        storage.save_bytes(version.canonical_storage_key, b"Corrupted")
    else:
        with db_session() as session, transaction(session):
            session.get(DocumentVersion, version.id).canonical_schema_version = None
    response = client.post(
        f"{version_url(doc['id'], version_id)}/accept", headers=bearer("primary")
    )
    assert response.status_code in {409, 503}
    assert get_version(db_session, version_id).processing_status == "NEEDS_REVIEW"
    assert published_events(db_session) == []


@pytest.mark.parametrize("empty_body", [False, True])
def test_owner_retries_failed_version(empty_body, client, upload, core, db_session, storage):
    doc = upload().json()
    version_id = doc["latest_version"]["id"]
    with db_session() as session, transaction(session):
        version = session.get(DocumentVersion, UUID(version_id))
        version.processing_status = "FAILED"
        version.error_code = "IDP_QUALITY_FAILED"
        version.error_message = "Tài liệu chưa đạt yêu cầu."
        version.review_summary_json = REVIEW_SUMMARY
        version.available_at = utc_now() + timedelta(days=1)
    response = client.post(
        f"{version_url(doc['id'], version_id)}/retry",
        headers=bearer("primary"),
        **({"json": {}} if empty_body else {}),
    )
    assert response.status_code == 200, response.text
    dto = response.json()
    assert dto["processing_status"] == "PENDING"
    assert dto["error_code"] is dto["error_message"] is dto["review_summary"] is None
    assert get_version(db_session, version_id).available_at <= utc_now()
    with db_session() as session:
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "DOCUMENT_VERSION_RETRIED")
        ).one()
        assert audit.actor_id == core.users["primary"].id
        assert audit.target_id == UUID(version_id)
        assert audit.metadata_json == {"version_number": 1}
    assert published_events(db_session) == []
    assert worker.process_next_version(
        db_session, storage, Settings(), runner=lambda *a, **kw: idp_outcome("SUCCESS")
    )
    assert get_version(db_session, version_id).processing_status == "SUCCESS"


@pytest.mark.parametrize("max_attempts", [1, 3])
def test_crashed_worker_lease_recovery(
    max_attempts, client, core, shared, db_session, storage, work_root
):
    doc_id, v1_id = shared["id"], shared["latest_version"]["id"]
    v2_id = append_version(client, doc_id)
    config = Settings(document_worker_max_attempts=max_attempts)

    def crash(source, out_dir, **kwargs):
        with db_session() as session, transaction(session):
            version = session.get(DocumentVersion, UUID(v2_id))
            assert version.processing_status == "PROCESSING"
            assert version.attempt_count == 1
            assert version.available_at > utc_now()
            version.available_at = utc_now() - timedelta(seconds=1)
        assert source.read_bytes() == b"Version two source"
        out_dir.mkdir()
        (out_dir / "partial").write_bytes(b"incomplete")
        raise RuntimeError("crashed mid-processing")

    with pytest.raises(RuntimeError, match="crashed mid-processing"):
        worker.process_next_version(db_session, storage, config, runner=crash)
    orphan = get_version(db_session, v2_id)
    assert orphan.processing_status == "PROCESSING"
    assert orphan.available_at < utc_now()
    assert not (work_root / v2_id).exists()
    assert published_events(db_session) == []
    assert_v1_serves(client, core, doc_id, v1_id, v2_id)
    assert storage.read_bytes(orphan.storage_key, expected_sha256=orphan.source_sha256)
    if max_attempts == 1:
        assert not worker.process_next_version(db_session, storage, config)
        failed = get_version(db_session, v2_id)
        assert failed.processing_status == "FAILED"
        assert failed.error_code == "WORKER_LEASE_EXPIRED"
        assert failed.error_message == "Quá thời gian xử lý tài liệu. Vui lòng thử lại."
        assert published_events(db_session) == []
        assert_v1_serves(client, core, doc_id, v1_id, v2_id)
        path = f"{version_url(doc_id, v2_id)}/retry"
        assert client.post(path, headers=bearer("primary")).status_code == 200

    assert worker.process_next_version(
        db_session, storage, config, runner=lambda *a, **kw: idp_outcome("SUCCESS")
    )
    recovered = get_version(db_session, v2_id)
    assert recovered.processing_status == "SUCCESS"
    assert recovered.attempt_count == 2
    assert len(published_events(db_session)) == 1
    assert not (work_root / v2_id).exists()


def test_expired_runner_cannot_replace_reclaimed_canonical(upload, db_session, storage, work_root):
    version_id = upload().json()["latest_version"]["id"]
    config = Settings()
    latest = idp_outcome("SUCCESS", "Current lease result")
    stale = idp_outcome("SUCCESS", "Expired lease result")

    def finish_late(source, out_dir, **kwargs):
        with db_session() as session, transaction(session):
            session.get(DocumentVersion, UUID(version_id)).available_at = utc_now() - timedelta(1)
        assert worker.process_next_version(
            db_session, storage, config, runner=lambda *a, **kw: latest
        )
        assert source.is_file()
        return stale

    assert worker.process_next_version(db_session, storage, config, runner=finish_late)
    version = get_version(db_session, version_id)
    assert version.processing_status == "SUCCESS"
    assert version.attempt_count == 2
    assert storage.read_bytes(version.canonical_storage_key) == latest.canonical_bytes
    assert len(published_events(db_session)) == 1
    assert not (work_root / version_id).exists()


def test_worker_verifies_source_integrity_before_idp(upload, db_session, storage):
    version_id = upload().json()["latest_version"]["id"]
    version = get_version(db_session, version_id)
    storage.save_bytes(version.storage_key, b"X" * version.file_size_bytes)

    def forbidden_runner(*args, **kwargs):
        pytest.fail("Checksum must be verified before invoking IDP")

    assert worker.process_next_version(db_session, storage, Settings(), runner=forbidden_runner)
    version = get_version(db_session, version_id)
    assert version.processing_status == "FAILED"
    assert version.error_code == "SOURCE_STORAGE_ERROR"
    assert published_events(db_session) == []


def test_canonical_checksum_mismatch_prevents_publish(upload, db_session, storage, monkeypatch):
    version_id = upload().json()["latest_version"]["id"]
    monkeypatch.setattr(storage, "save_bytes", lambda *args, **kwargs: "invalid")
    assert worker.process_next_version(
        db_session, storage, Settings(), runner=lambda *a, **kw: idp_outcome("SUCCESS")
    )
    version = get_version(db_session, version_id)
    assert version.processing_status == "FAILED"
    assert version.error_code == "CANONICAL_STORAGE_ERROR"
    assert published_events(db_session) == []


@pytest.mark.parametrize("delete_before_finalization", [False, True])
def test_document_deletion_before_or_after_finalization(
    delete_before_finalization, client, upload, db_session, storage, monkeypatch
):
    in_flight = upload().json()
    doc_id = in_flight["id"]
    version_id = in_flight["latest_version"]["id"]
    deleted = upload().json()

    def delete(document):
        response = client.delete(f"{DOCUMENTS}/{document['id']}", headers=bearer("primary"))
        assert response.status_code == 204

    delete(deleted)

    def unexpected_save(*args, **kwargs):
        pytest.fail("Canonical storage must be skipped for a deleted document")

    if delete_before_finalization:
        monkeypatch.setattr(storage, "save_bytes", unexpected_save)

    def delete_during_processing(*args, **kwargs):
        if delete_before_finalization:
            delete(in_flight)
        return idp_outcome("SUCCESS")

    assert worker.process_next_version(
        db_session, storage, Settings(), runner=delete_during_processing
    )
    # A delete that acquires the document lock second runs after finalization commits.
    if not delete_before_finalization:
        delete(in_flight)

    assert not worker.process_next_version(db_session, storage, Settings())
    assert get_version(db_session, deleted["latest_version"]["id"]).processing_status == "PENDING"
    version = get_version(db_session, version_id)
    events = published_events(db_session)
    if delete_before_finalization:
        assert version.processing_status == "PROCESSING"
        assert events == []
    else:
        assert version.processing_status == "SUCCESS"
        assert [event.payload_json["version_id"] for event in events] == [version_id]
    artifact = client.get(
        f"/internal/v1/documents/{doc_id}/versions/{version_id}/canonical", headers=INTERNAL_KEY
    )
    assert artifact.status_code == 404


def test_idp_review_summary_uses_only_codes_page_numbers_and_counts(tmp_path):
    canonical = {
        "schema_version": "idp-canonical-2",
        "pages": [
            {
                "page_id": 2,
                "review": [{"code": "FAST_OCR_REVIEW", "snippet": "Private document text"}],
                "review_flags": [{"code": "C:/private/doc.txt", "trace": "private stack trace"}],
                "blocks": [
                    {
                        "block_id": "b1",
                        "type": "Text",
                        "text": "Private document text",
                        "review_flags": [{"code": "TABLE_STRUCTURE_REVIEW", "path": "/tmp/secret"}],
                    }
                ],
            }
        ],
    }
    raw = json.dumps(canonical, indent=2).encode()
    (tmp_path / "canonical.json").write_bytes(raw)
    (tmp_path / "report.json").write_text(
        json.dumps(
            {
                "ingestion_auto_publish_pass": False,
                "ingestion_review_assisted_pass": True,
            }
        )
    )
    (tmp_path / "ingestion_report.json").write_text(
        json.dumps(
            {
                "checks": {"low_confidence_flagged": {"pass": True, "value": "sensitive"}},
                "quality": {"review_pages": [0, -1, True, "2", 3]},
            }
        )
    )
    (tmp_path / "review_queue.jsonl").write_text(
        json.dumps(
            {
                "page": 2,
                "block_id": "b1",
                "flags": [{"code": "TABLE_STRUCTURE_REVIEW"}],
            }
        )
        + "\n"
    )
    outcome = _read_outcome(tmp_path, {}, "DOCX")
    assert outcome.status == "NEEDS_REVIEW"
    assert outcome.canonical_bytes == raw
    assert outcome.review_summary == {
        "review_item_count": 2,
        "flag_codes": ["FAST_OCR_REVIEW", "TABLE_STRUCTURE_REVIEW"],
        "flagged_pages": [2, 3],
    }


def test_idp_input_errors_are_safe(tmp_path):
    source = tmp_path / "private.invalid"
    source.write_bytes(b"")
    outcome = run_idp_for_version(source, tmp_path / "out", config=Settings(), file_type="TXT")
    assert outcome.status == "FAILED"
    assert outcome.error_code == "UNSUPPORTED_FILE_TYPE"
    assert "private" not in outcome.error_message
    assert str(tmp_path) not in outcome.error_message


@pytest.mark.parametrize(("prior_attempts", "seconds"), [(0, 2), (8, 300)])
def test_outbox_relay_failure_backs_off_then_succeeds(db_session, prior_attempts, seconds):
    with db_session() as session, transaction(session):
        record_outbox_event(session, PUBLISHED, uuid4(), 1, {})
        session.flush()
        event = session.scalars(select(OutboxEvent)).one()
        event.attempts = prior_attempts
        event_id = event.id

    def unavailable(event):
        raise RuntimeError("private transport details")

    before = utc_now()
    assert worker.relay_outbox_events(db_session, publisher=unavailable) == 0
    after = utc_now()
    with db_session() as session, transaction(session):
        event = session.get(OutboxEvent, event_id)
        assert event.published_at is None
        assert event.attempts == prior_attempts + 1
        assert event.last_error_code == "OUTBOX_PUBLISH_FAILED"
        delay = timedelta(seconds=seconds)
        assert before + delay <= event.available_at <= after + delay
    delivered = []
    assert worker.relay_outbox_events(db_session, publisher=delivered.append) == 0
    assert delivered == []
    with db_session() as session, transaction(session):
        session.get(OutboxEvent, event_id).available_at = utc_now()
    assert worker.relay_outbox_events(db_session, publisher=delivered.append) == 1
    assert worker.relay_outbox_events(db_session, publisher=delivered.append) == 0
    (event,) = delivered
    assert event.id == event_id
    assert event.published_at is not None
    assert event.last_error_code is None


def test_cli_once_processes_jobs_and_logs_only_outbox_metadata(
    upload, db_session, storage, monkeypatch, caplog
):
    doc = upload().json()
    extra = upload().json()
    version_id = doc["latest_version"]["id"]
    with db_session() as session, transaction(session):
        record_outbox_event(session, "test.event", uuid4(), 1, {"sample": "private payload"})
    monkeypatch.setattr(worker, "SessionLocal", db_session)
    monkeypatch.setattr(worker, "get_storage", lambda: storage)
    monkeypatch.setattr(
        worker,
        "process_next_version",
        partial(worker.process_next_version, runner=lambda *a, **kw: idp_outcome("SUCCESS")),
    )
    with caplog.at_level(logging.INFO, logger=worker.__name__):
        assert worker.main(["--once"]) == 0
    assert get_version(db_session, version_id).processing_status == "SUCCESS"
    assert get_version(db_session, extra["latest_version"]["id"]).processing_status == "SUCCESS"
    with db_session() as session:
        events = list(session.scalars(select(OutboxEvent)))
        assert len(events) == 3
        assert all(event.published_at is not None for event in events)
    messages = [record.getMessage() for record in caplog.records if record.name == worker.__name__]
    assert len(messages) == 3
    for message in messages:
        metadata = json.loads(message.removeprefix("Document outbox event "))
        assert set(metadata) == {"event_id", "event_type", "aggregate_id", "aggregate_version"}
    assert "private payload" not in caplog.text


def test_database_url_aliases(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DOCUMENT_DATABASE_URL", "postgresql://document@localhost/document_alias")
    assert Settings(_env_file=None).database_url.endswith("/document_alias")
    monkeypatch.setenv("DATABASE_URL", "postgresql://document@localhost/document_primary")
    assert Settings(_env_file=None).database_url.endswith("/document_primary")
    assert Settings(database_url="sqlite://", _env_file=None).database_url == "sqlite://"


def test_document_app_does_not_import_ai_pipeline_modules():
    forbidden = {
        "chunking",
        "embedding",
        "indexing",
        "search",
        "retrieval",
        "generation",
        "packing",
        "citations",
    }
    imports = []
    for path in (SERVICE_ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                imports.extend(
                    (path, name) for name in names if forbidden.intersection(name.split("."))
                )
    assert imports == []
