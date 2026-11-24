from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock
from typing import Literal
from uuid import UUID

import httpx
from rag.embedding.bge_embedding import OnnxBgeM3
from rag.generation.openai_compatible import OpenAICompatibleGenerator
from rag.generation.pipeline import GroundedTaskPipeline
from rag.generation.rag_generation import LlamaGenerator
from rag.generation.runtime.scheduler import SlotScheduler
from rag.retrieval.corpus import load_retriever
from rag.retrieval.retrieval import TextRetriever

from app.common.errors import AppError
from app.config import Settings
from app.runtime.executor import RagExecutor


class CancelRegistry:
    def __init__(self):
        self._lock = Lock()
        self._running: dict[UUID, Event] = {}

    @contextmanager
    def track(self, operation_id: UUID) -> Iterator[Event]:
        with self._lock:
            if operation_id in self._running:
                raise AppError(409)
            cancelled = self._running[operation_id] = Event()
        try:
            yield cancelled
        finally:
            with self._lock:
                self._running.pop(operation_id)

    def cancel(self, operation_id: UUID) -> bool:
        with self._lock:
            event = self._running.get(operation_id)
            if event is None:
                return False
            event.set()
            return True

    def cancel_all(self) -> None:
        with self._lock:
            for event in self._running.values():
                event.set()


class RagRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.scheduler = SlotScheduler(1)
        self.executor = RagExecutor(settings.ai_rag_workers, settings.ai_rag_admission_seconds)
        self.cancellations = CancelRegistry()
        self._pipeline: GroundedTaskPipeline | None = None
        self._pipeline_lock = Lock()
        self._encoder: OnnxBgeM3 | None = None
        self._encoder_lock = Lock()
        self._retrievers: dict[UUID, TextRetriever] = {}
        self._retriever_lock = Lock()
        self._canonical_records: dict[UUID, list[dict]] = {}
        self._canonical_lock = Lock()

    def has_canonical_records(self, document_version_id: UUID) -> bool:
        with self._canonical_lock:
            return document_version_id in self._canonical_records

    def canonical_records(self, document_version_id: UUID) -> list[dict]:
        with self._canonical_lock:
            return list(self._canonical_records.get(document_version_id, ()))

    def store_canonical_records(self, document_version_id: UUID, records: list[dict]) -> None:
        with self._canonical_lock:
            self._canonical_records[document_version_id] = list(records)

    def pipeline(self) -> GroundedTaskPipeline:
        settings = self.settings
        if settings.llama_mode == "disabled":
            raise AppError(503)
        with self._pipeline_lock:
            if self._pipeline is None:
                try:
                    if settings.llama_mode == "openai_compatible":
                        if settings.llm_api_base is None or settings.llm_api_key is None:
                            raise ValueError("LLM_API_BASE and LLM_API_KEY are required")
                        output_ceiling = max(settings.llama_output_ceiling, 1024)
                        generator = OpenAICompatibleGenerator(
                            str(settings.llm_api_base),
                            settings.llm_api_key.get_secret_value(),
                            model=settings.llm_model,
                            context_tokens=max(settings.llama_context_tokens, 16384),
                            max_new_tokens=min(output_ceiling, 1024),
                            output_ceiling=output_ceiling,
                            timeout=settings.llama_timeout_seconds,
                        )
                    else:
                        generator = LlamaGenerator(
                            str(settings.llama_url),
                            model_path=settings.llama_model_path,
                            server_path=settings.llama_server_path,
                            provenance={
                                "repo": settings.llama_model_repo,
                                "revision": settings.llama_model_revision,
                                "variant": settings.llama_model_variant,
                                "sha256": settings.llama_model_sha256,
                            },
                            context_tokens=settings.llama_context_tokens,
                            output_ceiling=settings.llama_output_ceiling,
                            timeout=settings.llama_timeout_seconds,
                        )
                except (OSError, ValueError):
                    raise AppError(503) from None
                self._pipeline = GroundedTaskPipeline(generator, scheduler=self.scheduler)
            return self._pipeline

    def retriever(self, generation_id: UUID, path: Path, *, encoder) -> TextRetriever:
        """Load published corpora; callers must prefilter retrieval with current grants."""
        with self._retriever_lock:
            loaded = self._retrievers.get(generation_id)
            if loaded is None:
                loaded, _, _ = load_retriever(path, encoder=encoder)
                self._retrievers[generation_id] = loaded
            return loaded

    def encoder(self) -> OnnxBgeM3:
        config = self.settings
        with self._encoder_lock:
            if self._encoder is None:
                if not all(
                    (
                        config.ai_embedding_model_path,
                        config.ai_embedding_tokenizer_path,
                        config.ai_embedding_repo,
                        config.ai_embedding_revision,
                        config.ai_embedding_variant,
                    )
                ):
                    raise AppError(503)
                try:
                    self._encoder = OnnxBgeM3(
                        config.ai_embedding_model_path,
                        config.ai_embedding_tokenizer_path,
                        provenance={
                            "repo": config.ai_embedding_repo,
                            "revision": config.ai_embedding_revision,
                            "variant": config.ai_embedding_variant,
                        },
                        threads=config.ai_embedding_threads,
                        max_tokens=config.ai_embedding_max_tokens,
                    )
                except (ImportError, OSError, ValueError, RuntimeError):
                    raise AppError(503) from None
            return self._encoder

    def evict_retriever(self, generation_id: UUID) -> None:
        with self._retriever_lock:
            self._retrievers.pop(generation_id, None)

    async def probe_llama(self, client: httpx.AsyncClient) -> Literal["connected", "disabled"]:
        if self.settings.llama_mode == "disabled":
            return "disabled"
        if self.settings.llama_mode == "openai_compatible":
            if (
                self.settings.llm_api_base is not None
                and self.settings.llm_api_key is not None
                and self.settings.llm_api_key.get_secret_value().strip()
            ):
                return "connected"
            raise AppError(503)
        try:
            response = await client.get(f"{str(self.settings.llama_url).rstrip('/')}/health")
            if response.status_code == 200 and response.json() == {"status": "ok"}:
                return "connected"
        except (httpx.HTTPError, ValueError):
            pass
        raise AppError(503)

    async def close(self) -> None:
        self.cancellations.cancel_all()
        await self.executor.close()
