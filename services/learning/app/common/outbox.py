from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.db import Base, CreatedAt, JSONBType, UUIDPrimaryKey

OUTBOX_SCHEMA_VERSION = 1


class OutboxEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("idx_learning_outbox_pending", "status", "next_retry_at", "created_at"),
    )

    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=OUTBOX_SCHEMA_VERSION)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def record_outbox_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID | str,
    payload_json: dict[str, Any],
    aggregate_version: int | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        schema_version=OUTBOX_SCHEMA_VERSION,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        aggregate_version=aggregate_version,
        payload_json=payload_json,
        status="PENDING",
    )
    session.add(event)
    session.flush()
    return event


def record_learning_notification(
    session: Session,
    *,
    event_type: str,
    school_id: UUID,
    class_id: UUID,
    resource_type: str,
    resource_id: UUID,
    student_id: UUID | None = None,
) -> None:
    payload = {
        "school_id": str(school_id), "class_id": str(class_id),
        "resource_type": resource_type, "resource_id": str(resource_id),
    }
    if student_id is not None:
        payload["student_id"] = str(student_id)
    record_outbox_event(
        session, event_type=event_type, aggregate_type=resource_type,
        aggregate_id=resource_id, payload_json=payload,
    )
