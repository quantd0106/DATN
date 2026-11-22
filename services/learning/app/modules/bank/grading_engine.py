"""Deterministic grading, answer-key stripping, AI/Web type normalization, and source masking.

Pure functions: no DB or network calls. No eval() is ever used for numeric evaluation.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.common.errors import AppError

if TYPE_CHECKING:
    from app.integrations.document_client import GrantSnapshot

DEFAULT_TRUE_FALSE_SCALE = {0: 0.0, 1: 0.1, 2: 0.25, 3: 0.5, 4: 1.0}
MAX_SHORT_ANSWER_EQUIVALENTS = 5


def canonical_content_sha256(questions: list[dict[str, Any]], settings: dict[str, Any]) -> str:
    payload = json.dumps(
        {"questions": questions, "settings": settings},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_question_type(raw_type: str) -> str:
    if raw_type == "true_false_4":
        return "true_false"
    if raw_type in {"single_choice", "multiple_choice", "true_false", "short_answer"}:
        return raw_type
    raise AppError(422)


def _norm_text(value: str) -> str:
    cleaned = unicodedata.normalize("NFC", value).strip().lower()
    return re.sub(r"\s+", " ", cleaned)


def _safe_eval_number(raw: str) -> float | None:
    """Safely evaluate a plain number or simple arithmetic fraction (e.g. '3/4', '-2.5')."""
    text = raw.strip().replace(",", ".")
    if not text or len(text) > 64:
        return None
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None

    def _eval(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(
            n.value, bool
        ):
            val = float(n.value)
            if not math.isfinite(val):
                raise ValueError("non-finite")
            return val
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = _eval(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = _eval(n.left), _eval(n.right)
            if isinstance(n.op, ast.Add):
                return left + right
            if isinstance(n.op, ast.Sub):
                return left - right
            if isinstance(n.op, ast.Mult):
                return left * right
            if right == 0:
                raise ValueError("div0")
            return left / right
        raise ValueError("unsupported")

    try:
        result = _eval(node)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def normalize_ai_item_to_bank(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert AI export_payload item (services/rag/generation/tasks/quiz) to Learning bank format."""
    item = payload.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("type"), str):
        raise AppError(422)
    qtype = normalize_question_type(item["type"])
    prompt = str(item.get("stem") or item.get("context") or item.get("prompt") or "").strip()
    if not prompt:
        raise AppError(422)
    explanation = str(item.get("explanation") or "").strip()
    rubric = str(item.get("rubric") or "").strip()

    body_json: dict[str, Any] = {}
    key_json: dict[str, Any] = {}

    if qtype == "single_choice":
        correct = str(item.get("correct_answer") or "").strip()
        distractors = item.get("distractors") or []
        if not correct or not isinstance(distractors, list) or not distractors:
            raise AppError(422)
        texts = [correct] + [
            str(d.get("text") if isinstance(d, dict) else d).strip() for d in distractors
        ]
        options = [
            {"id": f"opt-{i + 1}", "label": chr(ord("A") + i), "text": t}
            for i, t in enumerate(texts)
            if t
        ]
        if len(options) < 2:
            raise AppError(422)
        body_json = {"options": options}
        key_json = {
            "correct_option_id": options[0]["id"],
            "distractor_misconceptions": {
                options[i + 1]["id"]: str(d.get("misconception") or "")
                for i, d in enumerate(distractors)
                if isinstance(d, dict) and i + 1 < len(options)
            },
        }
    elif qtype == "multiple_choice":
        corrects = item.get("correct_answers") or []
        distractors = item.get("distractors") or []
        if not isinstance(corrects, list) or len(corrects) < 2 or not isinstance(distractors, list):
            raise AppError(422)
        texts = [str(c).strip() for c in corrects] + [
            str(d.get("text") if isinstance(d, dict) else d).strip() for d in distractors
        ]
        options = [
            {"id": f"opt-{i + 1}", "label": chr(ord("A") + i), "text": t}
            for i, t in enumerate(texts)
            if t
        ]
        body_json = {"options": options}
        key_json = {"correct_option_ids": [opt["id"] for opt in options[: len(corrects)]]}
    elif qtype == "true_false":
        statements = item.get("statements") or []
        if not isinstance(statements, list) or not (1 <= len(statements) <= 4):
            raise AppError(422)
        normalized_stmts = []
        truth_map: dict[str, bool] = {}
        for idx, stmt in enumerate(statements):
            if not isinstance(stmt, dict) or not isinstance(stmt.get("truth"), bool):
                raise AppError(422)
            sid = f"stmt-{idx + 1}"
            normalized_stmts.append(
                {"id": sid, "label": chr(ord("a") + idx), "text": str(stmt.get("text") or "").strip()}
            )
            truth_map[sid] = stmt["truth"]
        body_json = {"statements": normalized_stmts}
        key_json = {"statement_truths": truth_map}
    else:  # short_answer
        answer = item.get("answer")
        if isinstance(answer, dict):
            kind = answer.get("kind", "text")
            canonical = str(answer.get("value") or answer.get("canonical") or "").strip()
            accepted = [str(v).strip() for v in (answer.get("accepted") or []) if str(v).strip()]
            tolerance = answer.get("tolerance")
            body_json = {"manual_grading_required": False}
            key_json = {
                "kind": kind,
                "expected_answer": canonical,
                "accepted_answers": accepted[:MAX_SHORT_ANSWER_EQUIVALENTS],
                "tolerance": float(tolerance) if isinstance(tolerance, (int, float)) else 1e-6,
            }
        else:
            canonical = str(item.get("expected_answer") or "").strip()
            body_json = {"manual_grading_required": bool(item.get("manual_grading_required", False))}
            key_json = {"kind": "text", "expected_answer": canonical, "accepted_answers": []}

    sources = payload.get("sources") or []
    source_ref = None
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        first = sources[0]
        source_ref = {
            "source_id": first.get("source_id"),
            "source_spans": first.get("source_spans", []),
            "document_id": first.get("document_id"),
            "document_version_id": first.get("document_version_id"),
            "locator": first.get("locator") or "",
        }

    return {
        "type": qtype,
        "prompt": prompt,
        "body_json": body_json,
        "key_json": key_json,
        "explanation": explanation,
        "rubric": rubric,
        "source_ref_json": source_ref,
    }


