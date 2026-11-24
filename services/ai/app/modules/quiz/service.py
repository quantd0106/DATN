import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from rag.generation.contracts.errors import GenerationUnavailable
from rag.generation.runtime.scheduler import P1_TEACHER, SchedulerBusy
from rag.generation.tasks.quiz import job as rag_job
from rag.generation.tasks.quiz import review
from rag.generation.tasks.quiz.blueprint import (
    MAX_ITEMS_PER_JOB,
    MatrixRow,
    QuizSpec,
    in_scope,
    parse_quiz_spec,
    propose_matrix,
)
from rag.generation.tasks.quiz.facts import extract_facts
from rag.generation.tasks.quiz.job import run_job
from rag.generation.tasks.quiz.knowledge_units import KnowledgeUnit, build_knowledge_units
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.common.class_access import require_active_teacher
from app.common.db import Database
from app.common.errors import AppError
from app.integrations.core_client import CoreClient, CurrentUser
from app.integrations.document_client import DocumentPort, GrantSnapshot
from app.integrations.learning_client import LearningPort, QuizExportReceipt
from app.modules.indexing.corpora import active_corpora
from app.modules.quiz.models import DraftStatus, JobStatus, QuizItemDraft, QuizJob
from app.modules.tutor.canonical_fallback import ensure_canonical_cache
from app.runtime.provenance import save_provenance
from app.runtime.rag_runtime import RagRuntime

logger = logging.getLogger(__name__)


def _scoped_grants(scope: GrantSnapshot, spec: QuizSpec) -> GrantSnapshot:
    versions = {entry["document_version_id"] for entry in spec.scope}
    allowed = {str(grant.document_version_id) for grant in scope.grants}
    if not versions <= allowed:
        raise AppError(403)
    return scope.model_copy(
        update={
            "grants": tuple(
                grant for grant in scope.grants if str(grant.document_version_id) in versions
            )
        }
    )


def load_quiz_units(
    database: Database,
    runtime: RagRuntime,
    scope: GrantSnapshot,
    spec: QuizSpec,
) -> list[KnowledgeUnit]:
    scoped = _scoped_grants(scope, spec)
    count_tokens = runtime.pipeline().generator.counter.count
    units: list[KnowledgeUnit] = []
    indexed_versions: set[UUID] = set()
    for grant, retriever in active_corpora(database, runtime, scoped):
        indexed_versions.add(grant.document_version_id)
        records = [
            {
                **child,
                "section_path": child["section_path"] or ["Nội dung tài liệu"],
                "source_quality_warnings": child.get("quality_warnings", []),
            }
            for child in retriever.children.values()
            if child["retrieval_eligible"]
        ]
        units.extend(
            build_knowledge_units(
                records,
                count_tokens=count_tokens,
                document_version_of=lambda _record, version=str(grant.document_version_id): version,
                is_exercise=lambda record: record.get("unit_kind") in {"exercise", "activity"},
            )
        )
    for grant in scoped.grants:
        if grant.document_version_id in indexed_versions:
            continue
        cached = runtime.canonical_records(grant.document_version_id)
        if not cached:
            continue
        records = [
            {
                **record,
                "section_path": record.get("section_path") or ["Nội dung tài liệu"],
                "source_quality_warnings": record.get("source_quality_warnings", []),
            }
            for record in cached
        ]
        units.extend(
            build_knowledge_units(
                records,
                count_tokens=count_tokens,
                document_version_of=lambda _record, version=str(grant.document_version_id): version,
                is_exercise=lambda record: record.get("unit_kind") in {"exercise", "activity"},
            )
        )
    usable = [unit for unit in units if not unit.exercise and in_scope(unit, spec.scope)]
    if not usable:
        raise AppError(409)
    return usable


