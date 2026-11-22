import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.common.db import Base, CreatedAt, JSONBType, ensure_utc, utcnow
from app.common.errors import AppError


class IdempotencyRecord(CreatedAt, Base):
    __tablename__ = "idempotency_records"

    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body_json: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def payload_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def lookup_idempotency(session: Session, scope_key: str, req_hash: str) -> IdempotencyRecord | None:
    record = session.scalar(
        select(IdempotencyRecord).where(IdempotencyRecord.scope_key == scope_key)
    )
    if record is None:
        return None
    if ensure_utc(record.expires_at) <= utcnow():
        session.delete(record)
        session.flush()
        return None
    if record.request_hash != req_hash:
        raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
    return record


def store_idempotency(
    session: Session,
    scope_key: str,
    req_hash: str,
    status_code: int,
    response_body: dict[str, Any],
    *,
    ttl_hours: int = 24,
) -> IdempotencyRecord:
    record = IdempotencyRecord(
        scope_key=scope_key,
        request_hash=req_hash,
        status_code=status_code,
        response_body_json=response_body,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    session.add(record)
    session.flush()
    return record
