from alembic import context
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection

from app.common.config import get_settings
from app.model_registry import Base

config = context.config
schema = "document"
target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    return type_ != "schema" or name == schema


def run_migrations_offline(url: str) -> None:
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, version_table_schema=schema
    )
    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS document")
        context.run_migrations()


def run_migrations_online(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline(config.attributes.get("database_url") or get_settings().database_url)
elif (connection := config.attributes.get("connection")) is not None:
    run_migrations_online(connection)
else:
    engine = create_engine(
        get_settings().database_url, poolclass=pool.NullPool, hide_parameters=True
    )
    try:
        with engine.connect() as connection:
            run_migrations_online(connection)
    finally:
        engine.dispose()
