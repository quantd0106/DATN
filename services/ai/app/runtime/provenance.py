import math
import re
from collections.abc import Iterable, Mapping

from rag.generation.registry import catalog
from sqlalchemy.orm import Session

from app.runtime.models import GenerationProvenance

_HASH = re.compile(r"[a-f0-9]{64}")
_TASKS = {(entry["name"], entry["version"]) for entry in catalog()} | {
    ("assignment.grade_hint", "assignment-grade-hint-1")
}
_CODES = frozenset(
    {
        "all_source_claims_dropped",
        "answered_with_missing_parts",
        "answer_in_stem",
        "answer_without_source_claim",
        "banned_option_phrase",
        "computation_mismatch",
        "computation_not_evaluable",
        "correct_echoes_stem",
        "correct_option_longest",
        "distractor_entailed",
        "downgraded_to_partial",
        "false_statement_not_contradicted",
        "feedback_leaks_answer",
        "general_knowledge_disabled",
        "generation_not_complete",
        "grade_hint_contains_score",
        "hint_leaks_step_result",
        "invalid_history",
        "invalid_json",
        "invalid_verdict",
        "judge_unavailable",
        "key_not_entailed",
        "key_number_not_in_source",
        "latex_unbalanced",
        "option_length_imbalance",
        "refusal_with_source_claim",
        "schema_violation",
        "short_answer_not_numeric",
        "source_quality_warning",
        "step_without_evidence",
        "tolerance_too_wide",
        "too_many_general_claims",
        "true_false_balance",
        "true_statement_not_entailed",
        "true_statement_number_not_in_source",
        "unknown_item_type",
    }
)


def _digest(value: object) -> str | None:
    return value if isinstance(value, str) and _HASH.fullmatch(value) else None


def _counter(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2_147_483_647 else None


def _milliseconds(value: object) -> int | None:
    if type(value) in (float, int) and 0 <= value <= 2_147_483 and math.isfinite(value):
        return round(value * 1000)
    return None


def _safe_code(value: object) -> str:
    return value if isinstance(value, str) and value in _CODES else "unclassified"


def save_provenance(session: Session, records: Iterable[Mapping]) -> None:
    for record in records:
        task, version = record.get("task"), record.get("task_version")
        prompt, evidence = (
            _digest(record.get("prompt_sha256")),
            _digest(record.get("evidence_sha256")),
        )
        attempt = _counter(record.get("attempt"))
        if (
            not isinstance(task, str)
            or not isinstance(version, str)
            or (task, version) not in _TASKS
            or prompt is None
            or evidence is None
            or not attempt
        ):
            continue
        sampling = record.get("sampling")
        error = record.get("error_code")
        flags = record.get("flags")
        session.add(
            GenerationProvenance(
                task_name=task,
                task_version=version,
                prompt_sha256=prompt,
                evidence_sha256=evidence,
                raw_response_sha256=_digest(record.get("raw_response_sha256")),
                model_sha256=_digest(record.get("model_sha256")),
                seed=_counter(sampling.get("seed")) if isinstance(sampling, Mapping) else None,
                attempt=attempt,
                outcome="ok" if record.get("outcome") == "ok" else "rejected",
                error_code=_safe_code(error) if error is not None else None,
                flags_json=sorted({_safe_code(flag) for flag in flags})
                if isinstance(flags, (list, tuple))
                else [],
                prompt_tokens=_counter(record.get("prompt_tokens")),
                completion_tokens=_counter(record.get("completion_tokens")),
                waited_ms=_milliseconds(record.get("queue_wait_seconds")),
                latency_ms=_milliseconds(record.get("seconds")),
            )
        )
