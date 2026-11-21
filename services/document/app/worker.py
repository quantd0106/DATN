"""Process Document versions and relay transactional outbox events."""

import argparse
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from math import isfinite
from time import sleep

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.model_registry  # noqa: F401
from app.common.config import Settings, get_settings
from app.common.db import SessionLocal, transaction, utc_now
from app.common.outbox import OutboxEvent
from app.common.storage import StorageBackend, get_storage
from app.modules.versions.public import process_next_version

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
Publisher = Callable[[OutboxEvent], None]


def log_outbox_event(event: OutboxEvent) -> None:
    metadata = {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "aggregate_id": str(event.aggregate_id),
        "aggregate_version": event.aggregate_version,
    }
    logger.info("Document outbox event %s", json.dumps(metadata))


def relay_outbox_events(
    session_factory: SessionFactory, *, publisher: Publisher = log_outbox_event
) -> int:
    eligible_at = utc_now()
    query = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None), OutboxEvent.available_at <= eligible_at)
        .order_by(OutboxEvent.available_at, OutboxEvent.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    relayed = 0
    while True:
        with session_factory() as session, transaction(session):
            event = session.scalar(query)
            if event is None:
                return relayed
            try:
                publisher(event)
            except Exception:  # noqa: BLE001 - Publishers may raise transport-specific exceptions.
                event.attempts += 1
                event.last_error_code = "OUTBOX_PUBLISH_FAILED"
                delay = min(300, 2 ** min(event.attempts, 9))
                event.available_at = utc_now() + timedelta(seconds=delay)
            else:
                event.published_at = utc_now()
                event.last_error_code = None
                relayed += 1


def run_once(
    session_factory: SessionFactory,
    storage: StorageBackend,
    config: Settings,
    *,
    publisher: Publisher = log_outbox_event,
) -> None:
    while process_next_version(session_factory, storage, config):
        relay_outbox_events(session_factory, publisher=publisher)
    relay_outbox_events(session_factory, publisher=publisher)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process available work and exit.")
    parser.add_argument("--poll-seconds", type=float, help="Time between worker passes.")
    args = parser.parse_args(argv)
    config = get_settings()
    poll_seconds = config.document_worker_poll_seconds
    if args.poll_seconds is not None:
        if not isfinite(args.poll_seconds) or args.poll_seconds <= 0:
            parser.error("--poll-seconds must be a finite positive number")
        poll_seconds = args.poll_seconds
    storage = get_storage()
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            try:
                run_once(SessionLocal, storage, config)
            except SQLAlchemyError as exc:
                logger.error("Document worker database operation failed (%s)", type(exc).__name__)
                if args.once:
                    return 1
            if args.once:
                return 0
            sleep(poll_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
