import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.config import Settings
from app.common.db import transaction, utc_now
from app.common.storage import CHUNK_SIZE, StorageBackend, StorageIntegrityError
from app.common.types import DocumentFormat
from app.modules.library.public import get_live_document
from app.modules.versions.idp_runner import (
    PROCESSING_ERROR_MESSAGE,
    SOURCE_SUFFIX,
    IdpRunOutcome,
    run_idp_for_version,
)
from app.modules.versions.models import DocumentVersion
from app.modules.versions.repository import (
    expired_versions,
    get_version_by_id,
    next_available_version,
)
from app.modules.versions.service import record_version_published

SessionFactory = Callable[[], Session]


class IdpRunner(Protocol):
    def __call__(
        self, source_path: Path, work_out_dir: Path, *, config: Settings, file_type: DocumentFormat
    ) -> IdpRunOutcome: ...


@dataclass(frozen=True)
class VersionLease:
    id: UUID
    document_id: UUID
    attempt: int
    file_type: DocumentFormat
    source_key: str
    source_sha256: str
    source_size: int


def _claim_version(session_factory: SessionFactory, config: Settings) -> VersionLease | None:
    with session_factory() as session, transaction(session):
        now = utc_now()
        for expired in expired_versions(session, now):
            expired.updated_at = now
            if expired.attempt_count >= config.document_worker_max_attempts:
                expired.processing_status = "FAILED"
                expired.error_code = "WORKER_LEASE_EXPIRED"
                expired.error_message = "Quá thời gian xử lý tài liệu. Vui lòng thử lại."
            else:
                expired.processing_status = "PENDING"
                expired.available_at = now
        session.flush()
        version = next_available_version(session, now)
        if version is None:
            return None
        version.processing_status = "PROCESSING"
        version.attempt_count += 1
        version.available_at = now + timedelta(seconds=config.document_worker_lease_seconds)
        version.updated_at = now
        return VersionLease(
            version.id,
            version.document_id,
            version.attempt_count,
            cast(DocumentFormat, version.file_type),
            version.storage_key,
            version.source_sha256,
            version.file_size_bytes,
        )


def _copy_source(storage: StorageBackend, lease: VersionLease, path: Path) -> None:
    digest = sha256()
    size = 0
    with storage.open_stream(lease.source_key) as source, path.open("wb") as target:
        while chunk := source.read(CHUNK_SIZE):
            size += len(chunk)
            if size > lease.source_size:
                raise StorageIntegrityError("Source size mismatch")
            digest.update(chunk)
            target.write(chunk)
    if size != lease.source_size or digest.hexdigest() != lease.source_sha256:
        raise StorageIntegrityError("Source integrity check failed")


def _leased_version(
    session: Session, lease: VersionLease, *, lock: bool = False
) -> DocumentVersion | None:
    version = get_version_by_id(session, lease.document_id, lease.id, lock=lock)
    if version is None or version.processing_status != "PROCESSING":
        return None
    # A reclaimed lease always advances the attempt counter.
    return version if version.attempt_count == lease.attempt else None


def _save_canonical(
    storage: StorageBackend, lease: VersionLease, outcome: IdpRunOutcome, out_dir: Path
) -> tuple[str, str]:
    if not outcome.canonical_bytes or not outcome.canonical_schema_version:
        raise StorageIntegrityError("Missing Canonical artifact")

    prefix = f"canonical/{lease.document_id}/{lease.id}"
    asset_dir = (out_dir / "assets").resolve()
    if not asset_dir.is_relative_to(out_dir.resolve()):
        raise StorageIntegrityError("Invalid Canonical asset directory")
    for path in asset_dir.rglob("*"):
        if path.is_file() and path.resolve().is_relative_to(asset_dir):
            asset_key = f"{prefix}/assets/{path.relative_to(asset_dir).as_posix()}"
            with path.open("rb") as asset:
                storage.save_file(asset_key, asset)

    key = f"{prefix}.json"
    digest = sha256(outcome.canonical_bytes).hexdigest()
    if storage.save_bytes(key, outcome.canonical_bytes, expected_sha256=digest) != digest:
        raise StorageIntegrityError("Canonical checksum mismatch")
    return key, digest


