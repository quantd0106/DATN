from __future__ import annotations

import io
import json
from uuid import uuid4

import httpx
import pytest
from rag.generation.openai_compatible import OpenAICompatibleGenerator
from rag.generation.pipeline import GroundedTaskPipeline

from app.common.errors import AppError
from app.config import Settings
from app.integrations.document_client import ActiveGrant
from app.modules.tutor.canonical_fallback import parse_canonical_records, rank_canonical_records
from app.runtime.rag_runtime import RagRuntime


def _base_settings(**overrides) -> Settings:
    defaults = {
        "ai_environment": "local_dev",
        "ai_database_url": "postgresql+psycopg://postgres:postgres@localhost:5432/test_ai",
        "core_api_url": "http://127.0.0.1:8000",
        "core_service_key": "core-secret",
        "document_mode": "disabled",
        "learning_mode": "disabled",
        "llama_mode": "disabled",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_openai_compatible_settings_require_base_and_key():
    with pytest.raises(ValueError, match="LLM_API_BASE and LLM_API_KEY"):
        _base_settings(llama_mode="openai_compatible")
    settings = _base_settings(
        llama_mode="openai_compatible",
        llm_api_base="https://api.deepseek.com",
        llm_api_key="sk-test-secret",
        llm_model="deepseek-chat",
    )
    assert settings.llama_mode == "openai_compatible"
    assert settings.llm_model == "deepseek-chat"


def test_openai_compatible_generator_pipeline_roundtrip():
    captured: dict = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            body = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"answer": "ok"})},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            ).encode("utf-8")
            return FakeResponse(body)

    gen = OpenAICompatibleGenerator(
        "https://api.deepseek.com/",
        "sk-test-123",
        model="deepseek-chat",
        context_tokens=4096,
        max_new_tokens=512,
        output_ceiling=768,
    )
    gen.opener = FakeOpener()
    assert "sk-test-123" not in json.dumps(gen.identity)
    assert gen.identity["model"] == "deepseek-chat"

    pipeline = GroundedTaskPipeline(gen)
    assert pipeline.generator is gen
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    raw = gen.generate(
        [{"role": "system", "content": "Be concise."}, {"role": "user", "content": "Hi"}],
        schema,
    )
    assert raw["finish_reason"] == "stop"
    assert json.loads(raw["text"]) == {"answer": "ok"}
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer sk-test-123"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert "reasoning" not in captured["body"]
    assert "JSON Schema" in captured["body"]["messages"][0]["content"]
    assert gen.counter.count("Một đoạn văn bản tiếng Việt để đếm token") >= 1


def test_openrouter_generator_disables_reasoning_and_snaps_quotes():
    from rag.generation.openai_compatible import _normalize_response_json

    captured: dict = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            body = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"ok": True})},
                        }
                    ]
                }
            ).encode("utf-8")
            return FakeResponse(body)

    gen = OpenAICompatibleGenerator(
        "https://openrouter.ai/api/v1",
        "sk-or-test",
        model="tencent/hy4-flash",
        context_tokens=16384,
        max_new_tokens=1024,
        output_ceiling=1536,
    )
    gen.opener = FakeOpener()
    gen.generate(
        [{"role": "system", "content": "Only JSON."}, {"role": "user", "content": "Hi"}],
        {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
    )
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["body"]["reasoning"] == {"enabled": False}
    assert captured["body"]["model"] == "tencent/hy4-flash"

    # Also verifies duplicate quotes in quiz fact_extract output get deduplicated cleanly
    schema = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "quotes": {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "properties": {
                                            "id": {"const": "S1"},
                                            "quote": {"type": "string", "enum": ["Phương trình bậc hai ax^2 + bx + c = 0"]},
                                        },
                                    }
                                ]
                            },
                        }
                    },
                },
            }
        },
    }
    raw = json.dumps(
        {
            "facts": [
                {
                    "quotes": [
                        {"id": "S1", "quote": "Phương trình bậc hai"},
                        {"id": "S1", "quote": "ax^2 + bx + c = 0"},
                    ]
                }
            ]
        },
        ensure_ascii=False,
    )
    normalized = json.loads(_normalize_response_json(raw, schema))
    assert normalized["facts"][0]["quotes"] == [
        {"id": "S1", "quote": "Phương trình bậc hai ax^2 + bx + c = 0"}
    ]