async def _teacher_grants(
    core: CoreClient, document: DocumentPort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> GrantSnapshot:
    access = await require_active_teacher(core, user, class_id, correlation_id)
    return await document.active_grants(class_id, access.school_id, correlation_id)


def _verify_grants_still_current(scoped: list[KnowledgeUnit], current: GrantSnapshot) -> None:
    allowed = {(str(grant.document_version_id), grant.source_id) for grant in current.grants}
    if not {(unit.document_version_id, unit.source_id) for unit in scoped} <= allowed:
        raise AppError(403)


async def propose_quiz(
    runtime: RagRuntime,
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    spec: QuizSpec,
    total: int,
    correlation_id: str,
) -> list[MatrixRow]:
    if (
        str(class_id) != spec.class_id
        or type(total) is not int
        or not 1 <= total <= MAX_ITEMS_PER_JOB
    ):
        raise AppError(422)
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)
    await ensure_canonical_cache(database, runtime, document, _scoped_grants(scope, spec), correlation_id)
    version_ids = [entry["document_version_id"] for entry in spec.scope]
    initial_cache = runtime.fact_cache_for(version_ids)

    def generate_proposal():
        scoped = load_quiz_units(database, runtime, scope, spec)
        pipeline = runtime.pipeline()
        fact_cache = dict(initial_cache)
        results: dict[str, dict] = {}
        new_by_version: dict[str, dict[str, list[dict]]] = {}
        for unit in scoped:
            res = extract_facts(
                pipeline,
                unit,
                grade=spec.grade,
                subject=spec.subject,
                cache=fact_cache,
                priority=P1_TEACHER,
            )
            results[unit.id] = res
        for unit in scoped:
            from rag.generation.tasks.quiz.facts import cache_key

            ckey = cache_key(unit)
            if ckey in fact_cache:
                new_by_version.setdefault(unit.document_version_id, {})[ckey] = fact_cache[ckey]
        for vid, entries in new_by_version.items():
            runtime.store_fact_cache(vid, entries)
        with database.SessionLocal.begin() as session:
            for result in results.values():
                save_provenance(session, result["provenance"])
        return scoped, propose_matrix(
            scoped, {unit_id: result["facts"] for unit_id, result in results.items()}, total=total
        )

    try:
        scoped, proposal = await runtime.executor.run(generate_proposal)
    except (GenerationUnavailable, SchedulerBusy):
        raise AppError(503) from None
    current = await _teacher_grants(core, document, user, class_id, correlation_id)
    _verify_grants_still_current(scoped, current)
    return proposal


async def enqueue_job(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    spec_json: dict,
    correlation_id: str,
    *,
    fact_cache: dict[str, list[dict]] | None = None,
) -> UUID:
    try:
        spec = parse_quiz_spec(spec_json)
    except ValueError:
        raise AppError(422) from None
    if str(class_id) != spec.class_id or not spec.matrix:
        raise AppError(422)
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)
    if not {entry["document_version_id"] for entry in spec.scope} <= {
        str(grant.document_version_id) for grant in scope.grants
    }:
        raise AppError(403)

    def persist() -> UUID:
        with database.SessionLocal.begin() as session:
            job = QuizJob(
                school_id=scope.school_id,
                class_id=class_id,
                requested_by=user.id,
                spec_json=spec_json,
                fact_cache=dict(fact_cache or {}),
                total_items=sum(row.count for row in spec.matrix),
            )
            session.add(job)
            session.flush()
            return job.id

    return await run_in_threadpool(persist)


