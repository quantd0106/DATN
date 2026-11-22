from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import DateTime, MetaData, create_engine, func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.common.errors import AppError
from app.config import Settings

SCHEMA = "ai"
MIGRATION_LOCK = 41_900_281_055


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class UUIDPrimaryKey:
    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UpdatedAt:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def database_connect_args(settings: Settings) -> dict:
    return {
        "options": "-c timezone=UTC -c search_path=public",
        "connect_timeout": settings.ai_database_connect_timeout_seconds,
    }


class Database:
    def __init__(self, settings: Settings):
        if settings.ai_database_url is None:
            raise ValueError("AI_DATABASE_URL is required")
        self.engine = create_engine(
            settings.ai_database_url.get_secret_value(),
            pool_pre_ping=True,
            hide_parameters=True,
            pool_size=settings.ai_database_pool_size,
            max_overflow=settings.ai_database_max_overflow,
            pool_timeout=settings.ai_database_pool_timeout_seconds,
            connect_args=database_connect_args(settings),
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def ping(self) -> None:
        with self.engine.connect() as connection:
            if connection.scalar(text("SELECT 1 FROM ai.alembic_version LIMIT 1")) != 1:
                raise SQLAlchemyError("AI schema has no applied migration")

    def close(self) -> None:
        self.engine.dispose()


def get_db(request: Request) -> Iterator[Session]:
    database: Database | None = request.app.state.database
    if database is None:
        raise AppError(503)
    with database.SessionLocal() as session:
        yield session


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
