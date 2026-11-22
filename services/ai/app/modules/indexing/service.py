from hashlib import sha256
from pathlib import Path
from uuid import UUID

from rag.retrieval.corpus import load_retriever
from sqlalchemy import and_, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.common.errors import AppError
from app.modules.indexing.models import IndexedDocumentVersion, IndexStatus
from app.runtime.rag_runtime import RagRuntime


def validate_generation(
    artifact_root: Path, generation_id: UUID, source_id: str, source_sha256: str, *, encoder
) -> Path:
    try:
        root = artifact_root.resolve(strict=True)
        directory = root / str(generation_id)
        if directory.resolve(strict=True) != directory:
            raise ValueError("Generation directory must not be an alias")
        _, sources, _ = load_retriever(directory, encoder=encoder)
        if (
            set(sources) != {source_id}
            or sha256(sources[source_id].encode()).hexdigest() != source_sha256
        ):
            raise ValueError("Corpus source identity mismatch")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise AppError(409) from None
    return directory


def publish_generation(
    session: Session,
    *,
    artifact_root: Path,
    document_version_id: UUID,
    generation_id: UUID,
    source_id: str,
    source_sha256: str,
    expected_generation_id: UUID | None,
    encoder,
) -> None:
    """Verify the immutable artifacts, then atomically replace the active pointer."""
    path = validate_generation(
        artifact_root, generation_id, source_id, source_sha256, encoder=encoder
    )
    model = IndexedDocumentVersion
    values = {
        "status": IndexStatus.READY,
        "corpus_generation_id": generation_id,
        "artifact_path": str(path),
        "error_code": None,
    }
    same_generation = model.corpus_generation_id == generation_id
    if expected_generation_id is None:
        incoming = insert(model).values(
            document_version_id=document_version_id, source_sha256=source_sha256, **values
        )
        statement = incoming.on_conflict_do_update(
            index_elements=[model.document_version_id],
            set_={**values, "updated_at": incoming.excluded.updated_at},
            where=and_(
                model.source_sha256 == source_sha256,
                or_(model.corpus_generation_id.is_(None), same_generation),
            ),
        )
    else:
        statement = (
            update(model)
            .where(
                model.document_version_id == document_version_id,
                model.source_sha256 == source_sha256,
                or_(model.corpus_generation_id == expected_generation_id, same_generation),
            )
            .values(**values)
        )
    published = session.scalar(statement.returning(model.document_version_id))
    if published is None:
        raise AppError(409)


def record_failed_build(session: Session, document_version_id: UUID, source_sha256: str) -> None:
    # A failed build may never overwrite an available publication.
    session.execute(
        insert(IndexedDocumentVersion)
        .values(
            document_version_id=document_version_id,
            source_sha256=source_sha256,
            status=IndexStatus.FAILED,
            error_code="INDEX_BUILD_FAILED",
        )
        .on_conflict_do_nothing(index_elements=[IndexedDocumentVersion.document_version_id])
    )


def invalidate_grants(session: Session, runtime: RagRuntime, document_version_id: UUID) -> None:
    # Authorization is fetched per operation; only derived corpus objects are cached.
    index = session.get(IndexedDocumentVersion, document_version_id)
    if index is not None and index.corpus_generation_id is not None:
        runtime.evict_retriever(index.corpus_generation_id)
