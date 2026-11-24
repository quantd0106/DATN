"""On-demand canonical document retrieval fallback for unindexed Document grants."""

from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from sqlalchemy import select

from app.common.db import Database
from app.integrations.document_client import ActiveGrant, GrantSnapshot
from app.modules.indexing.models import IndexedDocumentVersion, IndexStatus
from app.runtime.rag_runtime import RagRuntime

_PAGE_QUERY_RE = re.compile(r"(?:trang|page|tr\.?)\s*(\d+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_CHUNK_CHARS = 1000


def _fold_vi(value: str) -> str:
    nfd = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D").lower()


def _page_number(page: dict, fallback: int) -> int:
    number = page.get("page_number")
    if not isinstance(number, int) or number < 1:
        number = page.get("page_id")
    return number if isinstance(number, int) and number >= 1 else fallback


def _block_latex_text(raw: object) -> str:
    if isinstance(raw, list):
        return "\n".join(str(item).strip() for item in raw if str(item).strip())
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _extract_pages(artifact: dict) -> list[tuple[int, str, str]]:
    """Return list of (page_number, title, text) from Document canonical JSON."""
    extracted: list[tuple[int, str, str]] = []
    reader_pages = artifact.get("reader_pages")
    if isinstance(reader_pages, list):
        for idx, page in enumerate(reader_pages, 1):
            if not isinstance(page, dict):
                continue
            num = _page_number(page, idx)
            title = str(page.get("title") or f"Trang {num}")
            parts = [str(p).strip() for p in (page.get("paragraphs") or []) if str(p).strip()]
            parts.extend(str(f).strip() for f in (page.get("formulas") or []) if str(f).strip())
            text = "\n".join(parts).strip()
            if text:
                extracted.append((num, title, text))
        return extracted

    pages = artifact.get("pages")
    if isinstance(pages, list):
        for idx, page in enumerate(pages, 1):
            if not isinstance(page, dict):
                continue
            num = _page_number(page, idx)
            title = str(page.get("title") or f"Trang {num}")
            parts: list[str] = []
            for block in page.get("blocks") or []:
                if not isinstance(block, dict) or block.get("excluded"):
                    continue
                part = (block.get("text") or block.get("markdown") or "").strip()
                if not part:
                    part = _block_latex_text(block.get("latex"))
                if part:
                    parts.append(part)
            text = "\n".join(parts).strip()
            if text:
                extracted.append((num, title, text))
    return extracted


def _split_chunks(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[tuple[int, str]]:
    if len(text) <= max_chars:
        return [(0, text)]
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = text.rfind("\n", start, end)
            if split_at <= start + max_chars // 3:
                split_at = text.rfind(" ", start, end)
            if split_at > start + max_chars // 3:
                end = split_at
        piece = text[start:end].strip()
        if piece:
            chunks.append((start, piece))
        start = max(end, start + 1)
    return chunks


def parse_canonical_records(
    grant: ActiveGrant, artifact: dict, *, document_name: str | None = None
) -> list[dict]:
    """Convert Document canonical JSON into evidence_items-compatible records."""
    doc_name = document_name or "Tài liệu lớp học"
    records: list[dict] = []
    for page_num, page_title, text in _extract_pages(artifact):
        for offset, chunk in _split_chunks(text):
            records.append(
                {
                    "source_id": grant.source_id,
                    "document_name": doc_name,
                    "page": page_num,
                    "section_path": [doc_name, page_title],
                    "text": chunk,
                    "source_spans": [
                        {
                            "start": 0,
                            "end": len(chunk),
                            "source_start": offset,
                            "source_end": offset + len(chunk),
                            "page": page_num,
                        }
                    ],
                }
            )
    return records


def rank_canonical_records(records: list[dict], question: str, limit: int = 4) -> list[dict]:
    """Rank canonical chunks by explicit page match and lexical overlap."""
    if not records:
        return []
    requested_pages = {int(m) for m in _PAGE_QUERY_RE.findall(question)}
    q_tokens = {tok for tok in _TOKEN_RE.findall(question.lower()) if len(tok) >= 2}
    q_folded = {tok for tok in _TOKEN_RE.findall(_fold_vi(question)) if len(tok) >= 2}
    scored: list[tuple[int, int, dict]] = []
    for idx, record in enumerate(records):
        score = 0
        if record.get("page") in requested_pages:
            score += 10_000
        text_lower = record["text"].lower()
        if q_tokens:
            score += sum(10 for tok in q_tokens if tok in text_lower)
        if q_folded:
            text_folded = _fold_vi(record["text"])
            score += sum(4 for tok in q_folded if tok in text_folded)
        scored.append((score, -idx, record))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[:limit]]


async def ensure_canonical_cache(
    database: Database,
    runtime: RagRuntime,
    document: object,
    snapshot: GrantSnapshot,
    correlation_id: str,
) -> None:
    """Fetch canonical JSON from Document service for active grants lacking a local index."""
    if not snapshot.grants or not hasattr(document, "canonical_artifact"):
        return
    missing_ids: list[UUID] = [
        grant.document_version_id
        for grant in snapshot.grants
        if not runtime.has_canonical_records(grant.document_version_id)
    ]
    if not missing_ids:
        return
    with database.SessionLocal() as session:
        indexed_versions = set(
            session.scalars(
                select(IndexedDocumentVersion.document_version_id).where(
                    IndexedDocumentVersion.document_version_id.in_(missing_ids),
                    IndexedDocumentVersion.status == IndexStatus.READY,
                )
            )
        )
    for grant in snapshot.grants:
        if grant.document_version_id in indexed_versions:
            continue
        if runtime.has_canonical_records(grant.document_version_id):
            continue
        artifact = await document.canonical_artifact(grant, correlation_id)
        if isinstance(artifact, dict):
            doc_name = (
                document.document_name(grant.document_version_id)
                if hasattr(document, "document_name")
                else None
            )
            records = parse_canonical_records(grant, artifact, document_name=doc_name)
            if records:
                runtime.store_canonical_records(grant.document_version_id, records)
