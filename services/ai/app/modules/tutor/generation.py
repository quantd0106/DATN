from itertools import islice, zip_longest
from threading import Event
from typing import Literal

from rag.generation.contracts.evidence import evidence_items
from rag.generation.policy.config import TutorConfig
from rag.generation.tasks.qa.condense import condense, route, should_condense
from rag.generation.tasks.qa.quick import answer_quick, refusal
from rag.generation.tasks.qa.render import render_answer
from rag.generation.tasks.qa.step import StepSession, check_step, start_session
from rag.search.retrieval_plan import SearchRequest

from app.common.api import Services
from app.common.db import Database
from app.common.errors import AppError
from app.integrations.document_client import GrantSnapshot
from app.modules.indexing.corpora import active_corpora
from app.modules.tutor.canonical_fallback import rank_canonical_records
from app.modules.tutor.models import TutorMode
from app.modules.tutor.streaming import TurnResult
from app.runtime.rag_runtime import RagRuntime

Operation = Literal["ASK", "CHECK", "HINT", "REVEAL"]


def _render(value: dict) -> dict:
    return render_answer(value, lambda _source, _spans: None)


def _retrieve(
    database: Database,
    runtime: RagRuntime,
    question: str,
    scope: GrantSnapshot,
    cancelled: Event,
) -> list[dict]:
    ranked = []
    indexed_sources: set[str] = set()
    for grant, retriever in active_corpora(database, runtime, scope):
        if cancelled.is_set():
            raise AppError(409)
        indexed_sources.add(grant.source_id)
        request = SearchRequest(question, scope_source_ids=frozenset({grant.source_id}))
        allowed = {
            key
            for key, child in retriever.children.items()
            if child["source_id"] == grant.source_id
        }
        context = retriever.retrieve(request, authorize=lambda ids=allowed: ids)["context"]["items"]
        ranked.append([record for record in context if record["source_id"] == grant.source_id])
    for grant in scope.grants:
        if grant.source_id in indexed_sources:
            continue
        if cancelled.is_set():
            raise AppError(409)
        cached = runtime.canonical_records(grant.document_version_id)
        if cached:
            ranked.append(rank_canonical_records(cached, question, limit=6))
    records = (record for rank in zip_longest(*ranked) for record in rank if record is not None)
    return evidence_items(islice(records, 12))


def _step_result(
    session: StepSession, event: dict, sources: set[str], citations: list[dict]
) -> TurnResult:
    message = (
        event.get("text")
        or event.get("feedback")
        or {
            "step_correct": "Em đã trả lời đúng bước này.",
            "step_incorrect": "Câu trả lời chưa đúng. Em thử xem lại gợi ý nhé.",
            "step_revealed": "Xem lại kết quả của bước này rồi thử bước tiếp theo.",
            "check_unavailable": "Chưa thể kiểm tra câu trả lời lúc này.",
        }.get(event["kind"], "Hãy tiếp tục theo gợi ý.")
    )
    hint = event.get("next_hint") or event.get("hint")
    if event["kind"] == "solution":
        message = event["final_answer"]
        citations = [cite for step in event["steps"] for cite in step["citations"]]
    elif event["kind"] == "step_revealed":
        message = f"{message}\n\n{event['expected']}"
    if isinstance(hint, dict):
        message = f"{message}\n\n{hint['text']}"
        citations = [*citations, *hint["citations"]]
    rendered = _render(
        {
            "status": "answered",
            "claims": [
                {
                    "text": message,
                    "basis": "source" if citations else "general",
                    "evidence": citations,
                }
            ],
        }
    )
    view = session.public_view()
    rendered["step"] = {key: view[key] for key in ("state", "step", "total_steps")}
    rendered["_required_sources"] = sorted(sources)
    return TurnResult(rendered, event.get("provenance", []), session)


