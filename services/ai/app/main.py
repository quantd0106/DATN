from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.background_worker import recover_stale_state
from app.common.db import Database
from app.common.errors import (
    CorrelationMiddleware,
    UnexpectedErrorMiddleware,
    register_error_handlers,
)
from app.common.logging import logger
from app.config import Settings
from app.integrations.core_client import CoreClient
from app.integrations.document_client import (
    DocumentPort,
    HttpDocumentClient,
    LocalDevDocumentClient,
)
from app.integrations.learning_client import HttpLearningClient
from app.modules.analytics_export.router import router as analytics_router
from app.modules.health.router import router as health_router
from app.modules.indexing.router import router as indexing_router
from app.modules.quiz.router import router as quiz_router
from app.modules.tutor.learning_internal import router as learning_internal_router
from app.modules.tutor.router import router as tutor_router
from app.runtime.rag_runtime import RagRuntime


def create_app(
    settings: Settings | None = None, *, local_document: LocalDevDocumentClient | None = None
) -> FastAPI:
    config = settings if settings is not None else Settings()
    if local_document is not None and config.document_mode != "local_dev":
        raise ValueError("Document fixtures require DOCUMENT_MODE=local_dev")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = config
        try:
            database = Database(config) if config.ai_database_url else None
        except (ImportError, ValueError):
            logger.error("startup_failed")
            raise
        if database is not None:
            try:
                await run_in_threadpool(
                    recover_stale_state, database, config.ai_stale_operation_seconds
                )
            except SQLAlchemyError:
                database.close()
                logger.error("startup_failed")
                raise
        runtime = RagRuntime(config)
        try:
            async with httpx.AsyncClient(
                timeout=config.ai_dependency_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as http:
                app.state.http = http
                app.state.database = database
                app.state.runtime = runtime
                app.state.core = CoreClient(http, config)
                document: DocumentPort = HttpDocumentClient(http, config)
                if config.document_mode == "local_dev":
                    document = local_document or LocalDevDocumentClient({}, {})
                app.state.document = document
                app.state.learning = HttpLearningClient(http, config)
                logger.info("service_started")
                yield
        finally:
            await runtime.close()
            if database is not None:
                database.close()
            logger.info("service_stopped")

    app = FastAPI(title="RAG Course Assistant — AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ai_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "Idempotency-Key"],
        expose_headers=["X-Correlation-ID"],
    )
    app.add_middleware(CorrelationMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(tutor_router)
    app.include_router(learning_internal_router)
    app.include_router(quiz_router)
    app.include_router(indexing_router)
    app.include_router(analytics_router)
    return app


app = create_app()
