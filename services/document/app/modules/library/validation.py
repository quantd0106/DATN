import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from xml.sax import SAXParseException
from xml.sax.handler import ContentHandler
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
from zlib import error as ZlibError

import pymupdf
from defusedxml import sax
from defusedxml.common import DefusedXmlException
from fastapi import UploadFile

from app.common.config import Settings
from app.common.errors import AppError
from app.common.storage import CHUNK_SIZE
from app.common.types import DocumentFormat

FORMATS: dict[DocumentFormat, tuple[tuple[str, ...], str]] = {
    "PDF": ((".pdf",), "application/pdf"),
    "PPTX": (
        (".pptx",),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    "DOCX": ((".docx",), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "TXT": ((".txt",), "text/plain"),
    "MARKDOWN": ((".md", ".markdown"), "text/markdown"),
}
EXTENSIONS = {ext: kind for kind, (extensions, _) in FORMATS.items() for ext in extensions}
OFFICE_MAIN = {"DOCX": "word/document.xml", "PPTX": "ppt/presentation.xml"}
SLIDE = re.compile(r"ppt/slides/slide[0-9]+\.xml")
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_RATIO = 200


@dataclass(frozen=True)
class ValidatedFile:
    filename: str
    file_type: DocumentFormat
    mime_type: str
    size_bytes: int
    page_count: int | None
    sha256: str


def invalid_file(reason: str) -> AppError:
    messages = {
        "EMPTY_FILE": "Tệp tải lên trống.",
        "ENCRYPTED_PDF": "Tệp PDF được mã hóa. Vui lòng tải lên bản đã giải mã.",
        "PAGE_LIMIT_EXCEEDED": "Tệp vượt số trang hoặc trang chiếu cho phép.",
        "UNSUPPORTED_FILE_TYPE": "Định dạng tệp không được hỗ trợ.",
        "ZIP_LIMIT_EXCEEDED": "Tệp Office vượt giới hạn giải nén cho phép.",
    }
    return AppError(
        422, messages.get(reason, "Nội dung tệp không hợp lệ."), details={"reason": reason}
    )


def pdf_pages(content: bytes, limit: int) -> int:
    if not content.startswith(b"%PDF-"):
        raise invalid_file("FILE_TYPE_MISMATCH")
    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass or document.xref_get_key(-1, "Encrypt")[0] != "null":
                raise invalid_file("ENCRYPTED_PDF")
            pages = document.page_count
    except (RuntimeError, ValueError) as exc:
        raise invalid_file("CORRUPT_FILE") from exc
    if pages < 1:
        raise invalid_file("EMPTY_DOCUMENT")
    if pages > limit:
        raise invalid_file("PAGE_LIMIT_EXCEEDED")
    return pages


def check_office(content: bytes, kind: DocumentFormat, limit: int) -> int | None:
    if not content.startswith(b"PK\x03\x04"):
        raise invalid_file("FILE_TYPE_MISMATCH")
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_ZIP_BYTES:
                raise invalid_file("ZIP_LIMIT_EXCEEDED")
            names: set[str] = set()
            for entry in entries:
                name = entry.orig_filename.rstrip("/")
                normalized_name = name.casefold()
                if (
                    not name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or any(char in name for char in ("\\", ":", "\x00"))
                    or normalized_name in names
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.flag_bits & 1
                    or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                ):
                    raise invalid_file("CORRUPT_FILE")
                if entry.file_size > MAX_ZIP_RATIO * max(1, entry.compress_size):
                    raise invalid_file("ZIP_LIMIT_EXCEEDED")
                names.add(normalized_name)

            files = [entry for entry in entries if not entry.is_dir()]
            members = {entry.filename for entry in files}
            main = OFFICE_MAIN[kind]
            if main not in members or any(
                other in members for other in OFFICE_MAIN.values() if other != main
            ):
                raise invalid_file("FILE_TYPE_MISMATCH")
            pages = sum(bool(SLIDE.fullmatch(name)) for name in members) if kind == "PPTX" else None
            if pages is not None and pages > limit:
                raise invalid_file("PAGE_LIMIT_EXCEEDED")

            # Consume entries to validate their CRCs and keep unsafe XML out of the IDP queue.
            for entry in files:
                with archive.open(entry) as member:
                    if entry.filename.lower().endswith((".xml", ".rels")):
                        sax.parse(member, ContentHandler(), forbid_dtd=True)
                    else:
                        while member.read(CHUNK_SIZE):
                            pass
            return pages
    except (
        BadZipFile,
        DefusedXmlException,
        SAXParseException,
        EOFError,
        OSError,
        RuntimeError,
        ValueError,
        ZlibError,
    ) as exc:
        raise invalid_file("CORRUPT_FILE") from exc


def check_text(content: bytes) -> None:
    if content.startswith((b"%PDF-", b"PK\x03\x04", b"\xff\xfe\x00\x00")):
        raise invalid_file("FILE_TYPE_MISMATCH")
    try:
        encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        decoded = content.decode(encoding)
    except UnicodeError as exc:
        raise invalid_file("FILE_TYPE_MISMATCH") from exc
    if re.search(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]", decoded):
        raise invalid_file("FILE_TYPE_MISMATCH")
    if not decoded.strip():
        raise invalid_file("EMPTY_FILE")


def validate_upload(upload: UploadFile, settings: Settings) -> ValidatedFile:
    maximum = settings.document_max_size_bytes
    if upload.size is not None and upload.size > maximum:
        raise AppError(413, details={"reason": "FILE_TOO_LARGE"})

    upload.file.seek(0)
    data = BytesIO()
    while chunk := upload.file.read(min(CHUNK_SIZE, maximum - data.tell() + 1)):
        data.write(chunk)
        if data.tell() > maximum:
            raise AppError(413, details={"reason": "FILE_TOO_LARGE"})
    content = data.getvalue()
    if not content:
        raise invalid_file("EMPTY_FILE")

    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if len(filename) > 255 or not filename.isprintable():
        raise invalid_file("UNSUPPORTED_FILE_TYPE")
    kind = EXTENSIONS.get(PurePosixPath(filename).suffix.lower())
    if kind is None:
        raise invalid_file("UNSUPPORTED_FILE_TYPE")

    mime_type = FORMATS[kind][1]
    declared = (upload.content_type or "").split(";", 1)[0].strip().lower()
    allowed_mimes = {"", "application/octet-stream", mime_type}
    if kind == "MARKDOWN":
        allowed_mimes.add("text/plain")
    if declared not in allowed_mimes:
        raise invalid_file("FILE_TYPE_MISMATCH")

    pages = None
    if kind == "PDF":
        pages = pdf_pages(content, settings.document_max_pages)
    elif kind in OFFICE_MAIN:
        pages = check_office(content, kind, settings.document_max_pages)
    else:
        check_text(content)
    upload.file.seek(0)
    return ValidatedFile(
        filename, kind, mime_type, len(content), pages, sha256(content).hexdigest()
    )
