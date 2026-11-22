import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.common.errors import AppError

ALLOWED_MIME_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06"),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "text/plain": (),
    "text/markdown": (),
}

ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".zip": {"application/zip", "application/x-zip-compressed"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
}


@dataclass(frozen=True)
class StoredBlob:
    blob_ref: str
    file_name: str
    mime_type: str
    file_size_bytes: int
    file_sha256: str


class BlobStore(Protocol):
    def put(
        self,
        *,
        file_name: str,
        mime_type: str,
        content: bytes,
        max_bytes: int,
        accepted_types: list[str] | None = None,
    ) -> StoredBlob: ...

    def get(self, blob_ref: str) -> bytes: ...

    def delete(self, blob_ref: str) -> None: ...


def sanitize_file_name(file_name: str) -> str:
    base = Path(file_name.replace("\\", "/")).name.strip()
    if not base or base in {".", ".."} or len(base) > 255:
        raise AppError(422, "INVALID_UPLOAD_FILE")
    return base


def validate_upload(
    *,
    file_name: str,
    mime_type: str,
    content: bytes,
    max_bytes: int,
    accepted_types: list[str] | None = None,
) -> tuple[str, str]:
    clean_name = sanitize_file_name(file_name)
    if not content or len(content) > max_bytes:
        raise AppError(413 if len(content) > max_bytes else 422, "INVALID_UPLOAD_FILE")
    ext = Path(clean_name).suffix.lower()
    allowed_mimes = ALLOWED_EXTENSIONS.get(ext)
    if not allowed_mimes:
        raise AppError(422, "INVALID_UPLOAD_FILE")
    normalized_mime = (mime_type or "").split(";")[0].strip().lower()
    if normalized_mime == "application/x-zip-compressed" and ext == ".zip":
        normalized_mime = "application/zip"
    if normalized_mime not in allowed_mimes and not (ext == ".md" and normalized_mime == "text/plain"):
        raise AppError(422, "INVALID_UPLOAD_FILE")
    if accepted_types:
        allowed_filter = {t.strip().lower() for t in accepted_types if t.strip()}
        if allowed_filter and ext not in allowed_filter and normalized_mime not in allowed_filter:
            raise AppError(422, "INVALID_UPLOAD_FILE")
    signatures = ALLOWED_MIME_SIGNATURES.get(normalized_mime, ())
    if signatures and not any(content.startswith(sig) for sig in signatures):
        raise AppError(422, "INVALID_UPLOAD_FILE")
    if normalized_mime in {"text/plain", "text/markdown"}:
        if b"\x00" in content:
            raise AppError(422, "INVALID_UPLOAD_FILE")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise AppError(422, "INVALID_UPLOAD_FILE") from None
    return clean_name, normalized_mime


class LocalFsBlobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _resolve_ref(self, blob_ref: str) -> Path:
        if not blob_ref or "/" in blob_ref or "\\" in blob_ref or blob_ref in {".", ".."}:
            raise AppError(404)
        candidate = (self.root / blob_ref).resolve()
        if candidate.parent != self.root:
            raise AppError(404)
        return candidate

    def put(
        self,
        *,
        file_name: str,
        mime_type: str,
        content: bytes,
        max_bytes: int,
        accepted_types: list[str] | None = None,
    ) -> StoredBlob:
        clean_name, normalized_mime = validate_upload(
            file_name=file_name,
            mime_type=mime_type,
            content=content,
            max_bytes=max_bytes,
            accepted_types=accepted_types,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(content).hexdigest()
        blob_ref = f"{uuid4().hex}{Path(clean_name).suffix.lower()}"
        target = self._resolve_ref(blob_ref)
        target.write_bytes(content)
        return StoredBlob(
            blob_ref=blob_ref,
            file_name=clean_name,
            mime_type=normalized_mime,
            file_size_bytes=len(content),
            file_sha256=digest,
        )

    def get(self, blob_ref: str) -> bytes:
        target = self._resolve_ref(blob_ref)
        if not target.is_file():
            raise AppError(404)
        return target.read_bytes()

    def delete(self, blob_ref: str) -> None:
        target = self._resolve_ref(blob_ref)
        if target.is_file():
            target.unlink()
