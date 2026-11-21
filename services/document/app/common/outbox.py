from datetime import datetime
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Index, String, func, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.db import JSON_OBJECT, Base, CreatedAt, UTCDateTime


class OutboxEvent(CreatedAt, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "idx_doc_outbox_unpublished",
            "available_at",
            "id",
            postgresql_where=text("published_at IS NULL"),
            sqlite_where=text("published_at IS NULL"),
        ),
    )

    schema_version: Mapped[int] = mapped_column(server_default=text("1"))
    event_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[UUID]
    aggregate_version: Mapped[int]
    payload_json: Mapped[dict] = mapped_column(JSON_OBJECT)
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(String(64))


def record_outbox_event(
    session: Session, event_type: str, aggregate_id: UUID, aggregate_version: int, payload: dict
) -> None:
    event_id = uuid4()
    session.add(
        OutboxEvent(
            id=event_id,
            schema_version=1,
            event_type=event_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            payload_json=jsonable_encoder({**payload, "event_id": event_id, "schema_version": 1}),
        )
    )
