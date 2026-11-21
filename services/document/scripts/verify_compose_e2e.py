"""Verify the Document journey through Web on a disposable, seeded Compose stack."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from math import ceil
from pathlib import Path
from random import Random
from secrets import token_hex, token_urlsafe
from statistics import median
from time import monotonic, perf_counter, sleep
from uuid import UUID
from zipfile import ZipFile

import httpx
import pymupdf
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[3]
MAX_BYTES = 20_971_520
MAX_PAGES = 300
LESSON = "The water cycle moves water through evaporation, condensation, and precipitation."
REVISED_LESSON = "Groundwater returns slowly to the rivers through underground springs."
CANONICAL_SCHEMA = "idp-canonical-1"
DENIED = (403, 404)
IN_PROGRESS = {"PENDING", "PROCESSING"}
SEED_LECTURER = "primary@seed.example.com"
SEED_ASSISTING = "assisting@seed.example.com"
SEED_RECOVERY = """
import json
import sys

from sqlalchemy import text
import app.model_registry
from app.common.db import SessionLocal
from app.modules.identity.schemas import ForgotPasswordRequest
from app.modules.identity.service import IdentityService

database, *emails = sys.argv[1:]
allowed = {"primary@seed.example.com", "assisting@seed.example.com"}
with SessionLocal() as session:
    actual = session.scalar(text("SELECT current_database()"))
    if actual != database or not actual.startswith(("dev_", "test_")):
        raise SystemExit("Seed recovery requires the named disposable dev_/test_ database")
    session.rollback()
    service = IdentityService(session)
    tokens = {}
    for email in emails:
        if email not in allowed:
            raise SystemExit("Seed recovery supports only the synthetic seed lecturers")
        delivery = service.forgot_password(ForgotPasswordRequest(email=email))
        if delivery is None:
            raise SystemExit("Synthetic lecturer missing. Run Core seed-dev first.")
        tokens[email] = delivery.token.get_secret_value()
    print(json.dumps(tokens))
"""


class CheckFailure(Exception):
    """A verification check did not match the expected behavior."""


class CheckSkipped(Exception):
    """The operator opted out of a check or a prerequisite failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def pdf_bytes(
    text: str | None = LESSON,
    *,
    pages: int = 1,
    lines: int = 1,
    attachment_bytes: int = 0,
    encrypted: bool = False,
) -> bytes:
    with pymupdf.open() as pdf:
        for number in range(1, pages + 1):
            page = pdf.new_page()
            if text is not None:
                page.insert_text((48, 60), text, fontsize=10)
                for line in range(1, lines):
                    page.insert_text(
                        (48, 60 + 20 * line),
                        f"{text} Page {number}, item {line}.",
                        fontsize=10,
                    )
        if attachment_bytes:
            # Keep the visible text layer simple while exercising real binary transfer and storage.
            pdf.embfile_add("sample.bin", Random(42).randbytes(attachment_bytes))
        if encrypted:
            return pdf.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="fixture-owner",
                user_pw="fixture-reader",
            )
        return pdf.tobytes(deflate=True)


def docx_bytes(*, needs_review: bool = False) -> bytes:
    document = Document()
    document.add_paragraph(LESSON)
    if needs_review:
        # Pandoc preserves the OMML as LaTeX 'x('. The unmatched delimiter triggers
        # LATEX_INVALID, which blocks auto-publication but permits assisted review.
        equation = f"<m:oMath {nsdecls('m')}><m:r><m:t>x(</m:t></m:r></m:oMath>"
        document.add_paragraph()._p.append(parse_xml(equation))
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pptx_bytes() -> bytes:
    slide_deck = Presentation()
    slide = slide_deck.slides.add_slide(slide_deck.slide_layouts[1])
    slide.shapes.title.text = "The water cycle"
    slide.placeholders[1].text = LESSON
    buffer = BytesIO()
    slide_deck.save(buffer)
    return buffer.getvalue()


def spoofed_docx_bytes() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", "<presentation/>")
    return buffer.getvalue()


def successful_samples() -> dict[str, tuple[str, bytes]]:
    large_pdf = pdf_bytes(attachment_bytes=10_500_000)
    require(10 * 1024 * 1024 < len(large_pdf) <= MAX_BYTES, "PDF must exceed 10 MiB")
    return {
        "PDF": ("lesson.pdf", large_pdf),
        "PPTX": ("lesson.pptx", pptx_bytes()),
        "DOCX": ("lesson.docx", docx_bytes()),
        "TXT": ("lesson.txt", (LESSON + "\n").encode()),
        "MARKDOWN": ("lesson.md", ("# The water cycle\n\n" + LESSON + "\n").encode()),
    }


def write_demo_fixtures(directory: Path) -> None:
    fixtures = [
        *successful_samples().values(),
        ("review.docx", docx_bytes(needs_review=True)),
        ("blank.pdf", pdf_bytes(None)),
        ("revision.pdf", pdf_bytes(REVISED_LESSON)),
    ]
    directory.mkdir(parents=True, exist_ok=True)
    for filename, content in fixtures:
        with (directory / filename).open("xb") as output:
            output.write(content)
        print(f"{directory / filename} ({len(content):,} bytes)", flush=True)


def check_status(response: httpx.Response, expected: int | tuple[int, ...]) -> httpx.Response:
    statuses = (expected,) if isinstance(expected, int) else expected
    if response.status_code not in statuses:
        # Response bodies can contain activation codes, passwords, or raw documents.
        correlation = response.headers.get("x-correlation-id", "unavailable")
        raise CheckFailure(
            f"{response.request.method} {response.request.url.path}: expected {statuses}, "
            f"got HTTP {response.status_code} (request ID {correlation})"
        )
    return response


