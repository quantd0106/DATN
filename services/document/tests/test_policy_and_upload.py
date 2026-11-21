from hashlib import sha256
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf
import pytest
from conftest import DOCUMENTS, INTERNAL_KEY, bearer, class_documents, internal_grants
from sqlalchemy import select

from app.model_registry import AuditEvent, Document, DocumentVersion, OutboxEvent


def pdf(page_count=1, *, encrypted=False, user_password="teaching-password"):
    with pymupdf.open() as document:
        for _ in range(page_count):
            document.new_page()
        if encrypted:
            return document.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner-password",
                user_pw=user_password,
            )
        return document.tobytes()


def empty_pdf():
    data = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, obj in enumerate(
        (b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Count 0 /Kids [] >>"), start=1
    ):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 3\n0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def office_xml(main, extra=()):
    result = BytesIO()
    with ZipFile(result, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(main, "<main/>")
        for filename, contents in extra:
            archive.writestr(filename, contents)
    return result.getvalue()


def test_policy_public_to_active_accounts(client):
    response = client.get(f"{DOCUMENTS}/policy", headers=bearer("student"))
    assert response.status_code == 200
    assert response.json() == {
        "max_size_bytes": 20_971_520,
        "max_pages": 300,
        "allowed_formats": ["PDF", "PPTX", "DOCX", "TXT", "MARKDOWN"],
        "allowed_extensions": [".pdf", ".pptx", ".docx", ".txt", ".md", ".markdown"],
        "allowed_mime_types": [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/markdown",
        ],
    }
    assert client.get(f"{DOCUMENTS}/policy").status_code == 401


@pytest.mark.parametrize("token", ["student", "admin", "schoolless"])
def test_library_requires_lecturer(client, upload, token):
    assert client.get(DOCUMENTS, headers=bearer(token)).status_code == 403
    assert upload(token=token).status_code == 403


def test_doc_t01_owner_library(client, core, upload, db_session, storage):
    response = upload(description="A short description", title="  Hàm số  ")
    assert response.status_code == 201
    item = response.json()
    assert item["owner_user_id"] == str(core.users["primary"].id)
    assert item["owner_display_name"] == "primary"
    assert item["title"] == "Hàm số"
    assert item["processing_status"] == "PENDING"
    assert item["serving_version"] is None
    assert item["latest_version_number"] == 1
    assert item["latest_version"]["version_number"] == 1
    assert item["latest_version"]["is_serving"] is False

    detail = f"{DOCUMENTS}/{item['id']}"
    owner = bearer("primary")
    other = bearer("assisting")
    assert client.get(DOCUMENTS, headers=owner).json()["items"] == [item]
    assert client.get(DOCUMENTS, headers=other).json()["items"] == []
    assert client.get(detail, headers=other).status_code == 404
    assert client.patch(detail, json={"title": "Missing"}, headers=other).status_code == 404
    assert client.delete(detail, headers=other).status_code == 404
    updated = client.patch(detail, json={"description": None}, headers=owner)
    assert updated.status_code == 200
    assert updated.json()["description"] is None
    assert client.patch(detail, json={"title": None}, headers=owner).status_code == 422
    assert client.get(detail, headers=owner).json()["versions"] == [item["latest_version"]]

    with db_session() as session:
        version = session.get(DocumentVersion, UUID(item["latest_version"]["id"]))
        assert version.source_sha256 == sha256(b"Teaching material").hexdigest()
        assert storage.read_bytes(version.storage_key) == b"Teaching material"
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "DOCUMENT_CREATED"))
        assert audit.correlation_id == UUID(response.headers["X-Correlation-ID"])
        assert audit.metadata_json == {"version_number": 1}


