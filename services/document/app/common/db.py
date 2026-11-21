from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends
from sqlalchemy import JSON, DateTime, MetaData, create_engine, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.common.config import get_settings

DOCUMENT_SCHEMA = "document"
JSON_OBJECT = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return None


class Base(DeclarativeBase):
    metadata = MetaData(schema=DOCUMENT_SCHEMA)

    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class UpdatedAt(CreatedAt):
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


database_url = make_url(get_settings().database_url)
connect_args = (
    {"connect_timeout": 3, "options": "-c timezone=UTC"}
    if database_url.get_backend_name() == "postgresql"
    else {}
)
engine = create_engine(
    database_url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


Db = Annotated[Session, Depends(get_db)]


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