class WebSession:
    """Keep an independent Web cookie jar and bearer token for each actor."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.refresh_at = float("inf")

    def _set_tokens(self, response: httpx.Response) -> None:
        data = response.json()
        require(data["token_type"] == "bearer" and bool(data["access_token"]), "No bearer token")
        require(self.client.cookies.get("refresh_token") is not None, "Missing refresh cookie")
        self.client.headers["Authorization"] = "Bearer " + data["access_token"]
        self.refresh_at = monotonic() + max(1, data["expires_in"] - 30)

    def login(self, identifier: dict[str, str], password: str) -> None:
        self._set_tokens(
            self.request("POST", "/auth/login", json={**identifier, "password": password})
        )

    def request(self, method: str, path: str, expected: int | tuple[int, ...] = 200, **kwargs):
        if monotonic() >= self.refresh_at:
            self._set_tokens(check_status(self.client.post("/api/v1/auth/refresh"), 200))
        return check_status(self.client.request(method, "/api/v1" + path, **kwargs), expected)


class Compose:
    def __init__(self, args: argparse.Namespace) -> None:
        self.command = ["docker", "compose"]
        if args.compose_project:
            self.command.extend(["-p", args.compose_project])
        self.args = args

    def run(
        self, *arguments: str, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.command, *arguments],
            check=False,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if check and result.returncode:
            raise CheckFailure(f"Docker Compose {arguments[0]} exited with {result.returncode}")
        return result

    def available(self) -> bool:
        if not shutil.which("docker"):
            return False
        database = self.run("ps", "--quiet", "db", check=False)
        return database.returncode == 0 and bool(database.stdout.strip())

    def psql_command(self) -> list[str]:
        return [
            "exec",
            "-T",
            "-e",
            "PGPASSWORD",
            "db",
            "psql",
            "-XqAt",
            "-h",
            "127.0.0.1",
            "-U",
            self.args.document_db_user,
            "-d",
            self.args.db_name,
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            "VERBOSITY=verbose",
        ]

    def psql_env(self) -> dict[str, str]:
        return {**os.environ, "PGPASSWORD": self.args.document_db_password}

    def psql(self, query: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.run(*self.psql_command(), "-c", query, check=check, env=self.psql_env())

    @contextmanager
    def restore_service(self, service: str) -> Iterator[None]:
        try:
            yield
        finally:
            self.run("start", service)

    @contextmanager
    def hold_version_publish_lock(self, version_id: str) -> Iterator[None]:
        # Match the worker's transaction lock for this version. Claiming is independent of
        # publication, so even a fast worker can be killed after claim and before commit.
        key = int.from_bytes(UUID(version_id).bytes[:8], "big", signed=True)
        with subprocess.Popen(
            [*self.command, *self.psql_command(), "-f", "-"],
            cwd=ROOT,
            env=self.psql_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as session:
            try:
                session.stdin.write(
                    f"SET idle_session_timeout = '120s';\nSELECT pg_advisory_lock({key});\n"
                )
                session.stdin.flush()
                deadline = monotonic() + 15
                while True:
                    require(session.poll() is None, "PostgreSQL lock session exited")
                    if self.psql(f"SELECT pg_try_advisory_lock({key});").stdout.strip() == "f":
                        break
                    require(monotonic() < deadline, "Could not hold the version publication lock")
                    sleep(0.1)
                yield
            finally:
                session.stdin.close()
                try:
                    session.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    session.kill()
                    session.wait()
                    raise CheckFailure("PostgreSQL lock session did not close promptly") from None

    def version_attempt(self, version_id: str) -> tuple[int, datetime]:
        output = self.psql(
            "SELECT attempt_count, available_at FROM document.document_versions "
            f"WHERE id = '{UUID(version_id)}';"
        ).stdout.strip()
        attempts, lease_until = output.split("|")
        return int(attempts), datetime.fromisoformat(lease_until)


def wait_for(description: str, probe: Callable[[], bool], timeout: float) -> None:
    deadline = monotonic() + timeout
    while True:
        if probe():
            return
        require(monotonic() < deadline, f"Timed out waiting for {description}")
        sleep(0.5)


def health_ready(client: httpx.Client, url: str) -> None:
    def ready() -> bool:
        try:
            response = client.get(url + "/health/ready")
            return response.status_code == 200 and response.json()["status"] == "ready"
        except httpx.TransportError:
            return False

    wait_for(f"{url}/health/ready", ready, 60)


def document_path(document_id: str) -> str:
    return f"/documents/{document_id}"


def class_documents(class_id: str) -> str:
    return f"/classes/{class_id}/documents"


def class_source(class_id: str, document_id: str) -> str:
    return class_documents(class_id) + f"/{document_id}"


def verify_redacted(reader: dict, reason: str) -> None:
    require(reader["is_source_accessible"] is False, "Reader still exposes source access")
    require(reader["outline"] == [] and reader["pages"] == [], "Reader still exposes source")
    require(reader["revoked_reason"] == reason, f"Expected reader reason {reason}")
    require(
        reader["viewing_version"] is None and reader["serving_version"] is None,
        "Source version leaked",
    )
    require(
        all(note["source_quote"] is None for note in reader["notes"]), "Reader leaked a note quote"
    )


class Verification:
    def __init__(self, args: argparse.Namespace, stack: ExitStack) -> None:
        self.args = args
        self.stack = stack
        self.compose = Compose(args)
        self.run_id = token_hex(5)
        self.direct = stack.enter_context(httpx.Client(trust_env=False, timeout=5))
        self.anonymous = self.web_session()
        self.admin = self.web_session()
        self.owner = self.web_session()
        self.assisting = self.web_session()
        self.student = self.web_session()
        self.peer = self.web_session()
        self.outsider = self.web_session()

    def web_session(self) -> WebSession:
        return WebSession(
            self.stack.enter_context(
                httpx.Client(
                    base_url=self.args.web_url,
                    headers={"Origin": self.args.origin},
                    trust_env=False,
                    timeout=65,
                )
            )
        )

    def staff_logins(self, *, reset_seed_passwords: bool = False) -> tuple[str, str]:
        staff = (
            (self.owner, self.args.lecturer_email, self.args.lecturer_password),
            (self.assisting, self.args.assisting_email, self.args.assisting_password),
        )
        reset_emails = [
            email for _, email, password in staff if reset_seed_passwords or not password
        ]
        recovery: dict[str, str] = {}
        if reset_emails:
            require(self.args.db_name.startswith(("dev_", "test_")), "Use a dev_/test_ seed DB")
            require(
                self.compose.available(), "Provide lecturer credentials when Docker is unavailable"
            )
            result = self.compose.run(
                "exec",
                "-T",
                "core",
                "python",
                "-c",
                SEED_RECOVERY,
                self.args.db_name,
                *reset_emails,
            )
            # The subprocess output contains one-time tokens. Keep it in memory.
            recovery = json.loads(result.stdout)
        staff_ids = []
        for session, email, password in staff:
            password = password or token_urlsafe(32)
            if email in recovery:
                session.request(
                    "POST",
                    "/auth/password/reset",
                    204,
                    json={"token": recovery[email], "new_password": password},
                )
            session.login({"email": email}, password)
            profile = session.request("GET", "/users/me").json()
            require("LECTURER" in profile["roles"], "Expected lecturer role")
            staff_ids.append(profile["id"])
        return staff_ids[0], staff_ids[1]

    def setup(self) -> None:
        health_ready(self.direct, self.args.core_url)
        health_ready(self.direct, self.args.document_url)
        self.admin.login({"email": self.args.admin_email}, self.args.admin_password)
        self.owner_id, self.assisting_id = self.staff_logins()
        require(self.owner_id != self.assisting_id, "Lecturers must be different users")

        self.admin.client.headers["X-Admin-Reason"] = "ADMIN_INTERVENTION"
        student = self.create_student(self.student, "student")
        peer = self.create_student(self.peer, "peer")
        self.create_student(self.outsider, "outsider")
        course = self.admin.request(
            "POST",
            "/admin/courses",
            201,
            json={"code": f"E2E-{self.run_id}", "name": f"E2E documents {self.run_id}"},
        ).json()
        self.course_id = course["id"]
        self.class_a = self.create_class(self.course_id, "A", [self.assisting_id], [student, peer])
        self.class_b = self.create_class(self.course_id, "B", [], [student])
        self.archive_class_id = self.create_class(self.course_id, "Archive", [], [student])
        print(f"  Run {self.run_id}  class A={self.class_a}  class B={self.class_b}", flush=True)

    def create_student(self, session: WebSession, label: str) -> str:
        username = f"e2e.{self.run_id}.{label}"
        password = token_urlsafe(32)
        provisioned = self.admin.request(
            "POST", "/admin/students", 201, json={"username": username}
        ).json()
        require(provisioned["purpose"] == "ACTIVATION", "Expected a student activation code")
        session.request(
            "POST",
            "/auth/activate",
            204,
            json={
                "username": username,
                "activation_code": provisioned["code"],
                "new_password": password,
            },
        )
        session.login({"username": username}, password)
        return provisioned["student"]["id"]

    def create_class(
        self, course_id: str, label: str, assisting_ids: list[str], student_ids: list[str]
    ) -> str:
        created = self.admin.request(
            "POST",
            "/admin/classes",
            201,
            json={
                "course_id": course_id,
                "name": f"E2E {self.run_id} {label}",
                "academic_year": "2026-2027",
                "semester": "1",
                "grade_level": 9,
                "enrollment_mode": "MANUAL",
                "primary_lecturer_id": self.owner_id,
                "assisting_lecturer_ids": assisting_ids,
            },
        ).json()
        require(created["status"] == "ACTIVE", "Created class must be ACTIVE")
        class_id = created["id"]
        for student_id in student_ids:
            self.admin.request(
                "POST", f"/admin/classes/{class_id}/members", 201, json={"student_id": student_id}
            )
        return class_id

    def archive(self, class_id: str) -> None:
        result = self.admin.request("POST", f"/admin/classes/{class_id}/archive").json()
        require(result["status"] == "ARCHIVED", "Expected ARCHIVED class")

    def upload(
        self,
        label: str,
        filename: str,
        content: bytes,
        *,
        actor: WebSession | None = None,
        expected: int = 201,
    ) -> httpx.Response:
        return (actor or self.owner).request(
            "POST",
            "/documents",
            expected,
            data={"title": f"E2E {self.run_id} {label}"},
            files={"file": (filename, content)},
        )

    def upload_version(
        self,
        document_id: str,
        filename: str,
        content: bytes,
        *,
        actor: WebSession | None = None,
        expected: int | tuple[int, ...] = 201,
    ) -> httpx.Response:
        return (actor or self.owner).request(
            "POST",
            document_path(document_id) + "/versions",
            expected,
            files={"file": (filename, content)},
        )

    def detail(self, document_id: str, actor: WebSession | None = None) -> dict:
        return (actor or self.owner).request("GET", document_path(document_id)).json()

    def wait_version(
        self,
        document_id: str,
        version_id: str,
        expected: str,
        *,
        actor: WebSession | None = None,
        timeout: float | None = None,
        interval: float = 0.5,
    ) -> dict:
        deadline = monotonic() + (self.args.wait_timeout if timeout is None else timeout)
        previous = None
        while True:
            versions = self.detail(document_id, actor)["versions"]
            version = next((item for item in versions if item["id"] == version_id), None)
            require(version is not None, f"Version {version_id} missing from detail")
            status = version["processing_status"]
            if status != previous:
                print(
                    f"  {datetime.now(UTC).isoformat(timespec='milliseconds')} "
                    f"{version_id} {status}"
                    + (f" ({version['error_code']})" if version["error_code"] else ""),
                    flush=True,
                )
                previous = status
            if status == expected:
                return version
            require(status in IN_PROGRESS, f"{version_id}: expected {expected}, reached {status}")
            require(
                monotonic() < deadline, f"{version_id}: timed out at {status}, expected {expected}"
            )
            sleep(interval)

    def ready_upload(
        self,
        label: str,
        filename: str = "lesson.txt",
        content: bytes = LESSON.encode(),
        *,
        actor: WebSession | None = None,
    ) -> tuple[str, dict]:
        document = self.upload(label, filename, content, actor=actor).json()
        latest = document["latest_version"]
        require(latest["processing_status"] == "PENDING", "Upload must return raw PENDING")
        version = self.wait_version(document["id"], latest["id"], "SUCCESS", actor=actor)
        require(version["is_serving"] is True, "Successful first version must serve")
        return document["id"], version

    def grant(
        self,
        class_id: str,
        document_id: str,
        *,
        actor: WebSession | None = None,
        expected: int | tuple[int, ...] = 201,
    ) -> httpx.Response:
        return (actor or self.owner).request(
            "POST",
            class_documents(class_id) + "/grants",
            expected,
            json={"document_id": document_id},
        )

    def revoke(self, class_id: str, document_id: str, *, actor: WebSession | None = None) -> None:
        (actor or self.owner).request(
            "DELETE", class_documents(class_id) + f"/grants/{document_id}", 204
        )

    def read(self, class_id: str, document_id: str, number: int, *, text: str = LESSON) -> dict:
        reader = self.student.request("GET", class_source(class_id, document_id) + "/reader").json()
        require(reader["document"]["id"] == document_id, "Wrong reader document")
        require(reader["is_source_accessible"] is True, "Student source access denied")
        require(
            reader["serving_version"] == reader["viewing_version"] == number, "Wrong reader version"
        )
        require(
            any(text in paragraph for page in reader["pages"] for paragraph in page["paragraphs"]),
            "Reader Canonical text does not match the uploaded lesson",
        )
        return reader

    def check_original(self, class_id: str, document_id: str, content: bytes) -> None:
        response = self.student.request("GET", class_source(class_id, document_id) + "/download")
        require(response.content == content, "Download bytes differ from the original upload")

    def add_note(self, class_id: str, document_id: str, text: str) -> dict:
        note = self.student.request(
            "POST",
            f"/classes/{class_id}/notes",
            201,
            json={
                "document_id": document_id,
                "version_number": 1,
                "locator": "page:1",
                "page_number": 1,
                "note_text": text,
                "source_quote": LESSON,
            },
        ).json()
        require(note["is_source_accessible"] is True, "Note must have a live source on creation")
        require(
            note["note_text"] == text and note["source_quote"] == LESSON, "Note content mismatch"
        )
        return note

    def bookmark(self, class_id: str, document_id: str, enabled: bool) -> None:
        bookmark = self.student.request(
            "POST",
            f"/classes/{class_id}/bookmarks/toggle",
            json={"document_id": document_id, "page_number": 1},
        ).json()
        require(bookmark["bookmarked"] is enabled, "Bookmark did not toggle")
        if enabled:
            require(bookmark["bookmark"]["document_id"] == document_id, "Wrong bookmark document")

    def policy(self) -> None:
        self.anonymous.request("GET", "/documents/policy", 401)
        policy = self.owner.request("GET", "/documents/policy").json()
        require(policy["max_size_bytes"] == MAX_BYTES, "Wrong max_size_bytes")
        require(policy["max_pages"] == MAX_PAGES, "Wrong max_pages")
        require(
            {"PDF", "PPTX", "DOCX", "TXT", "MARKDOWN"} <= set(policy["allowed_formats"]),
            "Policy missing one or more required formats",
        )

    def ingress(self) -> None:
        cases = [
            ("empty.txt", b"", 422, "EMPTY_FILE"),
            ("large.txt", b"x" * (MAX_BYTES + 1), 413, "FILE_TOO_LARGE"),
            ("fake.pdf", b"NOT A PDF", 422, "FILE_TYPE_MISMATCH"),
            ("spoofed.docx", spoofed_docx_bytes(), 422, "FILE_TYPE_MISMATCH"),
            ("encrypted.pdf", pdf_bytes(encrypted=True), 422, "ENCRYPTED_PDF"),
            (
                "too-many-pages.pdf",
                pdf_bytes(None, pages=MAX_PAGES + 1),
                422,
                "PAGE_LIMIT_EXCEEDED",
            ),
        ]
        for name, content, status, reason in cases:
            with subcheck(f"{name} ({len(content):,} bytes) -> {status}"):
                response = self.upload("invalid", name, content, expected=status)
                require(
                    response.json()["error"]["details"]["reason"] == reason, f"Expected {reason}"
                )

    def formats(self) -> None:
        for kind, (filename, content) in successful_samples().items():
            with subcheck(f"{kind} ({len(content):,} bytes) upload, serving, reader and download"):
                document_id, version = self.ready_upload(kind, filename, content)
                require(version["file_type"] == kind, f"Unexpected detected format for {filename}")
                grant = self.grant(self.class_a, document_id).json()
                require(
                    grant["is_active"] is True and grant["serving_version_number"] == 1, "Bad grant"
                )
                items = self.student.request(
                    "GET", class_documents(self.class_a), params={"limit": 100}
                ).json()["items"]
                entry = next((item for item in items if item["id"] == document_id), None)
                require(
                    entry is not None and entry["serving_version"] == 1,
                    f"{kind} missing from class",
                )
                self.read(self.class_a, document_id, 1)
                self.check_original(self.class_a, document_id, content)

    def needs_review(self) -> None:
        content = docx_bytes(needs_review=True)
        document = self.upload("review", "review.docx", content).json()
        document_id = document["id"]
        path = document_path(document_id)
        version_id = document["latest_version"]["id"]
        version = self.wait_version(document_id, version_id, "NEEDS_REVIEW")
        summary = version["review_summary"]
        require(
            summary and "LATEX_INVALID" in summary["flag_codes"], "Missing LATEX_INVALID review"
        )
        require(version["is_serving"] is False, "Unaccepted version marked serving")
        require(
            self.detail(document_id)["document"]["serving_version"] is None, "Premature publication"
        )
        self.grant(self.class_a, document_id, expected=409)
        self.student.request("GET", class_source(self.class_a, document_id) + "/reader", 404)
        print("  LATEX_INVALID in review summary. No serving version before accept.", flush=True)

        with subcheck("assisting lecturer cannot see or modify the private source"):
            private = self.assisting.request(
                "GET", "/documents", params={"search": document["title"]}
            )
            require(
                not private.json()["items"], "Private document leaked in another lecturer's library"
            )
            self.assisting.request("GET", path, DENIED)
            self.upload_version(
                document_id, "not-owner.docx", content, actor=self.assisting, expected=DENIED
            )
            self.assisting.request("POST", f"{path}/versions/{version_id}/accept", DENIED)
            self.assisting.request("DELETE", path, DENIED)

        accepted = self.owner.request("POST", f"{path}/versions/{version_id}/accept").json()
        require(accepted["processing_status"] == "SUCCESS", "Owner accept did not publish")
        require(accepted["acceptance_mode"] == "OWNER_ACCEPTED", "Wrong acceptance mode")
        print("  Owner accept -> SUCCESS (OWNER_ACCEPTED)", flush=True)
        self.grant(self.class_a, document_id)
        self.read(self.class_a, document_id, 1)
        self.check_original(self.class_a, document_id, content)

    def fallback(self) -> None:
        first = pdf_bytes()
        document_id, v1 = self.ready_upload("H3", "lesson.pdf", first)
        self.grant(self.class_a, document_id)
        self.read(self.class_a, document_id, 1)
        # Valid one-page PDF at ingress, with no text or OCR content for the IDP to publish.
        v2 = self.upload_version(document_id, "blank.pdf", pdf_bytes(None)).json()
        require(
            v2["version_number"] == 2 and v2["processing_status"] == "PENDING", "Invalid v2 upload"
        )
        failed = self.wait_version(document_id, v2["id"], "FAILED")
        require(failed["error_code"] == "NEEDS_OCR_ENGINE", "Unexpected v2 IDP failure reason")
        versions = self.detail(document_id)["versions"]
        original = next(item for item in versions if item["id"] == v1["id"])
        require(
            original["is_serving"] is True and original["is_superseded"] is False,
            "Lost v1 fallback",
        )
        self.read(self.class_a, document_id, 1)
        self.check_original(self.class_a, document_id, first)
        print("  v2 FAILED. v1 still serves and is not superseded.", flush=True)

        retry_path = f"{document_path(document_id)}/versions/{v2['id']}/retry"
        retry = self.owner.request("POST", retry_path).json()
        require(
            retry["processing_status"] == "PENDING" and retry["id"] == v2["id"],
            "Retry did not reset v2",
        )
        retried = self.wait_version(document_id, v2["id"], "FAILED")
        require(retried["error_code"] == "NEEDS_OCR_ENGINE", "Unexpected retry failure reason")
        new_content = pdf_bytes(REVISED_LESSON)
        v3 = self.upload_version(document_id, "revision.pdf", new_content).json()
        require(v3["version_number"] == 3, "Expected version 3")
        served = self.wait_version(document_id, v3["id"], "SUCCESS")
        require(served["is_serving"] is True, "v3 did not become serving")
        versions = self.detail(document_id)["versions"]
        original = next(item for item in versions if item["id"] == v1["id"])
        require(
            original["processing_status"] == "SUCCESS"
            and original["is_serving"] is False
            and original["is_superseded"] is True,
            "v1 must remain SUCCESS with derived is_superseded",
        )
        self.read(self.class_a, document_id, 3, text=REVISED_LESSON)
        self.check_original(self.class_a, document_id, new_content)
        print("  v3 serves. v1 is_superseded=true.", flush=True)

    def grants(self) -> None:
        own_id, _ = self.ready_upload("assisting source", actor=self.assisting)
        self.grant(self.class_b, own_id, actor=self.assisting, expected=DENIED)
        self.grant(self.class_a, own_id, actor=self.assisting)
        self.revoke(self.class_a, own_id, actor=self.assisting)

        archived = self.create_class(self.course_id, "Archived grant denial", [], [])
        self.archive(archived)
        document_id, _ = self.ready_upload("grants")
        self.grant(archived, document_id, expected=409)
        self.grant(self.class_a, document_id)
        self.read(self.class_a, document_id, 1)
        source = class_source(self.class_a, document_id)
        self.outsider.request("GET", source + "/reader", 404)
        self.outsider.request("GET", source + "/download", DENIED)
        self.add_note(self.class_a, document_id, "Retain my note after the grant is revoked.")

        # O1 allows the assisting lecturer to revoke an existing class grant.
        self.revoke(self.class_a, document_id, actor=self.assisting)
        self.student.request("GET", source + "/download", DENIED)
        reader = self.student.request("GET", source + "/reader").json()
        verify_redacted(reader, "GRANT_REVOKED")
        listing = self.student.request(
            "GET", class_documents(self.class_a), params={"limit": 100}
        ).json()["items"]
        require(all(item["id"] != document_id for item in listing), "Revoked grant still appears")

    def internal_ai(self) -> None:
        revoke_id, revoke_version = self.ready_upload("AI revoke")
        keep_id, keep_version = self.ready_upload("AI keep")
        for document_id in (revoke_id, keep_id):
            self.grant(self.class_a, document_id)
        class_path = f"/internal/v1/classes/{self.class_a}/documents/active-grants"
        version_path = (
            f"/internal/v1/documents/{revoke_id}/versions/{revoke_version['id']}/canonical"
        )
        valid = {"X-Service-Key": self.args.internal_secret}
        for path in (class_path, version_path):
            for headers in ({}, {"X-Service-Key": token_hex(32)}):
                check_status(
                    self.direct.get(self.args.document_url + path, headers=headers), (401, 403)
                )
        active = check_status(
            self.direct.get(self.args.document_url + class_path, headers=valid), 200
        ).json()
        require(active["class_id"] == self.class_a, "Wrong internal class")
        grants = {item["document_id"]: item for item in active["items"]}
        for document_id, version in ((revoke_id, revoke_version), (keep_id, keep_version)):
            item = grants[document_id]
            require(
                item["serving_version_id"] == version["id"]
                and item["serving_version_number"] == version["version_number"]
                and item["canonical_schema_version"] == CANONICAL_SCHEMA,
                "Internal listing has the wrong Canonical metadata",
            )
        artifact = check_status(
            self.direct.get(self.args.document_url + version_path, headers=valid), 200
        )
        canonical = artifact.json()
        require(canonical["schema_version"] == CANONICAL_SCHEMA, "Unexpected Canonical schema")
        require(
            len(canonical["pages"]) == revoke_version["page_count"] == 1
            and canonical["pages"][0]["page_id"] == 1
            and any(LESSON in block["text"] for block in canonical["pages"][0]["blocks"]),
            "Wrong Canonical pages or lesson text",
        )
        require(
            sha256(artifact.content).hexdigest() == grants[revoke_id]["canonical_sha256"],
            "Canonical content does not match the active-grants checksum",
        )
        self.revoke(self.class_a, revoke_id)
        active = check_status(
            self.direct.get(self.args.document_url + class_path, headers=valid), 200
        ).json()
        ids = {item["document_id"]: item["serving_version_id"] for item in active["items"]}
        require(
            revoke_id not in ids and ids.get(keep_id) == keep_version["id"],
            "Stale or missing AI grant",
        )

    def notes(self) -> None:
        document_id, _ = self.ready_upload("personal notes")
        self.grant(self.class_a, document_id)
        text = "I will compare evaporation and condensation."
        note = self.add_note(self.class_a, document_id, text)
        self.bookmark(self.class_a, document_id, True)
        before = self.read(self.class_a, document_id, 1)
        require(
            before["is_bookmarked"] is True and before["notes"] == [note],
            "Missing reader note/bookmark",
        )
        note_path = f"/notes/{note['id']}"
        peer_notes = self.peer.request(
            "GET", "/users/me/notes", params={"document_id": document_id, "class_id": self.class_a}
        ).json()
        require(peer_notes["items"] == [], "Enrolled peer can read a private note")
        self.peer.request("PATCH", note_path, DENIED, json={"note_text": "Another student's edit"})
        self.peer.request("DELETE", note_path, DENIED)

        self.revoke(self.class_a, document_id)
        personal = self.student.request(
            "GET", "/users/me/notes", params={"document_id": document_id, "class_id": self.class_a}
        ).json()["items"]
        require(len(personal) == 1 and personal[0]["id"] == note["id"], "Personal note disappeared")
        self.check_redacted_note(personal[0], text)
        revised = "I now remember where the water vapor cools."
        update = self.student.request("PATCH", note_path, json={"note_text": revised}).json()
        self.check_redacted_note(update, revised)
        reader = self.student.request(
            "GET", class_source(self.class_a, document_id) + "/reader"
        ).json()
        verify_redacted(reader, "GRANT_REVOKED")
        require(reader["notes"] == [update], "Redacted reader did not preserve the edited note")
        self.student.request("GET", class_source(self.class_a, document_id) + "/download", DENIED)
        print(
            "  Notes list, note PATCH and Reader preserve note text with source_quote=null.",
            flush=True,
        )

        archived = self.archive_class_id
        self.grant(archived, document_id)
        original = self.add_note(archived, document_id, "Before archive")
        self.bookmark(archived, document_id, True)
        self.archive(archived)
        self.read(archived, document_id, 1)
        self.add_note(archived, document_id, "After archive")
        edited = self.student.request(
            "PATCH", f"/notes/{original['id']}", json={"note_text": "Edited after archive"}
        ).json()
        require(
            edited["note_text"] == "Edited after archive"
            and edited["is_source_accessible"] is True
            and edited["source_quote"] == LESSON,
            "Archived class must preserve editable personal notes and live source",
        )
        self.bookmark(archived, document_id, False)
        self.bookmark(archived, document_id, True)

    @staticmethod
    def check_redacted_note(note: dict, text: str) -> None:
        require(note["note_text"] == text, "Personal note text was lost or changed")
        require(
            note["is_source_accessible"] is False and note["source_quote"] is None,
            "Note leaked source",
        )

    def multi_class_delete(self) -> None:
        document_id, _ = self.ready_upload("multi-class delete")
        for class_id in (self.class_a, self.class_b):
            self.grant(class_id, document_id)
            self.read(class_id, document_id, 1)
        self.add_note(self.class_a, document_id, "Keep this note after delete")
        path = document_path(document_id)
        warning = self.owner.request("DELETE", path, 409).json()["error"]["details"]
        require(warning["active_grant_count"] == 2, "Delete warning must count two active grants")
        require(
            set(warning["affected_class_ids"]) == {self.class_a, self.class_b},
            "Wrong affected classes",
        )
        print(
            f"  HTTP 409  active_grant_count=2  affected_class_ids={warning['affected_class_ids']}",
            flush=True,
        )
        self.read(self.class_a, document_id, 1)
        self.owner.request("DELETE", path, 204, params={"confirm_multi_class": "true"})
        for class_id in (self.class_a, self.class_b):
            source = class_source(class_id, document_id)
            self.student.request("GET", source + "/download", DENIED)
            response = self.student.request(
                "GET", source + "/reader", 200 if class_id == self.class_a else (200, 404)
            )
            if response.status_code == 200:
                verify_redacted(response.json(), "DOCUMENT_DELETED")

    def worker_crash(self) -> None:
        if self.args.skip_docker_faults:
            raise CheckSkipped("--skip-docker-faults")
        lease = int(
            self.compose.run(
                "exec",
                "-T",
                "document-worker",
                "python",
                "-c",
                "import os; print(os.environ.get('DOCUMENT_WORKER_LEASE_SECONDS', '300'))",
            ).stdout.strip()
        )
        if lease > 15:
            print(
                f"  Worker lease is {lease}s. Set DOCUMENT_WORKER_LEASE_SECONDS=10 "
                "and recreate document-worker for a shorter demo run.",
                flush=True,
            )
        original = pdf_bytes()
        document_id, _ = self.ready_upload("worker crash", "lesson.pdf", original)
        self.grant(self.class_a, document_id)
        self.read(self.class_a, document_id, 1)
        content = pdf_bytes(REVISED_LESSON, pages=120, lines=24, attachment_bytes=10_500_000)
        with self.compose.restore_service("document-worker"):
            self.compose.run("stop", "document-worker")
            pending = self.upload_version(document_id, "crash.pdf", content).json()
            require(pending["processing_status"] == "PENDING", "Expected queued crash fixture")
            version_id = pending["id"]
            with self.compose.hold_version_publish_lock(version_id):
                self.compose.run("start", "document-worker")
                self.wait_version(document_id, version_id, "PROCESSING", interval=0.1)
                attempt, lease_until = self.compose.version_attempt(version_id)
                require(attempt == 1, "Expected first worker attempt")
                print(
                    f"  attempt_count={attempt}  lease expires {lease_until.isoformat()}",
                    flush=True,
                )
                self.compose.run("kill", "document-worker")
                print(
                    f"  {datetime.now(UTC).isoformat(timespec='milliseconds')} worker killed",
                    flush=True,
                )
                after = self.detail(document_id)
                require(
                    after["document"]["latest_version"]["processing_status"] == "PROCESSING",
                    "Version committed before crash",
                )
                require(
                    after["document"]["serving_version"]["version_number"] == 1, "Crash lost v1"
                )
                self.check_source_blob(document_id, version_id, content)
                self.read(self.class_a, document_id, 1)
                self.check_original(self.class_a, document_id, original)
        print(
            f"  {datetime.now(UTC).isoformat(timespec='milliseconds')} worker restarted", flush=True
        )
        remaining = max(0, (lease_until - datetime.now(UTC)).total_seconds())
        self.wait_version(
            document_id, version_id, "SUCCESS", timeout=remaining + self.args.wait_timeout
        )
        recovered_attempt, _ = self.compose.version_attempt(version_id)
        require(
            recovered_attempt == attempt + 1, "Recovery must claim the same version one more time"
        )
        print(f"  attempt_count {attempt} -> {recovered_attempt}", flush=True)
        self.read(self.class_a, document_id, 2, text=REVISED_LESSON)
        self.check_original(self.class_a, document_id, content)

    def check_source_blob(self, document_id: str, version_id: str, content: bytes) -> None:
        blob_hash = self.compose.run(
            "exec",
            "-T",
            "document",
            "python",
            "-c",
            "import hashlib, os, pathlib, sys\n"
            "path = pathlib.Path(os.environ['DOCUMENT_STORAGE_PATH'], 'source', *sys.argv[1:])\n"
            "with path.open('rb') as source: print(hashlib.file_digest(source, 'sha256').hexdigest())",
            str(UUID(document_id)),
            str(UUID(version_id)),
        ).stdout.strip()
        require(
            blob_hash == sha256(content).hexdigest(), "Original source blob changed after crash"
        )

    def latency(self) -> None:
        document_id, _ = self.ready_upload("latency")
        self.grant(self.class_a, document_id)
        paths = [
            (self.owner, "/documents"),
            (self.student, class_documents(self.class_a)),
            (self.student, class_source(self.class_a, document_id) + "/reader"),
        ]
        for actor, path in paths:
            actor.request("GET", path)
            samples = []
            for _ in range(20):
                start = perf_counter()
                actor.request("GET", path)
                samples.append((perf_counter() - start) * 1000)
            samples.sort()
            p95 = samples[ceil(0.95 * len(samples)) - 1]
            print(
                f"  GET {path}  n={len(samples)}  min={samples[0]:.1f} ms  "
                f"p50={median(samples):.1f} ms  p95={p95:.1f} ms",
                flush=True,
            )

    def database_isolation(self) -> None:
        if self.args.skip_docker_faults and not self.compose.available():
            raise CheckSkipped("Docker unavailable for the DB role check")
        login = self.compose.psql("SELECT current_user, current_database();").stdout.strip()
        require(
            login == f"{self.args.document_db_user}|{self.args.db_name}",
            "Connected as the wrong DB role or database",
        )
        self.compose.psql("SELECT 1 FROM document.documents LIMIT 0;")
        denied = self.compose.psql("SELECT 1 FROM core.users LIMIT 1;", check=False)
        require(
            denied.returncode != 0
            and "42501" in denied.stderr
            and "permission denied" in denied.stderr,
            "Expected PostgreSQL permission denied (SQLSTATE 42501) on core.users",
        )

    def service_isolation(self) -> None:
        if self.args.skip_docker_faults:
            raise CheckSkipped("--skip-docker-faults")
        self.owner.request("GET", "/documents/policy")
        with self.compose.restore_service("document"):
            self.compose.run("stop", "document")
            classes = self.student.request("GET", "/classes").json()["items"]
            require(
                any(item["id"] == self.class_a for item in classes), "Core lost the student's class"
            )
            unavailable = self.owner.request("GET", "/documents/policy", 503).json()
            require(
                unavailable["error"]["code"] == "UPSTREAM_UNAVAILABLE", "Wrong proxy outage error"
            )
        health_ready(self.direct, self.args.document_url)
        self.owner.request("GET", "/documents/policy")


@contextmanager
def subcheck(label: str) -> Iterator[None]:
    start = perf_counter()
    try:
        yield
    except Exception:
        print(f"  FAIL {label} ({perf_counter() - start:.2f}s)", flush=True)
        raise
    print(f"  PASS {label} ({perf_counter() - start:.2f}s)", flush=True)


def run_check(label: str, check: Callable[[], None]) -> str:
    start = perf_counter()
    try:
        check()
    except CheckSkipped as error:
        status, message = "SKIP", str(error)
    except (CheckFailure, httpx.HTTPError, OSError, subprocess.TimeoutExpired) as error:
        status = "FAIL"
        # TimeoutExpired has the exact command, which may contain sensitive details.
        message = (
            "Docker Compose command timed out"
            if isinstance(error, subprocess.TimeoutExpired)
            else str(error)
        )
    except (KeyError, ValueError, TypeError, StopIteration) as error:
        status, message = (
            "FAIL",
            f"Unexpected response or configured value ({type(error).__name__})",
        )
    else:
        status, message = "PASS", ""
    print(
        f"{status} {label} ({perf_counter() - start:.2f}s)" + (f" - {message}" if message else ""),
        flush=True,
    )
    return status


def http_origin(value: str) -> str:
    try:
        url = httpx.URL(value)
        if (
            url.scheme in {"http", "https"}
            and url.host
            and url.path == "/"
            and not (url.userinfo or url.query or url.fragment)
        ):
            return str(url).rstrip("/")
    except httpx.InvalidURL:
        pass
    raise argparse.ArgumentTypeError(
        "expected an HTTP(S) origin without path, credentials, or query"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--web-url", type=http_origin, default=f"http://127.0.0.1:{os.getenv('WEB_PORT', '3000')}"
    )
    parser.add_argument(
        "--origin",
        type=http_origin,
        help="Exact Web WEB_ORIGIN and Core CORS origin (default: --web-url)",
    )
    parser.add_argument(
        "--core-url", type=http_origin, default=f"http://127.0.0.1:{os.getenv('CORE_PORT', '8000')}"
    )
    parser.add_argument(
        "--document-url",
        type=http_origin,
        default=f"http://127.0.0.1:{os.getenv('DOCUMENT_PORT', '8001')}",
    )
    parser.add_argument("--internal-secret", default=os.getenv("INTERNAL_SERVICE_SECRET"))
    parser.add_argument(
        "--admin-email", default=os.getenv("CORE_SEED_ADMIN_EMAIL", "admin@seed.example.com")
    )
    parser.add_argument("--admin-password", default=os.getenv("CORE_SEED_ADMIN_PASSWORD"))
    parser.add_argument("--lecturer-email", default=os.getenv("E2E_LECTURER_EMAIL", SEED_LECTURER))
    parser.add_argument("--lecturer-password", default=os.getenv("E2E_LECTURER_PASSWORD"))
    parser.add_argument(
        "--assisting-email", default=os.getenv("E2E_ASSISTING_EMAIL", SEED_ASSISTING)
    )
    parser.add_argument("--assisting-password", default=os.getenv("E2E_ASSISTING_PASSWORD"))
    parser.add_argument("--compose-project", help="Pass -p PROJECT to each Compose command")
    parser.add_argument("--db-name", default=os.getenv("POSTGRES_DB", "rag_course"))
    parser.add_argument("--document-db-user", default=os.getenv("DOCUMENT_DB_USER", "document_svc"))
    parser.add_argument(
        "--document-db-password",
        default=os.getenv("DOCUMENT_DB_PASSWORD", "document-change-me-local-only"),
    )
    parser.add_argument(
        "--skip-docker-faults",
        action="store_true",
        help="Skip worker crash and Document outage checks",
    )
    parser.add_argument(
        "--wait-timeout", type=float, default=120, help="Seconds to wait for IDP (default: 120)"
    )
    demo = parser.add_mutually_exclusive_group()
    demo.add_argument(
        "--write-demo-fixtures", type=Path, metavar="DIR", help="Write browser demo files and exit"
    )
    demo.add_argument(
        "--prepare-demo-logins",
        action="store_true",
        help="Set both seed lecturer passwords using Core's one-time reset",
    )
    args = parser.parse_args()
    if args.write_demo_fixtures:
        return args
    args.origin = args.origin or args.web_url
    if args.prepare_demo_logins:
        if (args.lecturer_email, args.assisting_email) != (SEED_LECTURER, SEED_ASSISTING):
            parser.error("--prepare-demo-logins requires both synthetic seed lecturers")
        if not args.lecturer_password or not args.assisting_password:
            parser.error("set E2E_LECTURER_PASSWORD and E2E_ASSISTING_PASSWORD")
        return args
    if not args.internal_secret:
        parser.error("set INTERNAL_SERVICE_SECRET or pass --internal-secret")
    if not args.admin_password:
        parser.error("set CORE_SEED_ADMIN_PASSWORD or pass --admin-password")
    if not args.wait_timeout > 0:
        parser.error("--wait-timeout must be positive")
    if args.lecturer_email != SEED_LECTURER and not args.lecturer_password:
        parser.error("--lecturer-password is required for a non-seed account")
    if args.assisting_email != SEED_ASSISTING and not args.assisting_password:
        parser.error("--assisting-password is required for a non-seed account")
    return args


def main() -> int:
    args = parse_args()
    if args.write_demo_fixtures:
        return int(
            run_check("write demo fixtures", lambda: write_demo_fixtures(args.write_demo_fixtures))
            == "FAIL"
        )
    start = perf_counter()
    with ExitStack() as stack:
        suite = Verification(args, stack)
        print(f"Web {args.web_url}  Origin {args.origin}", flush=True)
        if args.prepare_demo_logins:
            return int(
                run_check(
                    "Prepare synthetic lecturer logins",
                    lambda: suite.staff_logins(reset_seed_passwords=True),
                )
                == "FAIL"
            )
        setup_status = run_check("Setup / provision disposable Compose fixtures", suite.setup)
        checks = [
            ("01 Policy and auth fail-closed", suite.policy),
            ("02 Ingress validation [DOC-T03/T04, B3/B5]", suite.ingress),
            ("03 All five formats via real IDP", suite.formats),
            ("04 NEEDS_REVIEW and owner-only accept", suite.needs_review),
            ("05 H3 fallback, retry and derived superseded [DOC-T05]", suite.fallback),
            ("06 Class grants and immediate revocation [DOC-T06..T10, O1]", suite.grants),
            ("07 Internal AI endpoints [ADR-002]", suite.internal_ai),
            ("08 Private notes, bookmarks and archived class [H6/L6/O2]", suite.notes),
            ("09 Multi-class delete warning [B6, DOC-T02]", suite.multi_class_delete),
            ("10 Worker crash and lease recovery [QAS-10]", suite.worker_crash),
            ("11a Web latency samples [QAS-01/02]", suite.latency),
            ("11b Database role isolation", suite.database_isolation),
            ("11c Document service outage and recovery [QAS-07]", suite.service_isolation),
        ]
        if setup_status == "PASS":
            statuses = [run_check(label, check) for label, check in checks]
        else:
            statuses = ["SKIP"] * len(checks)
            for label, _ in checks:
                print(f"SKIP {label} (0.00s) - setup failed", flush=True)
        print(
            f"Total {statuses.count('PASS')} PASS, {statuses.count('FAIL')} FAIL, "
            f"{statuses.count('SKIP')} SKIP ({perf_counter() - start:.2f}s including setup).",
            flush=True,
        )
        return int(setup_status != "PASS" or "FAIL" in statuses)


if __name__ == "__main__":
    raise SystemExit(main())