async def run_queued_job(
    runtime: RagRuntime,
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    job_id: UUID,
    correlation_id: str,
) -> None:
    """Worker entry point. Only one invocation may claim a queued row."""
    key = (
        QuizJob.id == job_id,
        QuizJob.school_id == user.school_id,
        QuizJob.class_id == class_id,
        QuizJob.requested_by == user.id,
    )

    def claim():
        with database.SessionLocal.begin() as session:
            claimed = session.scalar(
                update(QuizJob)
                .where(*key, QuizJob.status == JobStatus.QUEUED)
                .values(status=JobStatus.RUNNING, updated_at=datetime.now(UTC))
                .returning(QuizJob)
            )
            if claimed is None:
                return None, {}
            existing_rows = session.scalars(
                select(QuizItemDraft).where(QuizItemDraft.quiz_job_id == job_id)
            ).all()
            completed = {
                row.slot_id: {
                    "id": str(row.id),
                    "job_id": str(job_id),
                    "slot_id": row.slot_id,
                    "status": row.status.value,
                    "level": row.level,
                    "item": row.item_json,
                    "seed": row.seed,
                    **(row.slot_meta_json or {}),
                    "generation_records": [],
                }
                for row in existing_rows
            }
            return claimed, completed

    job, completed = await run_in_threadpool(claim)
    if job is None:
        raise AppError(409)

    def progress(event: dict) -> None:
        with database.SessionLocal.begin() as session:
            values = {"fact_cache": job.fact_cache, "updated_at": datetime.now(UTC)}
            if event["kind"] == "item_done":
                values["completed_items"] = event["done"]
            session.execute(
                update(QuizJob).where(*key, QuizJob.status == JobStatus.RUNNING).values(**values)
            )

    def generate(scope: GrantSnapshot, spec: QuizSpec) -> tuple[list[KnowledgeUnit], dict]:
        scoped = load_quiz_units(database, runtime, scope, spec)
        version_ids = [entry["document_version_id"] for entry in spec.scope]
        merged_cache = {**runtime.fact_cache_for(version_ids), **(job.fact_cache or {})}
        job.fact_cache = merged_cache
        result = run_job(
            runtime.pipeline(),
            spec,
            scoped,
            job_id=str(job_id),
            fact_cache=job.fact_cache,
            completed=completed,
            progress=progress,
        )
        from rag.generation.tasks.quiz.facts import cache_key

        by_version: dict[str, dict[str, list[dict]]] = {}
        for unit in scoped:
            ckey = cache_key(unit)
            if ckey in job.fact_cache:
                by_version.setdefault(unit.document_version_id, {})[ckey] = job.fact_cache[ckey]
        for vid, entries in by_version.items():
            runtime.store_fact_cache(vid, entries)
        return scoped, result

    def finish(result: dict | None) -> None:
        with database.SessionLocal.begin() as session:
            row = session.scalar(select(QuizJob).where(*key).with_for_update())
            if row is None or row.status != JobStatus.RUNNING:
                if result is None:
                    return
                raise AppError(409)
            if result is None:
                row.status = JobStatus.FAILED
                row.error_code = "QUIZ_JOB_FAILED"
                return
            existing_slots = set(
                session.scalars(
                    select(QuizItemDraft.slot_id).where(QuizItemDraft.quiz_job_id == job_id)
                ).all()
            )
            drafts = result["drafts"]
            for draft in drafts:
                if draft["slot_id"] in existing_slots:
                    continue
                save_provenance(session, draft.get("generation_records", []))
                item_type = draft.get("type") or (
                    draft["item"].get("type") if isinstance(draft.get("item"), dict) else None
                )
                session.add(
                    QuizItemDraft(
                        quiz_job_id=job_id,
                        slot_id=draft["slot_id"],
                        status=DraftStatus(draft["status"]),
                        level=draft["level"],
                        item_json=draft["item"],
                        seed=draft["seed"],
                        slot_meta_json={
                            "type": item_type,
                            "ku_id": draft.get("ku_id"),
                            "fact_ids": list(draft.get("fact_ids") or []),
                            "regenerations": int(draft.get("regenerations") or 0),
                            "flags": list(draft.get("flags") or []),
                        },
                    )
                )
            row.status = JobStatus.COMPLETED
            row.completed_items = len(drafts)
            row.fact_cache = job.fact_cache

    try:
        scope = await _teacher_grants(core, document, user, class_id, correlation_id)
        spec = parse_quiz_spec(job.spec_json)
        await ensure_canonical_cache(
            database, runtime, document, _scoped_grants(scope, spec), correlation_id
        )
        scoped, result = await runtime.executor.run(generate, scope, spec)
        current = await _teacher_grants(core, document, user, class_id, correlation_id)
        _verify_grants_still_current(scoped, current)
        await run_in_threadpool(finish, result)
    except (GenerationUnavailable, SchedulerBusy):
        logger.warning(
            "quiz_job_generation_unavailable",
            extra={"job_id": str(job_id), "class_id": str(class_id)},
        )
        await run_in_threadpool(finish, None)
        raise AppError(503) from None
    except (Exception, asyncio.CancelledError):
        logger.exception(
            "quiz_job_failed",
            extra={"job_id": str(job_id), "class_id": str(class_id)},
        )
        await run_in_threadpool(finish, None)
        raise