def generate_turn(
    question: str,
    mode: TutorMode,
    session: StepSession | None,
    scope: GrantSnapshot,
    cancelled: Event,
    history: list[dict[str, str]] | None = None,
    *,
    runtime: RagRuntime,
    database: Database,
    config: TutorConfig,
    grade: int,
    operation: Operation,
) -> TurnResult:
    if operation != "ASK":
        if session is None or session.state in ("SOLVED", "REVEALED"):
            raise AppError(409)
        planned_sources = session.plan.get("_ai_sources")
        if planned_sources is None or not set(planned_sources) <= scope.source_ids:
            raise AppError(403)
        if session.config != config.to_dict():
            raise AppError(409)
        if operation == "HINT":
            event = session.next_hint()
            return _step_result(session, event, set(planned_sources), event["citations"])
        if operation == "REVEAL":
            if not config.allow_solution_reveal:
                raise AppError(403)
            return _step_result(session, session.reveal_solution(), set(planned_sources), [])
        citations = session.plan["steps"][session.step_index]["evidence"]
        event = check_step(runtime.pipeline(), session, question, wait_timeout=10)
        return _step_result(session, event, set(planned_sources), citations)

    pipeline = runtime.pipeline()
    standalone = question
    intent = "DOC_QUESTION"
    needs_retrieval = True
    context_hint = ""
    extra_provenance: list[dict] = []
    if history and should_condense(question, history):
        condensed = condense(pipeline, question, history, wait_timeout=10)
        standalone = condensed["standalone_question"]
        intent = condensed["intent"]
        needs_retrieval = bool(condensed["needs_retrieval"])
        extra_provenance = list(condensed.get("provenance", []))
        if condensed.get("condensed"):
            context_hint = standalone

    routed = route(mode.value, intent)
    if routed == "DECLINE":
        rendered = _render(refusal("out_of_scope"))
        rendered["notice"] = "Câu hỏi nằm ngoài phạm vi học tập của lớp học."
        return TurnResult(rendered, extra_provenance)
    if routed == "META":
        return TurnResult(
            {
                "status": "answered",
                "notice": None,
                "claims": [
                    {
                        "text": "Mình là Trợ lý học tập Lumi. Bạn có thể đặt câu hỏi về tài liệu của lớp hoặc chọn chế độ hướng dẫn từng bước để luyện giải bài tập.",
                        "basis": "general",
                        "citations": [],
                    }
                ],
                "missing_parts": [],
            },
            extra_provenance,
        )

    effective_mode = TutorMode.STEP_BY_STEP if routed == "STEP_BY_STEP" else TutorMode.QUICK_QA
    evidence = (
        _retrieve(database, runtime, standalone, scope, cancelled)
        if needs_retrieval or not config.allow_general_knowledge
        else []
    )
    if not evidence and not config.allow_general_knowledge:
        return TurnResult(_render(refusal("no_evidence")), extra_provenance)
    if cancelled.is_set():
        raise AppError(409)
    if effective_mode == TutorMode.QUICK_QA:
        result = answer_quick(
            pipeline,
            question=standalone,
            evidence=evidence,
            config=config,
            grade=grade,
            context_hint=context_hint,
            wait_timeout=10,
        )
        return TurnResult(_render(result), [*extra_provenance, *result["provenance"]])
    result = start_session(
        pipeline,
        problem=standalone[:600],
        evidence=evidence,
        config=config,
        grade=grade,
        wait_timeout=10,
    )
    planned = result["session"]
    if planned is None:
        return TurnResult(
            _render(refusal("no_evidence")), [*extra_provenance, *result["provenance"]]
        )
    sources = {item["record"]["source_id"] for item in evidence}
    planned.plan["_ai_sources"] = sorted(sources)
    hint = {**result["first_hint"], "provenance": [*extra_provenance, *result["provenance"]]}
    return _step_result(planned, hint, sources, hint["citations"])


async def locate_citations(services: Services, result: TurnResult, snapshot: GrantSnapshot) -> None:
    grants = {grant.source_id: grant for grant in snapshot.grants}
    locations = {}
    for claim in result.rendered["claims"]:
        for cite in claim["citations"]:
            source_id = cite["source_id"]
            grant = grants.get(source_id)
            if grant is None:
                raise AppError(403)
            spans = tuple((span["start"], span["end"]) for span in cite["source_spans"])
            key = (source_id, spans)
            if key not in locations:
                try:
                    locations[key] = await services.document.locate(
                        snapshot.class_id,
                        snapshot.school_id,
                        grant,
                        spans,
                        services.correlation_id,
                    )
                except AppError as exc:
                    if exc.status_code != 503:
                        raise
                    locations[key] = None
            location = locations[key]
            if location is not None:
                cite["location"] = location.model_dump(exclude_none=True)
            else:
                page = next(
                    (
                        span.get("page")
                        for span in cite["source_spans"]
                        if isinstance(span.get("page"), int)
                    ),
                    None,
                )
                doc_name = (
                    services.document.document_name(grant.document_version_id)
                    if hasattr(services.document, "document_name")
                    else None
                )
                if page is not None and doc_name:
                    cite["location"] = {"document_name": doc_name, "page": page}
                elif doc_name:
                    cite["location"] = {"document_name": doc_name}
                else:
                    cite["location"] = None
