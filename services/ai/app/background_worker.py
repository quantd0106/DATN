import argparse
import asyncio
import logging.config
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.common.db import Database
from app.common.errors import AppError
from app.common.logging import logger, server_log_config
from app.config import Settings
from app.integrations.core_client import CoreClient, CurrentUser
from app.integrations.document_client import (
    DocumentPort,
    HttpDocumentClient,
    LocalDevDocumentClient,
)
from app.integrations.learning_client import HttpLearningClient, LearningPort
from app.modules.quiz.models import JobStatus, QuizJob
from app.modules.quiz.service import retry_pending_exports, run_queued_job
from app.modules.tutor.models import Message, MessageRole, MessageStatus
from app.runtime.rag_runtime import RagRuntime


def recover_stale_state(database: Database, stale_after_seconds: int = 300) -> dict[str, int]:
    """Fail orphaned streaming tutor replies and requeue stalled running quiz jobs."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=stale_after_seconds)
    with database.SessionLocal.begin() as session:
        message_ids = list(
            session.scalars(
                update(Message)
                .where(
                    Message.role == MessageRole.ASSISTANT,
                    Message.status == MessageStatus.STREAMING,
                    Message.created_at <= cutoff,
                )
                .values(
                    status=MessageStatus.FAILED,
                    content=None,
                    rendered_payload_json=None,
                    pedagogical_hint=None,
                )
                .returning(Message.id)
            ).all()
        )
        job_ids = list(
            session.scalars(
                update(QuizJob)
                .where(
                    QuizJob.status == JobStatus.RUNNING,
                    QuizJob.updated_at <= cutoff,
                )
                .values(status=JobStatus.QUEUED, error_code=None, updated_at=now)
                .returning(QuizJob.id)
            ).all()
        )
    if message_ids or job_ids:
        logger.info("stale_state_recovered")
    if job_ids:
        logger.info("quiz_job_recovered")
    return {"stale_messages": len(message_ids), "recovered_jobs": len(job_ids)}


async def run_worker_cycle(
    database: Database,
    runtime: RagRuntime,
    core: CoreClient,
    document: DocumentPort,
    learning: LearningPort,
    correlation_id: str,
    *,
    stale_after_seconds: int = 300,
    export_limit: int = 20,
) -> dict[str, int]:
    recovered = await run_in_threadpool(recover_stale_state, database, stale_after_seconds)

    def next_queued_job() -> QuizJob | None:
        with database.SessionLocal() as session:
            return session.scalar(
                select(QuizJob)
                .where(QuizJob.status == JobStatus.QUEUED)
                .order_by(QuizJob.created_at, QuizJob.id)
                .limit(1)
            )

    jobs_completed = 0
    jobs_failed = 0
    job = await run_in_threadpool(next_queued_job)
    if job is not None:
        teacher = CurrentUser(
            id=job.requested_by,
            school_id=job.school_id,
            roles=("LECTURER",),
            status="ACTIVE",
        )
        try:
            await run_queued_job(
                runtime,
                database,
                core,
                document,
                teacher,
                job.class_id,
                job.id,
                correlation_id,
            )
            jobs_completed += 1
        except AppError as exc:
            if exc.status_code != 409:
                jobs_failed += 1
                logger.error("worker_cycle_failed")
        except (httpx.HTTPError, OSError, SQLAlchemyError, RuntimeError, ValueError, TypeError):
            jobs_failed += 1
            logger.error("worker_cycle_failed")

    export_counts = await retry_pending_exports(
        database, document, learning, correlation_id, limit=export_limit
    )
    if export_counts["exports_sent"] or export_counts["exports_blocked"]:
        logger.info("quiz_export_retried")

    return {
        **recovered,
        "jobs_completed": jobs_completed,
        "jobs_failed": jobs_failed,
        **export_counts,
    }


async def run_worker(settings: Settings | None = None, *, once: bool = False) -> None:
    config = settings or Settings()
    if config.ai_environment != "local_dev" and config.document_mode == "local_dev":
        raise ValueError("DOCUMENT_MODE=local_dev requires AI_ENVIRONMENT=local_dev")
    database = Database(config)
    runtime = RagRuntime(config)
    try:
        async with httpx.AsyncClient(timeout=config.ai_dependency_timeout_seconds) as http:
            core = CoreClient(http, config)
            document: DocumentPort = (
                LocalDevDocumentClient({}, {})
                if config.document_mode == "local_dev"
                else HttpDocumentClient(http, config)
            )
            learning = HttpLearningClient(http, config)
            while True:
                try:
                    await run_worker_cycle(
                        database,
                        runtime,
                        core,
                        document,
                        learning,
                        uuid4().hex,
                        stale_after_seconds=config.ai_stale_operation_seconds,
                    )
                    logger.info("worker_cycle_completed")
                except asyncio.CancelledError:
                    raise
                except (
                    AppError,
                    httpx.HTTPError,
                    OSError,
                    SQLAlchemyError,
                    RuntimeError,
                    ValueError,
                    TypeError,
                ):
                    logger.error("worker_cycle_failed")
                if once:
                    break
                await asyncio.sleep(config.ai_worker_poll_seconds)
    finally:
        await runtime.close()
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI service background worker.")
    parser.add_argument("--once", action="store_true", help="Run a single worker cycle and exit.")
    args = parser.parse_args()
    logging.config.dictConfig(server_log_config())
    asyncio.run(run_worker(once=args.once))


if __name__ == "__main__":
    main()
