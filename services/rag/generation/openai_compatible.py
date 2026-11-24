"""OpenAI-compatible / DeepSeek chat completions adapter for GroundedTaskPipeline."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            newurl, code, f"redirect blocked ({code})", headers, fp
        )


def _schema_instruction(schema: dict[str, Any]) -> str:
    compact = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return (
        "You MUST respond with a single valid JSON object (no markdown fences, no extra prose) "
        'that strictly conforms to this JSON Schema. Whenever a field specifies "enum" or '
        '"const", copy an exact string from "enum" verbatim without truncating or changing '
        f"whitespace:\n{compact}"
    )


def _extract_quote_enums(node: Any, out: dict[str, list[str]]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            id_spec = props.get("id")
            quote_spec = props.get("quote")
            if (
                isinstance(id_spec, dict)
                and isinstance(id_spec.get("const"), str)
                and isinstance(quote_spec, dict)
                and isinstance(quote_spec.get("enum"), list)
            ):
                snippets = [s for s in quote_spec["enum"] if isinstance(s, str)]
                if snippets:
                    out.setdefault(id_spec["const"], []).extend(snippets)
        for value in node.values():
            _extract_quote_enums(value, out)
    elif isinstance(node, list):
        for item in node:
            _extract_quote_enums(item, out)


def _match_quote_enum(quote: str, snippets: list[str]) -> str | None:
    if not snippets:
        return None
    if quote in snippets:
        return quote
    q_norm = " ".join(quote.split()).lower()
    if not q_norm:
        return snippets[0]
    for snippet in snippets:
        s_norm = " ".join(snippet.split()).lower()
        if q_norm in s_norm or s_norm in q_norm:
            return snippet
    q_words = set(q_norm.split())
    best_snippet = None
    best_score = 0.0
    for snippet in snippets:
        s_words = set(" ".join(snippet.split()).lower().split())
        if not s_words:
            continue
        overlap = len(q_words & s_words) / max(1, len(q_words))
        if overlap > best_score:
            best_score = overlap
            best_snippet = snippet
    return best_snippet if best_score >= 0.4 else None


def _snap_quote_enums(node: Any, enums_by_id: dict[str, list[str]]) -> Any:
    if isinstance(node, dict):
        if (
            isinstance(node.get("id"), str)
            and isinstance(node.get("quote"), str)
            and node["id"] in enums_by_id
        ):
            matched = _match_quote_enum(node["quote"], enums_by_id[node["id"]])
            if matched is not None:
                node["quote"] = matched
            return node
        for key, value in node.items():
            snapped = _snap_quote_enums(value, enums_by_id)
            if key == "evidence" and isinstance(snapped, list):
                deduped = []
                seen: set[tuple[str, str]] = set()
                for item in snapped:
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("id"), str)
                        and isinstance(item.get("quote"), str)
                    ):
                        pair = (item["id"], item["quote"])
                        if pair in seen:
                            continue
                        seen.add(pair)
                    deduped.append(item)
                node[key] = deduped
            else:
                node[key] = snapped
        return node
    if isinstance(node, list):
        return [_snap_quote_enums(item, enums_by_id) for item in node]
    return node


def _strip_markdown_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    return stripped


def _normalize_response_json(content: str, schema: dict[str, Any]) -> str:
    cleaned = _strip_markdown_fences(content)
    enums_by_id: dict[str, list[str]] = {}
    _extract_quote_enums(schema, enums_by_id)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return cleaned
    if isinstance(parsed, dict):
        if (
            parsed.get("status") == "answered"
            and "missing_parts" not in parsed
            and "missing_parts" in json.dumps(schema, ensure_ascii=False)
        ):
            parsed["missing_parts"] = []
    snapped = _snap_quote_enums(parsed, enums_by_id) if enums_by_id else parsed
    return json.dumps(snapped, ensure_ascii=False)


def _inject_schema_prompt(
    messages: list[dict[str, str]], schema: dict[str, Any]
) -> list[dict[str, str]]:
    instruction = _schema_instruction(schema)
    copied = [dict(m) for m in messages]
    if copied and copied[0].get("role") == "system":
        copied[0]["content"] = (
            f"{copied[0].get('content', '').rstrip()}\n\n{instruction}".strip()
        )
        return copied
    return [{"role": "system", "content": instruction}, *copied]


class OpenAICompatibleGenerator:
    """Adapter matching LlamaGenerator's contract against an OpenAI-compatible HTTP API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        context_tokens: int = 8192,
        max_new_tokens: int = 1024,
        output_ceiling: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        clean_base = str(base_url).strip().rstrip("/")
        if not clean_base.startswith(("https://", "http://")):
            raise ValueError("base_url must be http or https")
        clean_base = clean_base.removesuffix("/chat/completions")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key is required")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is required")
        if (
            type(context_tokens) is not int
            or type(max_new_tokens) is not int
            or context_tokens < 833
            or max_new_tokens < 64
            or max_new_tokens + 64 >= context_tokens
        ):
            raise ValueError("invalid generator context/output budget")
        if (
            type(output_ceiling) is not int
            or not max_new_tokens <= output_ceiling <= 4096
            or output_ceiling + 64 >= context_tokens
        ):
            raise ValueError("invalid generator output ceiling")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 600
        ):
            raise ValueError("timeout must be in (0, 600]")

        self.base_url = clean_base
        self._api_key = api_key.strip()
        self.model = model.strip()
        self.context_tokens = context_tokens
        self.max_new_tokens = max_new_tokens
        self.output_ceiling = output_ceiling
        self.timeout = float(timeout)
        self.opener = urllib.request.build_opener(_NoRedirect())
        self.identity = {
            "adapter": "openai-compatible-generator-1",
            "model": self.model,
            "base_url": self.base_url,
            "context_tokens": context_tokens,
            "max_new_tokens": max_new_tokens,
            "output_ceiling": output_ceiling,
            "external_inference": True,
        }

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        total = 4
        for message in messages:
            serialized = json.dumps(message, ensure_ascii=False)
            total += max(4, (len(serialized) + 1) // 2)
        return total

    def generate(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        *,
        profile: dict[str, Any] | None = None,
        max_new_tokens: int | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        budget = self.max_new_tokens if max_new_tokens is None else max_new_tokens
        if type(budget) is not int or not 1 <= budget <= self.output_ceiling:
            raise ValueError("invalid max_new_tokens")
        if seed is not None and type(seed) is not int:
            raise ValueError("invalid seed")

        temperature = 0.2
        if isinstance(profile, dict) and isinstance(
            profile.get("temperature"), (int, float)
        ):
            temperature = float(profile["temperature"])

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _inject_schema_prompt(messages, schema),
            "max_tokens": max(budget, self.output_ceiling),
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("generation response byte limit")
        body = json.loads(raw)
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ValueError("missing choices in completion response")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise (
                ValueError("missing message content in completion response")
                if content is None
                else TypeError("invalid message content type")
            )
        return {
            "text": _normalize_response_json(content, schema),
            "finish_reason": first.get("finish_reason"),
            "usage": body.get("usage"),
        }