def strip_answer_keys(question: dict[str, Any]) -> dict[str, Any]:
    """Produce a student-safe question snapshot without answer keys, solutions, or rubrics."""
    qtype = normalize_question_type(str(question["type"]))
    base: dict[str, Any] = {
        "id": str(question["id"]),
        "type": qtype,
        "prompt": str(question["prompt"]),
        "points": float(question.get("points", 1.0)),
    }
    if question.get("topic"):
        base["topic"] = str(question["topic"])
    if question.get("sourceRef"):
        base["sourceRef"] = question["sourceRef"]
    if question.get("source"):
        base["source"] = question["source"]

    if qtype in {"single_choice", "multiple_choice"}:
        base["options"] = [
            {
                "id": str(opt["id"]),
                "label": str(opt.get("label") or chr(ord("A") + idx)),
                "text": str(opt["text"]),
            }
            for idx, opt in enumerate(question.get("options") or [])
        ]
    elif qtype == "true_false":
        base["statements"] = [
            {
                "id": str(stmt["id"]),
                "label": str(stmt.get("label") or chr(ord("a") + idx)),
                "text": str(stmt["text"]),
            }
            for idx, stmt in enumerate(question.get("statements") or [])
        ]
    return base


def mask_source_ref(
    source_ref: dict[str, Any] | None, allowed_source_ids: frozenset[str] | None, allowed_doc_ids: frozenset[str] | None
) -> dict[str, Any] | None:
    """Check current Document grants; mask revoked source while preserving question and grade (H4/B7)."""
    if not source_ref:
        return None
    source_id = source_ref.get("source_id")
    doc_id = str(source_ref.get("documentId") or source_ref.get("document_id") or "")
    accessible = False
    if allowed_source_ids is not None and source_id:
        accessible = str(source_id) in allowed_source_ids
    elif allowed_doc_ids is not None and doc_id:
        accessible = doc_id in allowed_doc_ids
    if accessible:
        return {**source_ref, "isAccessible": True, "accessible": True}
    version = source_ref.get("versionNumber") or source_ref.get("version") or 1
    if not str(version).isdigit() or len(str(version)) > 9:
        version = 1
    return {
        "documentId": doc_id,
        "documentTitle": "Tài liệu không còn khả dụng",
        "title": "Tài liệu không còn khả dụng",
        "versionNumber": int(version),
        "version": int(version),
        "locator": "",
        "displayText": "Nguồn tài liệu đã bị thu hồi quyền truy cập",
        "isAccessible": False,
        "accessible": False,
    }


