from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import JSON, DateTime, Engine, MetaData, Uuid, create_engine, func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.common.errors import AppError
from app.config import Settings

SCHEMA = "learning"
MIGRATION_LOCK = 41_900_281_059
SCHEMA_HEAD = "0004_grades_appeals_adjustments"

JSONBType = JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class UUIDPrimaryKey:
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class UpdatedAt:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def database_connect_args(settings: Settings) -> dict:
    return {
        "options": "-c timezone=UTC -c search_path=public",
        "connect_timeout": settings.learning_database_connect_timeout_seconds,
    }


class Database:
    def __init__(self, settings: Settings | None = None, *, engine: Engine | None = None):
        if engine is not None:
            self.engine = engine
            self._is_sqlite = engine.dialect.name == "sqlite"
        else:
            if settings is None or settings.learning_database_url is None:
                raise ValueError("LEARNING_DATABASE_URL is required")
            self.engine = create_engine(
                settings.learning_database_url.get_secret_value(),
                pool_pre_ping=True,
                hide_parameters=True,
                pool_size=settings.learning_database_pool_size,
                max_overflow=settings.learning_database_max_overflow,
                pool_timeout=settings.learning_database_pool_timeout_seconds,
                connect_args=database_connect_args(settings),
            )
            self._is_sqlite = False
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def ping(self) -> None:
        with self.engine.connect() as connection:
            if self._is_sqlite:
                connection.scalar(text("SELECT 1"))
                return
            revisions = connection.scalars(text("SELECT version_num FROM learning.alembic_version")).all()
            if revisions != [SCHEMA_HEAD]:
                raise SQLAlchemyError("Learning schema is not at its expected revision")

    def close(self) -> None:
        self.engine.dispose()


def get_db(request: Request) -> Iterator[Session]:
    database: Database | None = request.app.state.database
    if database is None:
        raise AppError(503)
    with database.SessionLocal() as session:
        yield session


def get_database(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise AppError(503)
    return database


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise


@contextmanager
def write_transaction(session: Session) -> Iterator[Session]:
    with transaction(session):
        if session.get_bind().dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE. Reserve its writer lock before reading state.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield session
