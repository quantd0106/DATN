from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.common.db import Database
from app.common.errors import (
    CorrelationMiddleware,
    UnexpectedErrorMiddleware,
    register_error_handlers,
)
from app.common.logging import logger
from app.config import Settings
from app.integrations.ai_client import AiPort, HttpAiClient
from app.integrations.core_client import CoreClient, CorePort
from app.integrations.document_client import (
    DocumentPort,
    HttpDocumentClient,
    LocalDevDocumentClient,
)
from app.model_registry import load_metadata
from app.modules.appeals.router import router as appeals_router
from app.modules.assessment.router import router as assessment_router
from app.modules.assignment.router import router as assignment_router
from app.modules.grading.router import router as grading_router
from app.modules.health.router import router as health_router
from app.modules.internal.router import router as internal_router
from app.modules.practice.router import router as practice_router
from app.storage.blob_store import BlobStore, LocalFsBlobStore


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    core: CorePort | None = None,
    document: DocumentPort | None = None,
    ai: AiPort | None = None,
    blob_store: BlobStore | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    load_metadata()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = database
        if db is None and config.learning_database_url is not None:
            db = Database(config)
        app.state.database = db
        async with httpx.AsyncClient(timeout=config.learning_dependency_timeout_seconds) as client:
            app.state.core = core if core is not None else CoreClient(client, config)
            app.state.ai = ai if ai is not None else HttpAiClient(client, config)
            if document is not None:
                app.state.document = document
            elif config.document_mode == "local_dev":
                app.state.document = LocalDevDocumentClient()
            else:
                app.state.document = HttpDocumentClient(client, config)
            if db is not None:
                try:
                    db.ping()
                except SQLAlchemyError:
                    logger.error("dependency_unready")
            logger.info("service_started")
            try:
                yield
            finally:
                if db is not None and database is None:
                    db.close()
                logger.info("service_stopped")

    app = FastAPI(title="Learning", lifespan=lifespan)
    app.state.settings = config
    app.state.database = database
    app.state.blob_store = (
        blob_store if blob_store is not None else LocalFsBlobStore(config.blob_storage_dir)
    )
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.learning_cors_origins,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Correlation-ID",
            "Idempotency-Key",
            "X-Admin-Reason",
        ],
        expose_headers=["X-Correlation-ID", "Content-Disposition"],
    )
    app.add_middleware(CorrelationMiddleware)
    register_error_handlers(app)
    for router in (
        health_router,
        practice_router,
        assessment_router,
        assignment_router,
        grading_router,
        appeals_router,
        internal_router,
    ):
        app.include_router(router)
    return app