async def list_jobs(
    database: Database,
    core: CoreClient,
    user: CurrentUser,
    class_id: UUID,
    correlation_id: str,
    *,
    limit: int = 20,
) -> list[QuizJob]:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise AppError(422)
    access = await require_active_teacher(core, user, class_id, correlation_id)

    def read() -> list[QuizJob]:
        with database.SessionLocal() as session:
            return list(
                session.scalars(
                    select(QuizJob)
                    .where(
                        QuizJob.school_id == access.school_id,
                        QuizJob.class_id == class_id,
                    )
                    .order_by(QuizJob.created_at.desc(), QuizJob.id.desc())
                    .limit(limit)
                )
            )

    return await run_in_threadpool(read)


async def get_job(
    database: Database,
    core: CoreClient,
    user: CurrentUser,
    class_id: UUID,
    job_id: UUID,
    correlation_id: str,
) -> QuizJob:
    access = await require_active_teacher(core, user, class_id, correlation_id)

    def read() -> QuizJob:
        with database.SessionLocal() as session:
            job = session.scalar(
                select(QuizJob).where(
                    QuizJob.id == job_id,
                    QuizJob.school_id == access.school_id,
                    QuizJob.class_id == class_id,
                )
            )
            if job is None:
                raise AppError(404)
            return job

    return await run_in_threadpool(read)


async def list_drafts(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    job_id: UUID,
    correlation_id: str,
) -> list[dict]:
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)

    def read() -> list[dict]:
        with database.SessionLocal() as session:
            job = session.scalar(
                select(QuizJob.id).where(
                    QuizJob.id == job_id,
                    QuizJob.school_id == scope.school_id,
                    QuizJob.class_id == class_id,
                )
            )
            if job is None:
                raise AppError(404)
            drafts = session.scalars(
                select(QuizItemDraft)
                .where(
                    QuizItemDraft.quiz_job_id == job_id,
                )
                .order_by(QuizItemDraft.slot_id)
            )
            views = []
            for draft in drafts:
                item = draft.item_json
                try:
                    _verify_item_sources(item, scope)
                except AppError:
                    item = None
                views.append(
                    {
                        "id": draft.id,
                        "slot_id": draft.slot_id,
                        "status": draft.status,
                        "level": draft.level,
                        "item": item,
                        "review_reason": draft.review_reason,
                        "learning_item_id": draft.learning_item_id,
                    }
                )
            return views

    return await run_in_threadpool(read)


def _locked_draft(session: Session, scope: GrantSnapshot, draft_id: UUID) -> QuizItemDraft:
    draft = session.scalar(
        select(QuizItemDraft)
        .join(QuizJob, QuizJob.id == QuizItemDraft.quiz_job_id)
        .where(
            QuizItemDraft.id == draft_id,
            QuizJob.school_id == scope.school_id,
            QuizJob.class_id == scope.class_id,
        )
        .with_for_update(of=QuizItemDraft)
    )
    if draft is None:
        raise AppError(404)
    return draft


def _verify_item_sources(item: dict | None, scope: GrantSnapshot) -> None:
    if item is None:
        return
    evidence = list(item.get("evidence", []))
    for statement in item.get("statements", []):
        evidence.extend(statement.get("evidence", []))
    source_ids = scope.source_ids
    if not evidence or any(cite.get("source_id") not in source_ids for cite in evidence):
        raise AppError(403)


