import os
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from hashlib import file_digest, sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Annotated, BinaryIO, Protocol
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends
from sqlalchemy.exc import DBAPIError

from app.common.config import get_settings
from app.common.errors import AppError

CHUNK_SIZE = 1024 * 1024
KEY_PART = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*")
WINDOWS_DEVICE = re.compile(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", re.IGNORECASE)


class StorageBackend(Protocol):
    def save_bytes(self, key: str, data: bytes, *, expected_sha256: str | None = None) -> str: ...

    def save_file(
        self, key: str, source: BinaryIO, *, expected_sha256: str | None = None
    ) -> str: ...

    def open_stream(self, key: str) -> BinaryIO: ...

    def read_bytes(self, key: str, *, expected_sha256: str | None = None) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def check_ready(self) -> bool: ...


class StorageIntegrityError(OSError):
    pass


class LocalVolumeStorage:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        parts = key.split("/")
        if any(
            not KEY_PART.fullmatch(part)
            or part.endswith(".")
            or WINDOWS_DEVICE.fullmatch(part.split(".", 1)[0])
            for part in parts
        ):
            raise ValueError("Invalid storage key")
        path = self.root
        for part in parts:
            path /= part
            if path.is_symlink() or path.is_junction():
                raise ValueError("Invalid storage key")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    def save_bytes(self, key: str, data: bytes, *, expected_sha256: str | None = None) -> str:
        return self.save_file(key, BytesIO(data), expected_sha256=expected_sha256)

    def save_file(self, key: str, source: BinaryIO, *, expected_sha256: str | None = None) -> str:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        digest = sha256()
        try:
            with NamedTemporaryFile(dir=target.parent, mode="w+b", delete=False) as temp:
                temp_path = Path(temp.name)
                while chunk := source.read(CHUNK_SIZE):
                    temp.write(chunk)
                    digest.update(chunk)
                checksum = digest.hexdigest()
                temp.flush()
                os.fsync(temp.fileno())
                temp.seek(0)
                if file_digest(temp, "sha256").hexdigest() != checksum or (
                    expected_sha256 is not None and expected_sha256 != checksum
                ):
                    raise StorageIntegrityError("Blob checksum mismatch")
            os.replace(temp_path, target)
            return checksum
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def open_stream(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def read_bytes(self, key: str, *, expected_sha256: str | None = None) -> bytes:
        with self.open_stream(key) as stream:
            data = stream.read()
        if expected_sha256 is not None and sha256(data).hexdigest() != expected_sha256:
            raise StorageIntegrityError("Blob checksum mismatch")
        return data

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def check_ready(self) -> bool:
        key = f"health/{uuid4()}"
        try:
            checksum = self.save_bytes(key, b"ready")
            try:
                return self.read_bytes(key, expected_sha256=checksum) == b"ready"
            finally:
                self.delete(key)
        except (OSError, ValueError):
            return False


def stream_chunks(stream: BinaryIO) -> Iterator[bytes]:
    with stream:
        while chunk := stream.read(CHUNK_SIZE):
            yield chunk


def safe_content_disposition(filename: str) -> str:
    # The fallback is ASCII and the RFC 5987 value contains no raw header metacharacters.
    suffix = PurePosixPath(filename).suffix.lower()
    safe_suffix = suffix if suffix in {".pdf", ".pptx", ".docx", ".txt", ".md", ".markdown"} else ""
    return (
        f'attachment; filename="document{safe_suffix}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def get_storage() -> StorageBackend:
    return LocalVolumeStorage(get_settings().document_storage_path)


Storage = Annotated[StorageBackend, Depends(get_storage)]


@contextmanager
def staged_upload(
    storage: StorageBackend, key: str, source: BinaryIO, *, expected_sha256: str
) -> Iterator[str]:
    try:
        checksum = storage.save_file(key, source, expected_sha256=expected_sha256)
    except OSError as exc:
        raise AppError(503) from exc
    try:
        yield checksum
    except Exception as exc:
        # A lost connection during commit can leave the outcome unknown. Keep the blob
        # in that case so a committed version still has an original file.
        if not isinstance(exc, DBAPIError) or not exc.connection_invalidated:
            with suppress(OSError):
                storage.delete(key)
        raise