def test_library_filters_and_cursor_scope(client, upload, publish):
    assert upload(title="Pending lesson").status_code == 201
    first = publish(title="Published lesson 100%")
    second = publish(title="Published lesson")
    publish(token="assisting", title="Published lesson of colleague")
    headers = bearer("primary")
    for status in ("SUCCESS", "READY"):
        params = {"status": status, "format": "TXT", "search": "lesson"}
        result = client.get(DOCUMENTS, params={**params, "limit": 1}, headers=headers).json()
        cursor = result["next_cursor"]
        next_page = client.get(
            DOCUMENTS, params={**params, "cursor": cursor}, headers=headers
        ).json()
        assert {result["items"][0]["id"], next_page["items"][0]["id"]} == {
            first["id"],
            second["id"],
        }
        assert next_page["next_cursor"] is None
        other_owner = client.get(DOCUMENTS, params={"cursor": cursor}, headers=bearer("assisting"))
        other_filter = client.get(
            DOCUMENTS, params={"cursor": cursor, "status": "PENDING"}, headers=headers
        )
        assert other_owner.status_code == other_filter.status_code == 400
    literal_search = client.get(DOCUMENTS, params={"search": "%"}, headers=headers)
    assert [item["id"] for item in literal_search.json()["items"]] == [first["id"]]
    for cursor in ("", "bad", "x" * 100):
        assert client.get(DOCUMENTS, params={"cursor": cursor}, headers=headers).status_code == 400
    for limit in (0, 101):
        assert client.get(DOCUMENTS, params={"limit": limit}, headers=headers).status_code == 422


def test_doc_t02_multiclass_delete_is_atomic(client, core, shared, db_session, storage):
    document_id = shared["id"]
    second_grant = client.post(
        f"{class_documents(core.second_class_id)}/grants",
        headers=bearer("primary"),
        json={"document_id": document_id},
    )
    assert second_grant.status_code == 201
    detail = f"{DOCUMENTS}/{document_id}"
    headers = {**bearer("primary"), "X-Correlation-ID": str(uuid4())}
    conflict = client.delete(detail, headers=headers)
    assert conflict.status_code == 409
    error = conflict.json()["error"]
    assert error["code"] == "CONFLICT"
    assert error["correlation_id"] == headers["X-Correlation-ID"]
    assert error["details"] == {
        "active_grant_count": 2,
        "affected_class_ids": sorted(str(i) for i in (core.class_id, core.second_class_id)),
    }
    assert client.get(detail, headers=headers).json()["document"]["active_grant_count"] == 2
    deleted = client.delete(detail, params={"confirm_multi_class": True}, headers=headers)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(detail, headers=headers).status_code == 404
    for class_id in (core.class_id, core.second_class_id):
        assert client.get(internal_grants(class_id), headers=INTERNAL_KEY).json()["items"] == []
    with db_session() as session:
        document = session.get(Document, UUID(document_id))
        assert document.deleted_at
        version = session.get(DocumentVersion, UUID(shared["latest_version"]["id"]))
        assert storage.exists(version.storage_key)
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == "document.deleted")
        )
        assert event.payload_json["revoked_class_ids"] == error["details"]["affected_class_ids"]
        assert event.payload_json["event_id"] == str(event.id)
        audit = session.scalar(select(AuditEvent).where(AuditEvent.action == "DOCUMENT_DELETED"))
        assert audit.metadata_json == {"active_grant_count": 2}