def _preserve_original_sources(original: dict, edited: dict) -> dict:
    # The editor owns the updated wording. Citation linkage remains server-controlled.
    result = {key: value for key, value in edited.items() if key != "evidence"}
    if "statements" in original:
        result["statements"] = [
            {"text": new["text"], "truth": new["truth"], "evidence": old["evidence"]}
            for old, new in zip(original["statements"], result["statements"], strict=True)
        ]
    else:
        result["evidence"] = original["evidence"]
    return result


async def review_draft(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    draft_id: UUID,
    decision: str,
    correlation_id: str,
    *,
    reason: str | None = None,
    edited_item: dict | None = None,
) -> DraftStatus:
    if reason is not None and reason not in review.REJECT_REASONS:
        raise AppError(422)
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)

    def persist() -> DraftStatus:
        with database.SessionLocal.begin() as session:
            draft = _locked_draft(session, scope, draft_id)
            if decision in {"accept", "edit"}:
                _verify_item_sources(draft.item_json, scope)
            try:
                result = review.review(
                    {"status": draft.status, "item": draft.item_json},
                    decision,
                    reviewer_id=str(user.id),
                    reason=reason,
                    edited_item=edited_item,
                )
            except (ValueError, KeyError, TypeError):
                raise AppError(409) from None
            draft.status = DraftStatus(result["status"])
            if draft.status == DraftStatus.EDITED_ACCEPTED:
                draft.original_item_json = draft.item_json
                draft.item_json = _preserve_original_sources(
                    draft.original_item_json, result["item"]
                )
            draft.reviewer_id = user.id
            draft.review_reason = result.get("review_reason")
            return draft.status

    return await run_in_threadpool(persist)


async def export_draft(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    learning: LearningPort,
    user: CurrentUser,
    class_id: UUID,
    draft_id: UUID,
    correlation_id: str,
) -> QuizExportReceipt:
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)

    def prepare() -> dict | QuizExportReceipt:
        with database.SessionLocal.begin() as session:
            draft = _locked_draft(session, scope, draft_id)
            if draft.status == DraftStatus.EXPORTED:
                return receipt_for(draft)
            if draft.status not in {
                DraftStatus.ACCEPTED,
                DraftStatus.EDITED_ACCEPTED,
                DraftStatus.EXPORT_PENDING,
            }:
                raise AppError(409)
            _verify_item_sources(draft.item_json, scope)
            approved_status = (
                review.EDITED_ACCEPTED if draft.original_item_json else review.ACCEPTED
            )
            payload = review.export_payload(
                {
                    "id": str(draft.id),
                    "item": draft.item_json,
                    "status": approved_status,
                    "level": draft.level,
                    "seed": draft.seed,
                },
                job={"id": str(draft.quiz_job_id)},
                approved_by=str(draft.reviewer_id),
            )
            if "statements" in payload["item"]:
                payload["item"]["statements"] = [
                    {key: value for key, value in statement.items() if key != "evidence"}
                    for statement in payload["item"]["statements"]
                ]
            draft.status = DraftStatus.EXPORT_PENDING
            return payload

    def receipt_for(draft: QuizItemDraft) -> QuizExportReceipt:
        return QuizExportReceipt(
            class_id=class_id,
            school_id=scope.school_id,
            idempotency_key=draft_id,
            learning_item_id=draft.learning_item_id,
        )

    prepared = await run_in_threadpool(prepare)
    if isinstance(prepared, QuizExportReceipt):
        return prepared
    receipt = await learning.export_quiz_item(
        class_id, scope.school_id, draft_id, prepared, correlation_id
    )

    def finish() -> None:
        with database.SessionLocal.begin() as session:
            draft = _locked_draft(session, scope, draft_id)
            if draft.status == DraftStatus.EXPORTED and receipt_for(draft) == receipt:
                return
            if draft.status != DraftStatus.EXPORT_PENDING:
                raise AppError(409)
            draft.status = DraftStatus.EXPORTED
            draft.learning_item_id = receipt.learning_item_id

    await run_in_threadpool(finish)
    return receipt