def mask_current_source(source: dict | None, grants: GrantSnapshot | None) -> dict | None:
    if not source:
        return None
    fields = {
        "id", "document_id", "documentId", "document_version_id", "documentVersionId",
        "source_id", "sourceId", "source_spans", "documentTitle", "title", "version_number",
        "version", "versionNumber", "locator", "displayText",
    }
    reference = {key: value for key, value in source.items() if key in fields}
    document_id = reference.get("documentId") or reference.get("document_id") or reference.get("id")
    source_id = reference.get("source_id") or reference.get("sourceId")
    version_id = reference.get("document_version_id") or reference.get("documentVersionId")
    version = reference.get("version_number") or reference.get("versionNumber") or reference.get("version")
    if document_id:
        reference["documentId"] = str(document_id)
    if source_id:
        reference["source_id"] = str(source_id)
    matching = [
        grant
        for grant in (grants.grants if grants else ())
        if (not document_id or str(grant.document_id) == str(document_id))
        and (not source_id or grant.source_id == source_id)
        and (not version_id or str(grant.document_version_id) == str(version_id))
        and (not version or str(grant.version_number) == str(version))
    ]
    return mask_source_ref(
        reference, frozenset(grant.source_id for grant in matching),
        frozenset(str(grant.document_id) for grant in matching),
    )


@dataclass(frozen=True)
class QuestionGradeOutcome:
    score: float | None  # None indicates manual grading required in official Assessment
    max_score: float
    is_correct: bool
    requires_manual_grading: bool
    correct_answer_text: str
    student_answer_text: str