def _mark_failed(version: DocumentVersion, error_code: str, error_message: str) -> None:
    version.processing_status = "FAILED"
    version.updated_at = utc_now()
    version.error_code = error_code
    version.error_message = error_message


def _commit_outcome(
    session_factory: SessionFactory, lease: VersionLease, outcome: IdpRunOutcome
) -> None:
    with session_factory() as session, transaction(session):
        document = get_live_document(session, lease.document_id, lock=True)
        if document is None or (version := _leased_version(session, lease, lock=True)) is None:
            return
        _mark_failed(
            version,
            outcome.error_code or "IDP_PROCESSING_ERROR",
            outcome.error_message or PROCESSING_ERROR_MESSAGE,
        )


def _finish_version(
    session_factory: SessionFactory,
    storage: StorageBackend,
    lease: VersionLease,
    outcome: IdpRunOutcome,
    out_dir: Path,
) -> None:
    if outcome.status == "FAILED":
        _commit_outcome(session_factory, lease, outcome)
        return
    with session_factory() as session, transaction(session):
        if session.get_bind().dialect.name == "postgresql":
            # Serialize writes to the deterministic artifact key through the version commit.
            lock_id = int.from_bytes(lease.id.bytes[:8], signed=True)
            session.execute(select(func.pg_advisory_xact_lock(lock_id)))

        document = get_live_document(session, lease.document_id, lock=True)
        if document is None or (version := _leased_version(session, lease, lock=True)) is None:
            return
        try:
            key, digest = _save_canonical(storage, lease, outcome, out_dir)
        except (OSError, ValueError):
            _mark_failed(
                version,
                "CANONICAL_STORAGE_ERROR",
                "Không thể lưu dữ liệu đọc của tài liệu. Vui lòng thử lại.",
            )
            return

        now = utc_now()
        version.processing_status = outcome.status
        version.updated_at = now
        version.error_code = version.error_message = None
        version.canonical_storage_key = key
        version.canonical_sha256 = digest
        version.canonical_schema_version = outcome.canonical_schema_version
        version.page_count = outcome.page_count
        version.review_summary_json = (
            outcome.review_summary if outcome.status == "NEEDS_REVIEW" else None
        )
        document.updated_at = now
        if outcome.status == "SUCCESS":
            version.acceptance_mode = "AUTO_PUBLISHED"
            record_version_published(session, version, document.school_id, now)


def process_next_version(
    session_factory: SessionFactory,
    storage: StorageBackend,
    config: Settings,
    *,
    runner: IdpRunner = run_idp_for_version,
) -> bool:
    lease = _claim_version(session_factory, config)
    if lease is None:
        return False

    version_dir = Path(tempfile.gettempdir()) / "doc-worker" / str(lease.id)
    work_dir = None
    try:
        try:
            version_dir.mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(dir=version_dir, prefix=f"{lease.attempt}-"))
            input_dir = work_dir / "input"
            input_dir.mkdir()
            source_path = input_dir / f"source{SOURCE_SUFFIX[lease.file_type]}"
            _copy_source(storage, lease, source_path)
        except (OSError, ValueError):
            _commit_outcome(
                session_factory,
                lease,
                IdpRunOutcome(
                    "FAILED",
                    error_code="SOURCE_STORAGE_ERROR",
                    error_message=PROCESSING_ERROR_MESSAGE,
                ),
            )
        else:
            out_dir = work_dir / "output"
            outcome = runner(source_path, out_dir, config=config, file_type=lease.file_type)
            _finish_version(session_factory, storage, lease, outcome, out_dir)
    finally:
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)
        with suppress(OSError):
            version_dir.rmdir()
    return True
