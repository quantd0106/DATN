"""Source-bound quote candidate generation for the staged RAG profile.

It constrains quote strings to
exact substrings of each supplied record; it does not certify that a claim is
supported by its quote.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from .span_mapping import quote_spans

MIN_QUOTE_CHARS = 3
MAX_QUOTE_CHARS = 160
MAX_SNIPPETS = 256
MAX_QUOTED_CHARS = 32_768
MAX_SOURCE_CHARS = 256_000
WINDOW_OVERLAP_CHARS = 32

_SENTENCE_END = re.compile(r"[.!?;](?=\s)|\n+")
_CLAUSE_END = re.compile(r"[,:(—–](?=\s)")
_WORD = re.compile(r"\S+")


class SourceBoundSchemaError(ValueError):
    """The supplied evidence cannot be represented under this schema budget."""


def _trim_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Choose non-whitespace boundaries without modifying the source string."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _ranges(text: str, start: int, end: int, delimiter: re.Pattern[str]):
    cursor = start
    for match in delimiter.finditer(text, start, end):
        boundary = match.end()
        if cursor < boundary:
            yield cursor, boundary
        cursor = boundary
    if cursor < end:
        yield cursor, end


def _sentence_and_clause_ranges(text: str):
    for raw_start, raw_end in _ranges(text, 0, len(text), _SENTENCE_END):
        start, end = _trim_bounds(text, raw_start, raw_end)
        if start >= end:
            continue
        yield start, end
        # Keep sentence-level candidates when they fit and also offer exact
        # clause candidates. Long sentences receive full-coverage windows.
        for clause_start, clause_end in _ranges(text, start, end, _CLAUSE_END):
            clause_start, clause_end = _trim_bounds(text, clause_start, clause_end)
            if clause_start < clause_end:
                yield clause_start, clause_end


def _window_slices(text: str, start: int, end: int):
    words = [(match.start(), match.end()) for match in _WORD.finditer(text, start, end)]
    if not words:
        return
    index = 0
    while index < len(words):
        window_start = words[index][0]
        if words[index][1] - window_start > MAX_QUOTE_CHARS:
            # An unbroken token cannot be cited under this schema. Preserve
            # the exact windows around it without truncating the token.
            index += 1
            continue
        window_end_index = index
        while (window_end_index + 1 < len(words)
               and words[window_end_index + 1][1] - window_start <= MAX_QUOTE_CHARS):
            window_end_index += 1
        window_end = words[window_end_index][1]
        yield window_start, window_end
        if window_end_index == len(words) - 1:
            break
        next_word_start, next_word_end = words[window_end_index + 1]
        if next_word_end - next_word_start > MAX_QUOTE_CHARS:
            index = window_end_index + 1
            continue

        # Start the next window at a word boundary close to the overlap target.
        # Advancing by at least one word guarantees progress for dense text.
        target = window_end - WINDOW_OVERLAP_CHARS
        next_index = index + 1
        while next_index + 1 <= window_end_index and words[next_index + 1][0] <= target:
            next_index += 1
        index = next_index


def _record_text(item: Mapping) -> tuple[str, str]:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id:
        raise SourceBoundSchemaError("evidence_id_must_be_a_nonempty_string")
    record = item.get("record")
    text = record.get("text") if isinstance(record, Mapping) else item.get("text")
    if not isinstance(text, str):
        raise SourceBoundSchemaError(f"evidence_record_text_missing:{item_id}")
    if "text" in item and item["text"] != text:
        raise SourceBoundSchemaError(f"evidence_text_mismatch:{item_id}")
    return item_id, text


def _extract_quote_candidates(text: str) -> tuple[list[str], list[str]]:
    """Collect deduplicated primary (sentence/window) and secondary (clause) candidates."""
    primary: list[str] = []
    clause: list[str] = []
    seen: set[str] = set()
    max_collect = MAX_SNIPPETS * 2
    for raw_start, raw_end in _ranges(text, 0, len(text), _SENTENCE_END):
        segment_start, segment_end = _trim_bounds(text, raw_start, raw_end)
        if segment_start >= segment_end:
            continue
        slices = (
            [(segment_start, segment_end)]
            if segment_end - segment_start <= MAX_QUOTE_CHARS
            else _window_slices(text, segment_start, segment_end)
        )
        for start, end in slices:
            quote = text[start:end]
            if not MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS:
                continue
            if quote not in seen and len(primary) + len(clause) < max_collect:
                seen.add(quote)
                primary.append(quote)
        for clause_raw_start, clause_raw_end in _ranges(text, segment_start, segment_end, _CLAUSE_END):
            c_start, c_end = _trim_bounds(text, clause_raw_start, clause_raw_end)
            if c_start >= c_end:
                continue
            c_slices = (
                [(c_start, c_end)]
                if c_end - c_start <= MAX_QUOTE_CHARS
                else _window_slices(text, c_start, c_end)
            )
            for start, end in c_slices:
                quote = text[start:end]
                if not MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS:
                    continue
                if quote not in seen and len(primary) + len(clause) < max_collect:
                    seen.add(quote)
                    clause.append(quote)
    # Formula and list fragments can consist entirely of short lines. Offer
    # the exact multiline text if it fits, rather than rewriting its spacing.
    if not primary and not clause and MIN_QUOTE_CHARS <= len(text.strip()) <= MAX_QUOTE_CHARS:
        primary.append(text.strip())
    return primary, clause


def _snippets_for(text: str) -> list[str]:
    primary, clause = _extract_quote_candidates(text)
    return [*primary, *clause][:MAX_SNIPPETS]


def _trim_quote_budget(
    item_candidates: list[tuple[str, list[str], list[str]]],
) -> dict[str, list[str]]:
    """Trim candidate quotes deterministically to satisfy snippet and character caps."""
    total_snippets = sum(len(p) + len(c) for _, p, c in item_candidates)
    total_chars = sum(sum(map(len, p)) + sum(map(len, c)) for _, p, c in item_candidates)
    if total_snippets <= MAX_SNIPPETS and total_chars <= MAX_QUOTED_CHARS:
        return {item_id: [*p, *c] for item_id, p, c in item_candidates}

    # Tier 1: drop clause sub-snippets while preserving all primary sentence/window quotes
    # (or keeping clause snippets for an item that has no primary quotes at all).
    reduced: list[tuple[str, list[str]]] = [
        (item_id, list(p) if p else list(c)) for item_id, p, c in item_candidates
    ]
    total_snippets = sum(len(quotes) for _, quotes in reduced)
    total_chars = sum(sum(map(len, quotes)) for _, quotes in reduced)
    if total_snippets <= MAX_SNIPPETS and total_chars <= MAX_QUOTED_CHARS:
        return {item_id: quotes for item_id, quotes in reduced if quotes}

    # Tier 2: round-robin selection across items so every evidence record remains quotable.
    selected: dict[str, list[str]] = {item_id: [] for item_id, _ in reduced}
    count_used = 0
    chars_used = 0
    depth = 0
    while count_used < MAX_SNIPPETS:
        progressed = False
        for item_id, quotes in reduced:
            if depth >= len(quotes):
                continue
            quote = quotes[depth]
            if count_used + 1 > MAX_SNIPPETS:
                break
            if chars_used + len(quote) > MAX_QUOTED_CHARS:
                continue
            selected[item_id].append(quote)
            count_used += 1
            chars_used += len(quote)
            progressed = True
        if not progressed:
            break
        depth += 1
    return {item_id: quotes for item_id, quotes in selected.items() if quotes}


def source_quote_enums(evidence) -> dict[str, list[str]]:
    """Return bounded source-exact quote candidates keyed by provided source ID."""
    if not isinstance(evidence, (list, tuple)) or not evidence:
        raise SourceBoundSchemaError("evidence_records_required")

    total_source_chars = 0
    seen_ids = set()
    item_candidates: list[tuple[str, list[str], list[str]]] = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise SourceBoundSchemaError("evidence_record_must_be_an_object")
        item_id, source_text = _record_text(item)
        if item_id in seen_ids:
            raise SourceBoundSchemaError(f"duplicate_evidence_id:{item_id}")
        seen_ids.add(item_id)
        total_source_chars += len(source_text)
        if total_source_chars > MAX_SOURCE_CHARS:
            raise SourceBoundSchemaError("source_work_budget_exceeded")

        primary, clause = _extract_quote_candidates(source_text)
        record = item.get("record")
        if isinstance(record, Mapping) and "source_spans" in record:
            def filter_mapped(candidates: list[str]) -> list[str]:
                mapped: list[str] = []
                for quote in candidates:
                    try:
                        quote_spans({"text": source_text, "record": record}, quote)
                    except ValueError:
                        continue
                    mapped.append(quote)
                return mapped

            primary = filter_mapped(primary)
            clause = filter_mapped(clause)
        if not primary and not clause:
            continue
        item_candidates.append((item_id, primary, clause))
    if not item_candidates:
        raise SourceBoundSchemaError("no_eligible_quote_snippet")
    quotes_by_id = _trim_quote_budget(item_candidates)
    if not quotes_by_id:
        raise SourceBoundSchemaError("no_eligible_quote_snippet")
    return quotes_by_id


def source_bound_schema(evidence):
    """Return a focused answer schema binding every quote enum to its source ID.

    Each enum value is an exact 3–160 character substring of that source's
    record text. All sentence/clause slices and overlapping long-text windows
    must fit the explicit caps. Items without an eligible quote are excluded
    from citation branches. If no item is quotable, the schema cannot answer.
    """
    quotes_by_id = source_quote_enums(evidence)
    branches = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "quote"],
            "properties": {
                "id": {"const": item_id},
                "quote": {"type": "string", "minLength": MIN_QUOTE_CHARS,
                          "maxLength": MAX_QUOTE_CHARS, "enum": snippets},
            },
        }
        for item_id, snippets in quotes_by_id.items()
    ]

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "claims"],
        "properties": {
            "status": {"type": "string", "enum": ["answered", "insufficient_evidence"]},
            "claims": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["text", "evidence"],
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 1600},
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 2,
                            "items": {"oneOf": branches},
                        },
                    },
                },
            },
        },
    }
