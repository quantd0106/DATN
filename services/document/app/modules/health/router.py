from fastapi import APIRouter
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.common.db import DOCUMENT_SCHEMA, Db
from app.common.errors import AppError
from app.common.storage import Storage

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
def ready(session: Db, storage: Storage) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
        connection = session.connection()
        if connection.dialect.name == "postgresql":
            usable = session.scalar(
                text(
                    "SELECT has_schema_privilege(current_user, oid, 'USAGE') "
                    "FROM pg_namespace WHERE nspname = :schema"
                ),
                {"schema": DOCUMENT_SCHEMA},
            )
        else:
            schema_map = connection.get_execution_options().get("schema_translate_map", {})
            usable = inspect(connection).has_table("documents", schema_map.get(DOCUMENT_SCHEMA))
        if not usable or not storage.check_ready():
            raise AppError(503)
    except (SQLAlchemyError, OSError) as exc:
        raise AppError(503) from exc
    return {"status": "ready", "db": "connected", "storage": "ready"}
