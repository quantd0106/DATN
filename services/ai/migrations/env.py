from alembic import context
from sqlalchemy import create_engine, pool, text

from app.common.db import MIGRATION_LOCK, SCHEMA, database_connect_args
from app.config import Settings
from app.model_registry import load_metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == SCHEMA
    if type_ == "table":
        return parent_names["schema_name"] == SCHEMA
    return True


options = {
    "target_metadata": load_metadata(),
    "version_table_schema": SCHEMA,
    "include_schemas": True,
    "include_name": include_name,
    "compare_type": True,
}

settings = Settings()
if settings.ai_database_url is None:
    raise RuntimeError("AI_DATABASE_URL is required for migrations")
url = settings.ai_database_url.get_secret_value()


def run_offline() -> None:
    context.configure(url=url, literal_binds=True, **options)
    with context.begin_transaction():
        if context.get_starting_revision_argument() in (None, "base"):
            context.execute("CREATE SCHEMA IF NOT EXISTS ai")
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(
        url,
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args=database_connect_args(settings),
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
            # A pre-provisioned schema does not need database-level CREATE privileges.
            exists = connection.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = 'ai'"))
            if not exists:
                connection.execute(text("CREATE SCHEMA IF NOT EXISTS ai"))
            context.configure(connection=connection, **options)
            context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
