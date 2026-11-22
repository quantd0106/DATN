from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from rag.indexing.rag_corpus import load_index_bundle
from rag.retrieval.retrieval import TextRetriever
from sqlalchemy import select

from app.common.db import Database
from app.common.errors import AppError
from app.integrations.document_client import ActiveGrant, GrantSnapshot
from app.modules.indexing.models import IndexedDocumentVersion, IndexStatus
from app.runtime.rag_runtime import RagRuntime


def _published_corpora(
    database: Database, runtime: RagRuntime, snapshot: GrantSnapshot
) -> Iterator[tuple[ActiveGrant, UUID, Path]]:
    versions = {grant.document_version_id for grant in snapshot.grants}
    if not versions:
        return
    model = IndexedDocumentVersion
    with database.SessionLocal() as session:
        published = {
            row.document_version_id: (row.corpus_generation_id, row.artifact_path)
            for row in session.scalars(
                select(model).where(
                    model.document_version_id.in_(versions), model.status == IndexStatus.READY
                )
            )
        }
    if not published:
        return
    root = runtime.settings.ai_index_root
    if root is None:
        raise AppError(503)
    for grant in snapshot.grants:
        if grant.document_version_id not in published:
            continue
        generation, artifact_path = published[grant.document_version_id]
        try:
            directory = root.resolve(strict=True) / str(generation)
            if directory != directory.resolve(strict=True) or directory != Path(artifact_path):
                raise ValueError("Index path does not match a published generation")
        except (OSError, ValueError):
            raise AppError(503) from None
        yield grant, generation, directory


def active_corpora(
    database: Database, runtime: RagRuntime, snapshot: GrantSnapshot
) -> Iterator[tuple[ActiveGrant, TextRetriever]]:
    for grant, generation, directory in _published_corpora(database, runtime, snapshot):
        try:
            retriever = runtime.retriever(generation, directory, encoder=runtime.encoder())
            if {child["source_id"] for child in retriever.children.values()} != {grant.source_id}:
                raise ValueError("Active grant differs from the published source")
        except (OSError, ValueError, KeyError):
            raise AppError(503) from None
        yield grant, retriever


def active_source_texts(
    database: Database, runtime: RagRuntime, snapshot: GrantSnapshot
) -> Iterator[tuple[ActiveGrant, str]]:
    """Read the exact normalized text for granted, published sources."""
    for grant, _, directory in _published_corpora(database, runtime, snapshot):
        try:
            sources = load_index_bundle(directory)["sources"]
            if set(sources) != {grant.source_id}:
                raise ValueError("Active grant differs from the published source")
        except (OSError, ValueError, KeyError, TypeError):
            raise AppError(503) from None
        yield grant, sources[grant.source_id]
