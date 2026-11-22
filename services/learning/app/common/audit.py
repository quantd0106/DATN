from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, Uuid
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.db import Base, CreatedAt, JSONBType, UUIDPrimaryKey

ADMIN_REASON_CODES = frozenset({"ADMIN_INTERVENTION", "POLICY_VIOLATION", "USER_REQUEST"})


class AuditEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("idx_learning_audit_scope", "school_id", "class_id", "created_at"),)

    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_roles_json: Mapped[list[str]] = mapped_column(JSONBType, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)


def record_audit_event(
    session: Session,
    *,
    correlation_id: UUID | str,
    school_id: UUID,
    class_id: UUID | None,
    actor_id: UUID,
    actor_roles: list[str] | tuple[str, ...],
    action: str,
    target_type: str,
    target_id: UUID | str,
    reason_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    # Rule K5: never record raw student answers or private chat text in audit metadata.
    row = AuditEvent(
        correlation_id=UUID(str(correlation_id)),
        school_id=school_id,
        class_id=class_id,
        actor_id=actor_id,
        actor_roles_json=list(actor_roles),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        reason_code=reason_code,
        metadata_json=metadata or {},
    )
    session.add(row)
    session.flush()
    return row