@pytest.mark.anyio
async def test_quiz_canonical_fallback_loads_units_and_caches_facts(sqlite_db, monkeypatch):
    from rag.generation.tasks.quiz.blueprint import parse_quiz_spec
    from app.integrations.document_client import GrantSnapshot
    from app.modules.quiz.service import load_quiz_units, propose_quiz

    doc_id = uuid4()
    ver_id = uuid4()
    class_id = uuid4()
    school_id = uuid4()
    grant = ActiveGrant(document_id=doc_id, document_version_id=ver_id, source_id=str(ver_id))
    snapshot = GrantSnapshot(class_id=class_id, school_id=school_id, grants=(grant,))

    class _FakeDoc:
        async def active_grants(self, cid, sid, corr):
            return snapshot

        def document_name(self, vid):
            return "Chuyên đề Toán 10"

        async def canonical_artifact(self, g, corr):
            return {
                "schema_version": "idp-canonical-1",
                "pages": [
                    {
                        "page_number": 1,
                        "blocks": [
                            {
                                "type": "Paragraph",
                                "text": "Hàm số bậc hai có dạng y = ax^2 + bx + c với a khác 0. Đỉnh parabol có hoành độ x = -b/(2a).",
                            }
                        ],
                    }
                ],
            }

    class _FakeCore:
        async def check_access(self, cid, user, action, corr):
            from app.integrations.core_client import AccessDecision

            return AccessDecision(
                allowed=True,
                class_id=cid,
                user_id=user.id,
                action=action,
                school_id=school_id,
                class_status="ACTIVE",
                teaching_role="PRIMARY",
            )

    settings = _base_settings(
        llama_mode="openai_compatible",
        llm_api_base="https://openrouter.ai/api/v1",
        llm_api_key="sk-or-test",
        llm_model="tencent/hy4-flash",
    )
    runtime = RagRuntime(settings)
    gen = runtime.pipeline().generator

    fact_calls = 0

    def fake_generate(messages, schema, *, profile=None, max_new_tokens=None, seed=None):
        nonlocal fact_calls
        fact_calls += 1
        return {
            "text": json.dumps(
                {
                    "facts": [
                        {
                            "statement": "Hàm số bậc hai có dạng y = ax^2 + bx + c với a khác 0.",
                            "kind": "definition",
                            "level_hint": "biet",
                            "quotes": [
                                {
                                    "id": "S1",
                                    "quote": "Hàm số bậc hai có dạng y = ax^2 + bx + c với a khác 0.",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    monkeypatch.setattr(gen, "generate", fake_generate)

    db = sqlite_db
    spec = parse_quiz_spec(
        {
            "class_id": str(class_id),
            "purpose": "PRACTICE",
            "grade": 10,
            "subject": "Toán",
            "language": "vi",
            "scope": [{"document_version_id": str(ver_id)}],
        }
    )
    user = type("U", (), {"id": uuid4(), "school_id": school_id, "roles": ("LECTURER",)})()
    proposal = await propose_quiz(
        runtime, db, _FakeCore(), _FakeDoc(), user, class_id, spec, 1, "corr-1"
    )
    assert len(proposal) == 1
    assert fact_calls == 1
    assert runtime.fact_cache_for([str(ver_id)])
    units = load_quiz_units(db, runtime, snapshot, spec)
    assert len(units) == 1 and "Hàm số bậc hai" in units[0].text
    # Second call hits cached facts and avoids redundant LLM call
    await propose_quiz(
        runtime, db, _FakeCore(), _FakeDoc(), user, class_id, spec, 1, "corr-2"
    )
    assert fact_calls == 1
    await runtime.close()


@pytest.mark.anyio
async def test_rag_runtime_openai_compatible_probe_and_pipeline():
    settings = _base_settings(
        llama_mode="openai_compatible",
        llm_api_base="https://api.deepseek.com",
        llm_api_key="sk-test-secret",
        llm_model="deepseek-chat",
    )
    runtime = RagRuntime(settings)
    async with httpx.AsyncClient() as client:
        assert await runtime.probe_llama(client) == "connected"
    pipe = runtime.pipeline()
    assert pipe.generator.model == "deepseek-chat"

    disabled_runtime = RagRuntime(_base_settings(llama_mode="disabled"))
    with pytest.raises(AppError) as exc:
        disabled_runtime.pipeline()
    assert exc.value.status_code == 503


def test_canonical_fallback_parses_and_prioritizes_requested_page():
    grant = ActiveGrant(
        document_id=uuid4(),
        document_version_id=uuid4(),
        source_id="src-1",
    )
    artifact = {
        "schema_version": "idp-canonical-1",
        "pages": [
            {
                "page_number": 1,
                "blocks": [{"type": "Paragraph", "text": "Giới thiệu chương 1 về đạo hàm."}],
            },
            {
                "page_number": 141,
                "blocks": [
                    {
                        "type": "Paragraph",
                        "text": "Trang 141 trình bày bài tập phương trình vi phân cấp hai.",
                    }
                ],
            },
        ],
    }
    records = parse_canonical_records(grant, artifact, document_name="phương trình vi phân")
    assert len(records) == 2
    ranked = rank_canonical_records(
        records,
        "Mình đang đọc phương trình vi phân, trang 141. Hãy hướng dẫn mình hiểu trang này.",
        limit=1,
    )
    assert ranked[0]["page"] == 141
    assert ranked[0]["document_name"] == "phương trình vi phân"


def test_openai_compatible_snaps_partial_quote_to_schema_enum():
    from rag.generation.openai_compatible import _normalize_response_json

    schema = {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "evidence": {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "properties": {
                                            "id": {"const": "S1"},
                                            "quote": {
                                                "type": "string",
                                                "enum": [
                                                    "12\nChương 1. Các bài toán dẫn đến phương trình vi phân"
                                                ],
                                            },
                                        },
                                    }
                                ]
                            },
                        }
                    },
                },
            }
        },
    }
    raw = json.dumps(
        {
            "claims": [
                {
                    "text": "Chương 1 giới thiệu phương trình vi phân.",
                    "basis": "source",
                    "evidence": [
                        {"id": "S1", "quote": "Chương 1. Các bài toán dẫn đến phương trình vi phân"}
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    normalized = json.loads(_normalize_response_json(raw, schema))
    assert (
        normalized["claims"][0]["evidence"][0]["quote"]
        == "12\nChương 1. Các bài toán dẫn đến phương trình vi phân"
    )

    fenced = '```json\n{"status": "answered", "claims": [{"text": "Chương 1", "basis": "source", "evidence": [{"id": "S1", "quote": "Chương 1. Các bài toán dẫn đến phương trình vi phân"}]}]}\n```'
    normalized_fenced = json.loads(
        _normalize_response_json(fenced, {**schema, "required": ["status", "claims", "missing_parts"]})
    )
    assert normalized_fenced["missing_parts"] == []


def test_canonical_fallback_extracts_equations_and_attaches_citation_document_id():
    from datetime import UTC, datetime
    from app.modules.tutor.models import Message, MessageRole, MessageStatus, TutorMode
    from app.modules.tutor.service import project_history_message

    grant = ActiveGrant(
        document_id=uuid4(),
        document_version_id=uuid4(),
        source_id="src-eq",
    )
    artifact = {
        "schema_version": "idp-canonical-1",
        "pages": [
            {
                "page_number": 3,
                "blocks": [{"type": "Equation", "latex": [r"ax^2 + bx + c = 0"]}],
            }
        ],
    }
    records = parse_canonical_records(grant, artifact, document_name="Đại số 9")
    assert len(records) == 1 and "ax^2 + bx + c = 0" in records[0]["text"]
    ranked = rank_canonical_records(records, "giai phuong trinh dai so", limit=1)
    assert ranked[0]["page"] == 3

    msg = Message(
        id=uuid4(),
        conversation_id=uuid4(),
        role=MessageRole.ASSISTANT,
        mode=TutorMode.QUICK_QA,
        status=MessageStatus.COMPLETED,
        created_at=datetime.now(UTC),
        rendered_payload_json={
            "status": "answered",
            "claims": [
                {
                    "text": "Phương trình bậc hai có dạng chuẩn.",
                    "basis": "source",
                    "citations": [
                        {
                            "source_id": "src-eq",
                            "quote": "ax^2 + bx + c = 0",
                            "source_spans": [{"start": 0, "end": 17, "page": 3}],
                            "location": {"document_name": "Đại số 9"},
                        }
                    ],
                }
            ],
            "missing_parts": [],
        },
    )
    projected = project_history_message(
        msg,
        frozenset({"src-eq"}),
        grants_by_source={"src-eq": grant},
    )
    cite = projected.rendered["claims"][0]["citations"][0]
    assert cite["document_id"] == str(grant.document_id)
    assert cite["location"]["page"] == 3
