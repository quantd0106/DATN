from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, CheckConstraint, MetaData, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker

from app.model_registry import load_metadata


def create_sqlite_schema(engine) -> None:
    """Create an isolated copy of AI tables in SQLite without mutating Base.metadata."""
    clone = MetaData(schema=load_metadata().schema)
    for table in load_metadata().tables.values():
        copied = table.to_metadata(clone)
        for constraint in list(copied.constraints):
            if isinstance(constraint, CheckConstraint):
                sql = str(constraint.sqltext)
                if "~" in sql or "jsonb_typeof(" in sql:
                    copied.constraints.discard(constraint)
        for column in copied.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON(none_as_null=column.type.none_as_null)
            if column.server_default is not None:
                default_sql = str(getattr(column.server_default, "arg", ""))
                if "::jsonb" in default_sql or "gen_random_uuid()" in default_sql:
                    column.server_default = None
        for index in copied.indexes:
            if "postgresql" in index.dialect_options:
                index.dialect_options["sqlite"]["where"] = index.dialect_options["postgresql"].get(
                    "where"
                )
    clone.create_all(engine)


@pytest.fixture
def sqlite_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai_unit.sqlite'}",
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"ai": None}},
    )
    create_sqlite_schema(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield SimpleNamespace(
            engine=engine,
            SessionLocal=session_local,
            ping=lambda: None,
            close=engine.dispose,
        )
    finally:
        engine.dispose()
