from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.common.errors import AppError, ErrorEnvelope
from app.common.logging import logger

router = APIRouter(tags=["Health"])


class LivenessResponse(BaseModel):
    status: Literal["live"] = "live"


class SchedulerSnapshot(BaseModel):
    busy: int
    slots: int
    waiting: dict[int, int]
    wait_p95_seconds: dict[int, float | None]


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"
    db: Literal["connected"] = "connected"
    scheduler: SchedulerSnapshot
    llama: Literal["connected", "disabled"]


@router.get("/health/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorEnvelope, "description": "Phụ thuộc AI chưa sẵn sàng"}},
)
async def ready(request: Request) -> ReadinessResponse:
    state = request.app.state
    try:
        if state.database is None:
            raise AppError(503)
        await run_in_threadpool(state.database.ping)
        llama = await state.runtime.probe_llama(state.http)
    except (AppError, SQLAlchemyError):
        logger.warning("dependency_unready")
        raise AppError(503) from None
    return ReadinessResponse(
        scheduler=SchedulerSnapshot.model_validate(state.runtime.scheduler.snapshot()),
        llama=llama,
    )
