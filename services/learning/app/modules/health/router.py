from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from app.common.errors import AppError
from app.common.logging import logger

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live")
async def health_live() -> dict:
    return {"status": "live"}


@router.get("/ready")
async def health_ready(request: Request) -> dict:
    database = request.app.state.database
    if database is None:
        logger.error("dependency_unready")
        raise AppError(503)
    try:
        database.ping()
    except SQLAlchemyError:
        logger.error("dependency_unready")
        raise AppError(503) from None
    return {"status": "ready", "db": "connected", "alembic_head": True}
