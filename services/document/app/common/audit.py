from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.db import JSON_OBJECT, Base, UTCDateTime


class AuditEvent(Base):
    __tablename__ = "audit_events"

    school_id: Mapped[UUID]
    actor_id: Mapped[UUID]
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[UUID]
    correlation_id: Mapped[UUID]
    metadata_json: Mapped[dict] = mapped_column(JSON_OBJECT)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class AuditMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID | None = None
    version_number: int | None = None
    active_grant_count: int | None = None


def record_audit(
    session: Session,
    *,
    school_id: UUID,
    actor_id: UUID,
    action: str,
    target_type: str,
    target_id: UUID,
    correlation_id: UUID,
    metadata: AuditMetadata | None = None,
) -> None:
    session.add(
        AuditEvent(
            school_id=school_id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            correlation_id=correlation_id,
            metadata_json=(
                metadata.model_dump(mode="json", exclude_none=True) if metadata is not None else {}
            ),
        )
    )