def _draft_view(draft: QuizItemDraft, scope: GrantSnapshot) -> dict:
    item = draft.item_json
    try:
        _verify_item_sources(item, scope)
    except AppError:
        item = None
    return {
        "id": draft.id,
        "slot_id": draft.slot_id,
        "status": draft.status,
        "level": draft.level,
        "item": item,
        "review_reason": draft.review_reason,
        "learning_item_id": draft.learning_item_id,
    }


async def regenerate_draft(
    runtime: RagRuntime,
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    class_id: UUID,
    draft_id: UUID,
    correlation_id: str,
) -> dict:
    scope = await _teacher_grants(core, document, user, class_id, correlation_id)

    def load_target() -> dict:
        with database.SessionLocal() as session:
            draft = _locked_draft(session, scope, draft_id)
            if draft.status != DraftStatus.REGENERATE:
                raise AppError(409)
            job = session.get(QuizJob, draft.quiz_job_id)
            if job is None:
                raise AppError(404)
            return {
                "job_id": job.id,
                "spec_json": dict(job.spec_json),
                "fact_cache": dict(job.fact_cache or {}),
                "slot_id": draft.slot_id,
                "level": draft.level,
                "seed": draft.seed,
                "item_json": draft.item_json,
                "slot_meta_json": dict(draft.slot_meta_json or {}),
            }

    target = await run_in_threadpool(load_target)
    try:
        spec = parse_quiz_spec(target["spec_json"])
    except ValueError:
        raise AppError(422) from None
    await ensure_canonical_cache(
        database, runtime, document, _scoped_grants(scope, spec), correlation_id
    )
    meta = target["slot_meta_json"]
    item_type = (
        meta.get("type")
        or (target["item_json"].get("type") if isinstance(target["item_json"], dict) else None)
        or (spec.matrix[0].type if spec.matrix else "single_choice")
    )

    def run_regenerate():
        scoped = load_quiz_units(database, runtime, scope, spec)
        unit = next((u for u in scoped if u.id == meta.get("ku_id")), scoped[0])
        pipeline = runtime.pipeline()
        fact_cache = dict(target["fact_cache"])
        extracted = extract_facts(
            pipeline,
            unit,
            grade=spec.grade,
            subject=spec.subject,
            cache=fact_cache,
            priority=P1_TEACHER,
        )
        all_facts = extracted["facts"]
        wanted_ids = set(meta.get("fact_ids") or [])
        facts = [f for f in all_facts if f.get("id") in wanted_ids] or list(all_facts)
        draft_dict = {
            "id": str(draft_id),
            "job_id": str(target["job_id"]),
            "slot_id": target["slot_id"],
            "type": item_type,
            "level": target["level"],
            "ku_id": unit.id,
            "fact_ids": [f["id"] for f in facts if isinstance(f, dict) and f.get("id")],
            "seed": target["seed"] if target["seed"] is not None else 0,
            "status": review.REGENERATE,
            "regenerations": int(meta.get("regenerations") or 0),
        }
        fresh = rag_job.regenerate(pipeline, spec, draft_dict, unit, facts)
        return scoped, fresh, fact_cache, extracted.get("provenance", []), unit.id

    try:
        scoped, fresh, fact_cache, fact_provenance, unit_id = await runtime.executor.run(
            run_regenerate
        )
    except (GenerationUnavailable, SchedulerBusy):
        raise AppError(503) from None

    current = await _teacher_grants(core, document, user, class_id, correlation_id)
    _verify_grants_still_current(scoped, current)

    def persist() -> dict:
        with database.SessionLocal.begin() as session:
            draft = _locked_draft(session, current, draft_id)
            if draft.status != DraftStatus.REGENERATE:
                raise AppError(409)
            job = session.get(QuizJob, draft.quiz_job_id)
            save_provenance(session, [*fact_provenance, *fresh.get("generation_records", [])])
            draft.status = DraftStatus(fresh["status"])
            draft.item_json = fresh["item"]
            draft.original_item_json = None
            draft.seed = fresh["seed"]
            draft.reviewer_id = None
            draft.review_reason = None
            draft.slot_meta_json = {
                "type": fresh.get("type") or item_type,
                "ku_id": unit_id,
                "fact_ids": list(fresh.get("fact_ids") or []),
                "regenerations": int(fresh.get("regenerations") or 0),
                "flags": list(fresh.get("flags") or []),
            }
            if job is not None:
                job.fact_cache = fact_cache
            return _draft_view(draft, current)

    return await run_in_threadpool(persist)