def grade_question(
    question: dict[str, Any],
    answer: dict[str, Any] | None,
    *,
    scoring_rules: dict[str, Any] | None = None,
    official: bool = False,
) -> QuestionGradeOutcome:
    """Grade one question across Practice and Official Assessment."""
    qtype = normalize_question_type(str(question["type"]))
    max_points = float(question.get("points", 1.0))
    rules = scoring_rules or {}

    if qtype == "single_choice":
        correct_id = str(
            question.get("correctOptionId")
            or (question.get("key")[0] if isinstance(question.get("key"), list) and question.get("key") else question.get("key") or "")
        )
        chosen = ""
        if isinstance(answer, dict):
            chosen = str(answer.get("singleChoiceValue") or answer.get("value") or "")
        options_map = {str(o["id"]): str(o.get("text", o["id"])) for o in (question.get("options") or [])}
        correct = bool(correct_id and chosen == correct_id)
        return QuestionGradeOutcome(
            score=max_points if correct else 0.0,
            max_score=max_points,
            is_correct=correct,
            requires_manual_grading=False,
            correct_answer_text=options_map.get(correct_id, correct_id),
            student_answer_text=options_map.get(chosen, chosen) if chosen else "",
        )

    if qtype == "multiple_choice":
        raw_correct = question.get("correctOptionIds") or question.get("key") or []
        correct_set = {str(x) for x in raw_correct} if isinstance(raw_correct, list) else set()
        raw_chosen = (
            answer.get("multipleChoiceValues")
            if isinstance(answer, dict) and answer.get("multipleChoiceValues") is not None
            else (answer.get("value") if isinstance(answer, dict) else [])
        )
        chosen_set = {str(x) for x in raw_chosen} if isinstance(raw_chosen, list) else set()
        options_map = {str(o["id"]): str(o.get("text", o["id"])) for o in (question.get("options") or [])}
        exact = bool(correct_set and chosen_set == correct_set)
        if exact:
            score = max_points
        elif rules.get("multiple_choice_partial") and correct_set:
            hits = len(chosen_set & correct_set)
            wrong = len(chosen_set - correct_set)
            score = round(max(0.0, (hits - wrong) / len(correct_set)) * max_points, 2)
        else:
            score = 0.0
        return QuestionGradeOutcome(
            score=score,
            max_score=max_points,
            is_correct=exact,
            requires_manual_grading=False,
            correct_answer_text=", ".join(options_map.get(x, x) for x in sorted(correct_set)),
            student_answer_text=", ".join(options_map.get(x, x) for x in sorted(chosen_set)),
        )

    if qtype == "true_false":
        statements = question.get("statements") or []
        key_map: dict[str, bool] = {}
        if isinstance(question.get("key"), dict):
            key_map = {str(k): bool(v) for k, v in question["key"].items()}
        else:
            for stmt in statements:
                if "isCorrect" in stmt and stmt["isCorrect"] is not None:
                    key_map[str(stmt["id"])] = bool(stmt["isCorrect"])
                elif "truth" in stmt and stmt["truth"] is not None:
                    key_map[str(stmt["id"])] = bool(stmt["truth"])
        raw_val = (
            answer.get("trueFalseValues")
            if isinstance(answer, dict) and answer.get("trueFalseValues") is not None
            else (answer.get("value") if isinstance(answer, dict) else {})
        )
        chosen_map = raw_val if isinstance(raw_val, dict) else {}
        total_stmts = len(key_map)
        matched = sum(
            1
            for sid, expected in key_map.items()
            if isinstance(chosen_map.get(sid), bool) and chosen_map[sid] is expected
        )
        all_correct = total_stmts > 0 and matched == total_stmts
        if total_stmts == 4 and rules.get("true_false_mode", "tiered_2025") == "tiered_2025":
            scale = rules.get("true_false_scale") or DEFAULT_TRUE_FALSE_SCALE
            ratio = float(scale.get(matched, scale.get(str(matched), 0.0)))
            score = round(max_points * ratio, 2)
        elif total_stmts > 0:
            score = round(max_points * (matched / total_stmts), 2)
        else:
            score = 0.0
        correct_text = "; ".join(
            f"{sid}: {'Đúng' if v else 'Sai'}" for sid, v in key_map.items()
        )
        student_text = "; ".join(
            f"{sid}: {'Đúng' if v else 'Sai'}"
            for sid, v in chosen_map.items()
            if isinstance(v, bool)
        )
        return QuestionGradeOutcome(
            score=score,
            max_score=max_points,
            is_correct=all_correct,
            requires_manual_grading=False,
            correct_answer_text=correct_text,
            student_answer_text=student_text,
        )

    # short_answer
    expected = str(question.get("expectedAnswer") or question.get("key") or "").strip()
    accepted = [
        str(v).strip()
        for v in (question.get("acceptedAnswers") or [])
        if str(v).strip()
    ][:MAX_SHORT_ANSWER_EQUIVALENTS]
    manual_flag = bool(question.get("manualGradingRequired", False))
    chosen_text = ""
    if isinstance(answer, dict):
        chosen_text = str(answer.get("shortAnswerValue") or answer.get("value") or "").strip()

    if official and manual_flag:
        return QuestionGradeOutcome(
            score=None,
            max_score=max_points,
            is_correct=False,
            requires_manual_grading=True,
            correct_answer_text=expected,
            student_answer_text=chosen_text,
        )

    candidates = [c for c in [expected, *accepted] if c]
    tol = float(question.get("tolerance", rules.get("numeric_tolerance", 1e-6)))
    matched = False
    if chosen_text and candidates:
        chosen_num = _safe_eval_number(chosen_text)
        for cand in candidates:
            cand_num = _safe_eval_number(cand)
            if chosen_num is not None and cand_num is not None:
                if abs(chosen_num - cand_num) <= max(tol, abs(cand_num) * 1e-6):
                    matched = True
                    break
            elif _norm_text(chosen_text) == _norm_text(cand):
                matched = True
                break

    if official and not matched and not expected:
        return QuestionGradeOutcome(
            score=None,
            max_score=max_points,
            is_correct=False,
            requires_manual_grading=True,
            correct_answer_text=expected,
            student_answer_text=chosen_text,
        )

    return QuestionGradeOutcome(
        score=max_points if matched else 0.0,
        max_score=max_points,
        is_correct=matched,
        requires_manual_grading=False,
        correct_answer_text=expected,
        student_answer_text=chosen_text,
    )
