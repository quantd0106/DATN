import asyncio
from datetime import datetime, timedelta
from logging.config import dictConfig
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_, select

from app.common.db import Database, utcnow, write_transaction
from app.common.errors import AppError
from app.common.logging import logger, server_log_config
from app.common.outbox import OutboxEvent
from app.config import Settings
from app.integrations.core_client import CoreClient, CorePort
from app.model_registry import load_metadata
from app.modules.assessment.service import sweep_expired_attempts
from app.modules.assignment.service import discard_expired_uploads
from app.storage.blob_store import LocalFsBlobStore

DEADLINE_EVENT = "calendar.deadline.upsert"
LEARNING_NOTIFICATION_EVENTS = (
    "ASSESSMENT_RESULT_RELEASED",
    "GRADE_PROPOSAL_PENDING",
    "APPEAL_RESOLVED",
    "PRACTICE_PUBLISHED",
)


async def dispatch_event(event: OutboxEvent, core: CorePort, correlation_id: str) -> None:
    data = event.payload_json
    school_id, class_id = UUID(data["school_id"]), UUID(data["class_id"])
    if event.event_type == DEADLINE_EVENT:
        receipt = await core.upsert_learning_deadline(
            school_id=school_id,
            class_id=class_id,
            external_ref=data["external_ref"],
            title=data["title"],
            starts_at=datetime.fromisoformat(data["starts_at"]),
            ends_at=datetime.fromisoformat(data["ends_at"]),
            version=data["version"],
            activity_kind=data.get("activity_kind"),
            correlation_id=correlation_id,
        )
        if receipt.version < data["version"]:
            raise AppError(503)
        return
    student_id = data.get("student_id")
    await core.publish_learning_notification(
        source_event_id=event.id,
        event_type=event.event_type,
        school_id=school_id,
        class_id=class_id,
        resource_type=data["resource_type"],
        resource_id=UUID(data["resource_id"]),
        student_id=UUID(student_id) if student_id is not None else None,
        correlation_id=correlation_id,
    )


async def run_worker_cycle(
    database: Database, core: CorePort, correlation_id: str
) -> dict[str, int]:
    with database.SessionLocal() as session, write_transaction(session):
        expired = sweep_expired_attempts(session)
    logger.info("sweeper_cycle_completed")

    counts = {
        "expired_attempts": expired,
        "deadlines_sent": 0,
        "deadlines_failed": 0,
        "notifications_sent": 0,
        "notifications_failed": 0,
    }
    for _ in range(100):
        with database.SessionLocal() as session, write_transaction(session):
            event = session.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == "PENDING",
                    OutboxEvent.event_type.in_((DEADLINE_EVENT, *LEARNING_NOTIFICATION_EVENTS)),
                    or_(OutboxEvent.next_retry_at.is_(None), OutboxEvent.next_retry_at <= utcnow()),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if event is None:
                break
            group = "deadlines" if event.event_type == DEADLINE_EVENT else "notifications"
            try:
                await dispatch_event(event, core, correlation_id)
            except AppError:
                event.retry_count += 1
                event.next_retry_at = utcnow() + timedelta(
                    seconds=min(300, 2 ** min(9, event.retry_count))
                )
                counts[f"{group}_failed"] += 1
            else:
                event.status, event.sent_at, event.next_retry_at = "SENT", utcnow(), None
                counts[f"{group}_sent"] += 1
    failed = counts["deadlines_failed"] or counts["notifications_failed"]
    logger.info("outbox_dispatch_failed" if failed else "outbox_dispatch_completed")
    return counts


async def run_worker() -> None:
    settings = Settings()
    load_metadata()
    database = Database(settings)
    store = LocalFsBlobStore(settings.blob_storage_dir)
    try:
        async with httpx.AsyncClient(
            timeout=settings.learning_dependency_timeout_seconds
        ) as client:
            core = CoreClient(client, settings)
            while True:
                try:
                    await run_worker_cycle(database, core, str(uuid4()))
                    with database.SessionLocal() as session:
                        discard_expired_uploads(session, store)
                except Exception:  # noqa: BLE001 - retry the cycle without logging sensitive details
                    logger.error("sweeper_cycle_failed")
                await asyncio.sleep(settings.sweeper_interval_seconds)
    finally:
        database.close()


if __name__ == "__main__":
    dictConfig(server_log_config())
    asyncio.run(run_worker())