@pytest.mark.parametrize(
    ("filename", "content", "mime", "file_type", "pages"),
    [
        ("a.pdf", pdf, "application/pdf", "PDF", 1),
        (
            "a.pptx",
            lambda: office_xml("ppt/presentation.xml", [("ppt/slides/slide1.xml", "<slide/>")]),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "PPTX",
            1,
        ),
        (
            "a.docx",
            lambda: office_xml("word/document.xml"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "DOCX",
            None,
        ),
        ("a.txt", lambda: "Văn bản UTF-16".encode("utf-16"), "text/plain", "TXT", None),
        ("a.markdown", lambda: b"# Heading", "text/markdown", "MARKDOWN", None),
    ],
)
def test_five_formats_enter_pending(upload, filename, content, mime, file_type, pages):
    response = upload(filename, content(), mime)
    assert response.status_code == 201, response.text
    model = response.json()
    assert model["format"] == file_type
    version = model["latest_version"]
    assert version["processing_status"] == "PENDING"
    assert version["page_count"] == pages


@pytest.mark.parametrize(
    ("filename", "content", "mime", "status", "reason"),
    [
        ("a.txt", lambda: b"x" * (20_971_520 + 1), "text/plain", 413, "FILE_TOO_LARGE"),
        ("a.txt", bytes, "text/plain", 422, "EMPTY_FILE"),
        ("a.txt", lambda: b"\x00binary", "text/plain", 422, "FILE_TYPE_MISMATCH"),
        ("a.pdf", lambda: b"This is not a PDF", "application/pdf", 422, "FILE_TYPE_MISMATCH"),
        ("a.pdf", lambda: b"%PDF-1.7\ngarbage", "application/pdf", 422, "CORRUPT_FILE"),
        ("a.pdf", pdf, "text/plain", 422, "FILE_TYPE_MISMATCH"),
        ("a.png", lambda: b"hello", "application/octet-stream", 422, "UNSUPPORTED_FILE_TYPE"),
        ("a.pdf", empty_pdf, "application/pdf", 422, "EMPTY_DOCUMENT"),
        ("a.pdf", lambda: pdf(301), "application/pdf", 422, "PAGE_LIMIT_EXCEEDED"),
        ("a.pdf", lambda: pdf(encrypted=True), "application/pdf", 422, "ENCRYPTED_PDF"),
        (
            "a.pdf",
            lambda: pdf(encrypted=True, user_password=""),
            "application/pdf",
            422,
            "ENCRYPTED_PDF",
        ),
        (
            "a.docx",
            lambda: office_xml("word/document.xml", [("bomb", b"x" * 1_000_000)]),
            "application/octet-stream",
            422,
            "ZIP_LIMIT_EXCEEDED",
        ),
        (
            "a.docx",
            lambda: office_xml("word/document.xml", [(f"file{i}", b"x") for i in range(2000)]),
            "application/octet-stream",
            422,
            "ZIP_LIMIT_EXCEEDED",
        ),
        (
            "a.docx",
            lambda: office_xml("ppt/presentation.xml"),
            "application/octet-stream",
            422,
            "FILE_TYPE_MISMATCH",
        ),
        (
            "a.docx",
            lambda: office_xml("word/document.xml", [("../escape.xml", "<safe/>")]),
            "application/octet-stream",
            422,
            "CORRUPT_FILE",
        ),
        (
            "a.docx",
            lambda: office_xml("word/document.xml", [("unsafe.xml", "<!DOCTYPE main><main/>")]),
            "application/octet-stream",
            422,
            "CORRUPT_FILE",
        ),
        (
            "a.pptx",
            lambda: office_xml(
                "ppt/presentation.xml",
                [(f"ppt/slides/slide{i}.xml", "<slide/>") for i in range(1, 302)],
            ),
            "application/octet-stream",
            422,
            "PAGE_LIMIT_EXCEEDED",
        ),
    ],
)
def test_doc_t04_upload_rejected_before_storage_and_db(
    upload, filename, content, mime, status, reason, db_session, storage
):
    response = upload(filename, content(), mime)
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["details"]["reason"] == reason
    assert error["code"] == ("PAYLOAD_TOO_LARGE" if status == 413 else "VALIDATION_ERROR")
    assert not storage.root.exists()
    with db_session() as session:
        assert session.scalar(select(DocumentVersion.id).limit(1)) is None
        assert session.scalar(select(Document.id).limit(1)) is None


def test_failed_transaction_removes_staged_file(upload, monkeypatch, db_session, storage):
    def broken_audit(*args, **kwargs):
        raise RuntimeError("private document content")

    monkeypatch.setattr("app.modules.library.service.record_audit", broken_audit)
    response = upload()
    assert response.status_code == 500
    assert "private document content" not in response.text
    assert not any(path.is_file() for path in storage.root.rglob("*"))
    with db_session() as session:
        assert session.scalar(select(Document.id).limit(1)) is None
