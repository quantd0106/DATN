from uuid import UUID

from conftest import API, DOCUMENTS, bearer, class_documents

from app.model_registry import SourceBoundNote

NOTES = f"{API}/users/me/notes"
BOOKMARKS = f"{API}/users/me/bookmarks"


def note_request(document_id, **overrides):
    return {
        "document_id": document_id,
        "locator": "Trang 1",
        "page_number": 1,
        "note_text": "My own summary",
        "source_quote": "Private source excerpt",
        **overrides,
    }


def create_note(client, core, shared):
    response = client.post(
        f"{API}/classes/{core.class_id}/notes",
        headers=bearer("student"),
        json=note_request(shared["id"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_doc_t11_personal_notes_and_bookmarks_are_owner_only(client, core, shared):
    note = create_note(client, core, shared)
    path = f"{API}/notes/{note['id']}"
    assert note["user_id"] == str(core.users["student"].id)
    assert note["document_title"] == shared["title"]
    assert note["document_format"] == "TXT"
    assert note["source_quote"] == "Private source excerpt"
    assert note["version_number"] == 1
    assert note["document_version_id"] == shared["latest_version"]["id"]

    for token in ("other-student", "assisting", "admin"):
        assert client.get(NOTES, headers=bearer(token)).json()["items"] == []
        update = client.patch(path, json={"note_text": "unauthorized"}, headers=bearer(token))
        assert update.status_code == 404
        assert client.delete(path, headers=bearer(token)).status_code == 404

    root = f"{API}/classes/{core.class_id}"
    spoofed = client.post(
        f"{root}/notes",
        headers=bearer("student"),
        json=note_request(shared["id"], user_id=str(core.users["other-student"].id)),
    )
    assert spoofed.status_code == 422
    assert (
        client.post(
            f"{root}/notes",
            headers=bearer("student"),
            json=note_request(shared["id"], version_number=99),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{root}/bookmarks/toggle",
            headers=bearer("student"),
            json={"document_id": shared["id"]},
        ).status_code
        == 200
    )
    assert client.get(BOOKMARKS, headers=bearer("other-student")).json()["items"] == []
    assert len(client.get(BOOKMARKS, headers=bearer("student")).json()["items"]) == 1

    patch = client.patch(path, headers=bearer("student"), json={"note_text": "My amended summary"})
    assert patch.status_code == 200
    assert patch.json()["note_text"] == "My amended summary"
    assert client.delete(path, headers=bearer("student")).status_code == 204
    assert client.get(NOTES, headers=bearer("student")).json()["items"] == []


def test_doc_t12_revoke_redacts_quote_and_preserves_text(client, core, shared, db_session):
    note = create_note(client, core, shared)
    student = bearer("student")
    root = class_documents(core.class_id)
    document_id = shared["id"]
    reader = f"{root}/{document_id}/reader"
    assert client.get(reader, headers=student).json()["notes"] == [note]
    assert client.get(reader, headers=bearer("other-student")).json()["notes"] == []
    assert (
        client.post(
            f"{API}/classes/{core.class_id}/bookmarks/toggle",
            headers=student,
            json={"document_id": document_id, "page_number": 1},
        ).status_code
        == 200
    )

    revoke = client.delete(f"{root}/grants/{document_id}", headers=bearer("primary"))
    assert revoke.status_code == 204
    after = client.get(reader, headers=student)
    assert after.status_code == 200
    assert after.json()["is_source_accessible"] is False
    assert after.json()["revoked_reason"] == "GRANT_REVOKED"
    assert after.json()["pages"] == after.json()["outline"] == []
    hidden = {
        **note,
        "document_title": "Tài liệu",
        "source_quote": None,
        "is_source_accessible": False,
    }
    assert after.json()["notes"] == [hidden]
    assert client.get(NOTES, headers=student).json()["items"] == [hidden]
    bookmark = client.get(BOOKMARKS, headers=student).json()["items"][0]
    assert bookmark["is_source_accessible"] is False
    assert (
        client.post(
            f"{API}/classes/{core.class_id}/notes", headers=student, json=note_request(document_id)
        ).status_code
        == 404
    )
    patched = client.patch(
        f"{API}/notes/{note['id']}", headers=student, json={"note_text": "Kept after revoke"}
    )
    assert patched.status_code == 200
    assert patched.json()["note_text"] == "Kept after revoke"
    assert patched.json()["source_quote"] is None

    assert (
        client.patch(
            f"{API}/notes/{note['id']}",
            headers=student,
            json={"note_text": "keep", "source_quote": "Spoofed quote"},
        ).status_code
        == 422
    )
    assert client.delete(f"{DOCUMENTS}/{document_id}", headers=bearer("primary")).status_code == 204
    assert client.get(reader, headers=student).json()["revoked_reason"] == "DOCUMENT_DELETED"
    with db_session() as session:
        stored = session.get(SourceBoundNote, UUID(note["id"]))
        assert stored.note_text == "Kept after revoke"
        assert stored.source_quote == note["source_quote"]


def test_notes_check_current_core_access_once_per_source_pair(client, core, shared):
    create_note(client, core, shared)
    create_note(client, core, shared)
    student_id = core.users["student"].id
    core.members.remove((core.class_id, student_id))
    core.access_checks.clear()
    headers = bearer("student")
    result = client.get(NOTES, headers=headers, params={"class_id": str(core.class_id)})
    assert result.status_code == 200
    assert len(result.json()["items"]) == 2
    assert core.access_checks == [(core.class_id, student_id, "read_document_source")]
    for note in result.json()["items"]:
        assert not note["is_source_accessible"] and note["source_quote"] is None
    root = class_documents(core.class_id)
    limited = client.get(f"{root}/{shared['id']}/reader", headers=headers)
    assert limited.json()["revoked_reason"] == "CLASS_ACCESS_LOST"
    assert all(note["source_quote"] is None for note in limited.json()["notes"])


def test_owned_bookmark_allows_redacted_reader_without_core_history(client, core, shared):
    class_root = f"{API}/classes/{core.class_id}"
    student = bearer("new-student")
    document_id = shared["id"]
    bookmark = client.post(
        f"{class_root}/bookmarks/toggle", headers=student, json={"document_id": document_id}
    )
    assert bookmark.status_code == 200
    revoke = client.delete(
        f"{class_root}/documents/grants/{document_id}", headers=bearer("assisting")
    )
    assert revoke.status_code == 204
    core.access_checks.clear()
    response = client.get(f"{class_root}/documents/{document_id}/reader", headers=student)
    assert response.status_code == 200
    assert response.json()["is_source_accessible"] is False
    assert response.json()["notes"] == []
    assert all(action == "read_document_source" for _, _, action in core.access_checks)


def test_doc_t13_archived_class_personal_note_and_bookmark_mutations(client, core, shared):
    core.class_status[core.class_id] = "ARCHIVED"
    note = create_note(client, core, shared)
    student = bearer("student")
    root = f"{API}/classes/{core.class_id}"
    patch = client.patch(
        f"{API}/notes/{note['id']}", headers=student, json={"note_text": "Edited in archive"}
    )
    assert patch.status_code == 200
    assert patch.json()["source_quote"] == note["source_quote"]
    assert patch.json()["is_source_accessible"] is True

    payload = {"document_id": shared["id"], "page_number": 1}
    toggle = f"{root}/bookmarks/toggle"
    added = client.post(toggle, json=payload, headers=student)
    assert added.status_code == 200
    assert added.json()["bookmarked"] is True
    assert added.json()["bookmark"]["is_source_accessible"] is True
    assert added.json()["bookmark"]["locator"] == "page:1"
    assert client.get(BOOKMARKS, headers=student, params={"class_id": str(core.class_id)}).json()[
        "items"
    ] == [added.json()["bookmark"]]
    removed = client.post(toggle, json=payload, headers=student)
    assert removed.status_code == 200
    assert removed.json() == {"bookmarked": False, "bookmark": None}
    assert client.get(BOOKMARKS, headers=student).json()["items"] == []
    assert (
        client.post(
            f"{root}/documents/grants",
            headers=bearer("assisting"),
            json={"document_id": shared["id"]},
        ).status_code
        == 409
    )
