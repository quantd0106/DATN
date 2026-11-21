from uuid import UUID, uuid4

import pytest
from conftest import DOCUMENTS, INTERNAL_KEY, bearer, class_documents, internal_grants
from sqlalchemy import select

from app.model_registry import AuditEvent, ClassDocumentGrant, OutboxEvent


@pytest.mark.parametrize(("owner", "revoker"), [("primary", "assisting"), ("assisting", "primary")])
def test_doc_t06_assigned_lecturers_grant_own_source_and_revoke_others(
    client, core, publish, db_session, owner, revoker
):
    document = publish(token=owner)
    document_id = document["id"]
    root = class_documents(core.class_id)
    payload = {"document_id": document_id}
    assert client.post(f"{root}/grants", headers=bearer(revoker), json=payload).status_code == 404
    assert (
        client.patch(
            f"{DOCUMENTS}/{document_id}", headers=bearer(revoker), json={"title": "wrong owner"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{DOCUMENTS}/{document_id}/versions",
            headers=bearer(revoker),
            files={"file": ("v2.txt", b"New version", "text/plain")},
        ).status_code
        == 404
    )
    assert client.post(f"{root}/grants", headers=bearer("student"), json=payload).status_code == 403

    created = client.post(f"{root}/grants", headers=bearer(owner), json=payload)
    assert created.status_code == 201
    assert created.json()["granted_by_user_id"] == str(core.users[owner].id)
    assert created.json()["serving_version_number"] == 1
    same = client.post(f"{root}/grants", headers=bearer(owner), json=payload)
    assert same.status_code == 200
    assert same.json() == created.json()
    listed = client.get(root, headers=bearer("student"), params={"format": "TXT", "search": "Bai"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [document_id]
    pdf_documents = client.get(root, headers=bearer("student"), params={"format": "PDF"})
    assert pdf_documents.json()["items"] == []

    path = f"{root}/grants/{document_id}"
    assert client.delete(path, headers=bearer(revoker)).status_code == 204
    assert client.delete(path, headers=bearer(revoker)).status_code == 204
    with db_session() as session:
        grant = session.get(ClassDocumentGrant, UUID(created.json()["id"]))
        assert grant.is_active is False
        assert grant.revoked_by_user_id == core.users[revoker].id
        assert grant.revoked_at is not None
        events = list(
            session.scalars(
                select(OutboxEvent).where(OutboxEvent.event_type == "document.grant.changed")
            )
        )
        assert sorted(event.payload_json["is_active"] for event in events) == [False, True]
        assert all(event.aggregate_id == core.class_id for event in events)
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "DOCUMENT_GRANT_REVOKED")
        )
        assert audit.target_id == UUID(document_id)
    reactivated = client.post(f"{root}/grants", headers=bearer(owner), json=payload)
    assert reactivated.status_code == 201
    assert reactivated.json()["id"] == created.json()["id"]
    assert reactivated.json()["revoked_at"] is None


def test_grant_requires_success(client, core, upload):
    document = upload().json()
    response = client.post(
        f"{class_documents(core.class_id)}/grants",
        headers=bearer("primary"),
        json={"document_id": document["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_doc_t07_live_permissions_and_archived_grants(client, core, shared):
    root = class_documents(core.class_id)
    document_id = shared["id"]
    path = f"{root}/grants/{document_id}"
    payload = {"document_id": document_id}

    core.class_status[core.class_id] = "ARCHIVED"
    for token in ("primary", "assisting"):
        headers = bearer(token)
        assert client.post(f"{root}/grants", headers=headers, json=payload).status_code == 409
        assert client.delete(path, headers=headers).status_code == 409
    assert client.get(f"{root}/{document_id}/reader", headers=bearer("student")).status_code == 200
    assert client.get(root, headers=bearer("student")).status_code == 200
    core.members.remove((core.class_id, core.users["new-student"].id))
    denied = client.get(root, headers=bearer("new-student"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    core.class_status[core.class_id] = "ACTIVE"
    del core.assignments[core.class_id, core.users["primary"].id]
    headers = bearer("primary")
    assert client.post(f"{root}/grants", headers=headers, json=payload).status_code == 403
    assert client.delete(path, headers=headers).status_code == 403
    assert client.get(root, headers=headers).status_code == 403
    assert client.get(f"{DOCUMENTS}/{document_id}", headers=headers).status_code == 200


def test_doc_t08_revoke_blocks_reader_download_and_internal_immediately(client, core, shared):
    root = class_documents(core.class_id)
    document_id = shared["id"]
    student = bearer("student")
    reader = f"{root}/{document_id}/reader"
    download = f"{root}/{document_id}/download"
    initial = client.get(reader, headers=student)
    assert initial.status_code == 200
    assert initial.headers["Cache-Control"] == "no-store"
    assert initial.json()["document"]["current_version"] == 1
    assert initial.json()["pages"] == [
        {
            "page_number": 1,
            "title": "Trang 1",
            "paragraphs": ["Lesson", "Canonical lesson"],
            "formulas": ["x^2"],
        }
    ]
    assert initial.json()["outline"] == [{"id": "heading", "label": "Lesson", "page_number": 1}]
    source = client.get(download, headers=student)
    assert source.status_code == 200
    assert source.content == b"Teaching material"
    assert source.headers["content-type"].startswith("text/plain")
    assert source.headers["content-disposition"] == (
        "attachment; filename=\"document.txt\"; filename*=UTF-8''lesson.txt"
    )
    assert source.headers["cache-control"] == "no-store"
    revoke = client.delete(f"{root}/grants/{document_id}", headers=bearer("assisting"))
    assert revoke.status_code == 204

    assert client.get(root, headers=student).json()["items"] == []
    assert client.get(download, headers=student).status_code == 404
    assert client.get(internal_grants(core.class_id), headers=INTERNAL_KEY).json()["items"] == []
    historical = client.get(reader, headers=student)
    assert historical.status_code == 200
    assert historical.json()["revoked_reason"] == "GRANT_REVOKED"
    assert historical.json()["document"]["id"] == document_id
    assert historical.json()["document"]["title"] == "Tài liệu"
    assert historical.json()["document"]["description"] is None
    assert historical.json()["pages"] == historical.json()["outline"] == []
    assert client.get(reader, headers=bearer("new-student")).status_code == 404
    assert client.get(f"{root}/{uuid4()}/reader", headers=student).status_code == 404


def test_doc_t09_cross_school_is_hidden(client, core, shared, publish):
    root = class_documents(core.class_id)
    doc_id = shared["id"]
    for path in (root, f"{root}/{doc_id}/reader", f"{root}/{doc_id}/download"):
        response = client.get(path, headers=bearer("outsider"))
        assert response.status_code == 404
        assert doc_id not in response.text

    foreign = publish(token="other-school-teacher")
    assert client.get(f"{DOCUMENTS}/{foreign['id']}", headers=bearer("primary")).status_code == 404
    foreign_reader = client.get(f"{root}/{foreign['id']}/reader", headers=bearer("student"))
    assert foreign_reader.status_code == 404
    assert (
        client.post(
            f"{root}/grants", headers=bearer("primary"), json={"document_id": foreign["id"]}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{root}/grants/{foreign['id']}", headers=bearer("primary")).status_code
        == 404
    )


def test_doc_t10_core_outage_and_revoked_token_fail_closed(client, core, shared):
    root = class_documents(core.class_id)
    doc_id = shared["id"]
    student = bearer("student")
    assert client.get(root, headers=student).status_code == 200
    core.access_outage = True
    for path in (root, f"{root}/{doc_id}/reader", f"{root}/{doc_id}/download"):
        response = client.get(path, headers=student)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert response.headers["cache-control"] == "no-store"
    assert client.delete(f"{root}/grants/{doc_id}", headers=bearer("primary")).status_code == 503
    core.access_outage = False
    core.outage = True
    assert client.get(f"{DOCUMENTS}/policy", headers=student).status_code == 503
    core.outage = False
    core.revoked.add("student")
    assert client.get(root, headers=student).status_code == 401
    assert client.get(f"{DOCUMENTS}/policy", headers=student).status_code == 401
    assert core.introspections.count("student") == 7


def test_h3_pending_review_and_failed_versions_keep_prior_success(
    client, core, shared, set_version_state
):
    document_path = f"{DOCUMENTS}/{shared['id']}"
    root = class_documents(core.class_id)
    reader = f"{root}/{shared['id']}/reader"
    owner = bearer("primary")
    student = bearer("student")
    upload = client.post(
        f"{document_path}/versions",
        headers=owner,
        files={"file": ("bài giảng.md", b"New version", "text/markdown")},
    )
    assert upload.status_code == 201, upload.text
    second = upload.json()
    assert second["version_number"] == 2
    assert second["processing_status"] == "PENDING"
    assert second["is_serving"] is second["is_superseded"] is False
    for state in ("PENDING", "PROCESSING", "NEEDS_REVIEW", "FAILED"):
        set_version_state(second["id"], state)
        detail = client.get(document_path, headers=owner).json()
        assert detail["document"]["latest_version"]["processing_status"] == state
        assert detail["document"]["serving_version"]["version_number"] == 1
        assert detail["versions"][1]["is_serving"] is True
        current = client.get(reader, headers=student)
        assert current.json()["serving_version"] == 1
        assert current.json()["viewing_version"] == 1
        assert client.get(reader, params={"version": 2}, headers=student).status_code == 404
        assert client.get(root, params={"format": "TXT"}, headers=student).json()["items"]
        assert detail["versions"][1]["review_summary"] is None

    set_version_state(second["id"], text="Published update")
    detail = client.get(document_path, headers=owner).json()
    new, old = detail["versions"]
    assert new["is_serving"] and not new["is_superseded"]
    assert old["is_superseded"] and not old["is_serving"]
    assert old["processing_status"] == "SUCCESS"
    assert client.get(reader, headers=student).json()["serving_version"] == 2
    historical = client.get(reader, params={"version": 1}, headers=student).json()
    assert historical["viewing_version"] == 1
    assert historical["pages"][0]["paragraphs"][-1] == "Canonical lesson"
    download = client.get(f"{root}/{shared['id']}/download", headers=student)
    assert download.content == b"New version"
    assert "b%C3%A0i%20gi%E1%BA%A3ng.md" in download.headers["content-disposition"]
