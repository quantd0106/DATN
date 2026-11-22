from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.common.api import ERROR_RESPONSES
from app.common.auth import verify_analytics_service_key
from app.common.errors import AppError
from app.modules.analytics_export.service import TopicCount, TopicParticipation, summarize_topics

router = APIRouter(
    prefix="/internal/v1",
    tags=["Tutor analytics"],
    dependencies=[Depends(verify_analytics_service_key)],
    responses=ERROR_RESPONSES,
)


class TopicRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: UUID
    class_id: UUID
    student_id: UUID
    topic_id: UUID


class TopicSummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    school_id: UUID
    rows: list[TopicRow] = Field(max_length=20000)


@router.post("/classes/{class_id}/tutor/topic-summary")
def export(class_id: UUID, body: TopicSummaryRequest) -> tuple[TopicCount, ...]:
    try:
        return summarize_topics(
            (TopicParticipation(**row.model_dump()) for row in body.rows),
            class_id=class_id,
            school_id=body.school_id,
        )
    except ValueError:
        raise AppError(422) from None