def _export_payload_for_draft(draft: QuizItemDraft, job_id: UUID) -> dict:
    approved_status = review.EDITED_ACCEPTED if draft.original_item_json else review.ACCEPTED
    payload = review.export_payload(
        {
            "id": str(draft.id),
            "item": draft.item_json,
            "status": approved_status,
            "level": draft.level,
            "seed": draft.seed,
        },
        job={"id": str(job_id)},
        approved_by=str(draft.reviewer_id),
    )
    if "statements" in payload["item"]:
        payload["item"]["statements"] = [
            {key: value for key, value in statement.items() if key != "evidence"}
            for statement in payload["item"]["statements"]
        ]
    return payload


def _prepare_export_or_block(
    database: Database, scope: GrantSnapshot, draft_id: UUID
) -> tuple[str, dict | None]:
    with database.SessionLocal.begin() as session:
        draft = _locked_draft(session, scope, draft_id)
        if draft.status != DraftStatus.EXPORT_PENDING:
            return "skip", None
        try:
            _verify_item_sources(draft.item_json, scope)
        except AppError:
            draft.status = DraftStatus.BLOCKED
            draft.review_reason = "source_access_revoked"
            return "blocked", None
        return "ready", _export_payload_for_draft(draft, draft.quiz_job_id)


def _mark_draft_exported(
    database: Database, scope: GrantSnapshot, draft_id: UUID, receipt: QuizExportReceipt
) -> bool:
    with database.SessionLocal.begin() as session:
        draft = _locked_draft(session, scope, draft_id)
        if draft.status != DraftStatus.EXPORT_PENDING:
            return False
        draft.status = DraftStatus.EXPORTED
        draft.learning_item_id = receipt.learning_item_id
        return True


async def retry_pending_exports(
    database: Database,
    document: DocumentPort,
    learning: LearningPort,
    correlation_id: str,
    *,
    limit: int = 20,
) -> dict[str, int]:
    def list_pending() -> list[tuple[UUID, UUID, UUID]]:
        with database.SessionLocal() as session:
            rows = session.execute(
                select(QuizItemDraft.id, QuizJob.class_id, QuizJob.school_id)
                .join(QuizJob, QuizJob.id == QuizItemDraft.quiz_job_id)
                .where(QuizItemDraft.status == DraftStatus.EXPORT_PENDING)
                .order_by(QuizItemDraft.updated_at, QuizItemDraft.id)
                .limit(limit)
            ).all()
            return [(row[0], row[1], row[2]) for row in rows]

    pending = await run_in_threadpool(list_pending)
    grants_cache: dict[tuple[UUID, UUID], GrantSnapshot] = {}
    sent = blocked = failed = 0

    for draft_id, class_id, school_id in pending:
        try:
            scope = grants_cache.get((school_id, class_id))
            if scope is None:
                scope = await document.active_grants(class_id, school_id, correlation_id)
                grants_cache[(school_id, class_id)] = scope
        except AppError:
            failed += 1
            continue

        state, payload = await run_in_threadpool(
            _prepare_export_or_block, database, scope, draft_id
        )
        if state == "skip":
            continue
        if state == "blocked":
            blocked += 1
            continue
        try:
            receipt = await learning.export_quiz_item(
                class_id, school_id, draft_id, payload, correlation_id
            )
        except AppError:
            failed += 1
            continue

        if await run_in_threadpool(_mark_draft_exported, database, scope, draft_id, receipt):
            sent += 1

    return {"exports_sent": sent, "exports_blocked": blocked, "exports_failed": failed}
