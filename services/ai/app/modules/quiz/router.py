from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from rag.generation.tasks.quiz.blueprint import MAX_ITEMS_PER_JOB, parse_quiz_spec

from app.common.api import ERROR_RESPONSES, Actor, ApiServices, Credentials
from app.common.class_access import require_active_teacher
from app.common.errors import AppError
from app.integrations.learning_client import QuizExportReceipt
from app.modules.quiz.models import DraftStatus, JobStatus
from app.modules.quiz.service import (
    enqueue_job,
    export_draft,
    get_job,
    list_drafts,
    list_jobs,
    propose_quiz,
    regenerate_draft,
    review_draft,
    run_queued_job,
)

router = APIRouter(prefix="/api/v1/classes/{class_id}", tags=["Quiz"], responses=ERROR_RESPONSES)
JobLimit = Annotated[int, Query(ge=1, le=50)]
Section = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
SectionPath = Annotated[list[Section], Field(min_length=1, max_length=12)]
ItemType = Literal["single_choice", "multiple_choice", "true_false_4", "short_answer"]
Level = Literal["biet", "hieu", "van_dung"]


class QuizInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionScope(QuizInput):
    document_version_id: UUID
    section_paths: list[SectionPath] = Field(default_factory=list, max_length=100)


class MatrixCell(QuizInput):
    section: Section | SectionPath
    type: ItemType
    level: Level
    count: int = Field(strict=True, ge=1, le=MAX_ITEMS_PER_JOB)


class QuizOptions(QuizInput):
    distractor_count: int = Field(default=3, strict=True, ge=2, le=4)
    option_count: int = Field(default=4, strict=True, ge=4, le=5)
    explanations: bool = Field(default=True, strict=True)
    parallel_forms: int = Field(default=1, strict=True, ge=1, le=2)


class QuizScopeRequest(QuizInput):
    purpose: Literal["PRACTICE", "ASSESSMENT_DRAFT"] = "PRACTICE"
    subject: str = Field(default="", max_length=80)
    language: Literal["vi", "en"] = "vi"
    scope: list[VersionScope] = Field(min_length=1, max_length=100)
    options: QuizOptions = Field(default_factory=QuizOptions)


class ProposalRequest(QuizScopeRequest):
    total: int = Field(strict=True, ge=1, le=MAX_ITEMS_PER_JOB)


class QuizJobRequest(QuizScopeRequest):
    matrix: list[MatrixCell] = Field(min_length=1, max_length=MAX_ITEMS_PER_JOB)


class JobCreated(BaseModel):
    id: UUID
    status: Literal["QUEUED"] = "QUEUED"


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    class_id: UUID
    status: JobStatus
    completed_items: int
    total_items: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class DraftView(BaseModel):
    id: UUID
    slot_id: str
    status: DraftStatus
    level: str
    item: dict | None
    review_reason: str | None
    learning_item_id: UUID | None


class ReviewRequest(QuizInput):
    decision: Literal["accept", "edit", "reject", "regenerate"]
    reason: str | None = Field(default=None, max_length=128)
    edited_item: dict | None = None

    @model_validator(mode="after")
    def validate_edit(self):
        if (self.decision == "edit") != (self.edited_item is not None):
            raise ValueError("Provide edited_item with the edit decision")
        return self


class ReviewResult(BaseModel):
    status: DraftStatus


async def _spec_data(
    class_id: UUID,
    body: QuizScopeRequest,
    user: Actor,
    credentials: Credentials,
    api: ApiServices,
) -> dict:
    await require_active_teacher(api.core, user, class_id, api.correlation_id)
    klass = await api.core.get_class(class_id, user, credentials.credentials, api.correlation_id)
    return {
        **body.model_dump(mode="json", exclude={"total"}),
        "class_id": str(class_id),
        "grade": klass.grade_level,
    }


@router.post("/quiz/proposals")
async def propose(
    class_id: UUID,
    body: ProposalRequest,
    user: Actor,
    credentials: Credentials,
    api: ApiServices,
) -> list[MatrixCell]:
    spec = parse_quiz_spec(await _spec_data(class_id, body, user, credentials, api))
    proposed = await propose_quiz(
        api.runtime,
        api.require_db(),
        api.core,
        api.document,
        user,
        class_id,
        spec,
        body.total,
        api.correlation_id,
    )
    return [MatrixCell.model_validate(row, from_attributes=True) for row in proposed]


async def _run_job(api: ApiServices, user: Actor, class_id: UUID, job_id: UUID) -> None:
    try:
        await run_queued_job(
            api.runtime,
            api.require_db(),
            api.core,
            api.document,
            user,
            class_id,
            job_id,
            api.correlation_id,
        )
    except AppError:
        # The runner records terminal status for the job. The response is already sent.
        return


@router.post("/quiz/jobs", status_code=202)
async def submit_job(
    class_id: UUID,
    body: QuizJobRequest,
    background: BackgroundTasks,
    user: Actor,
    credentials: Credentials,
    api: ApiServices,
) -> JobCreated:
    spec = await _spec_data(class_id, body, user, credentials, api)
    version_ids = [str(entry.document_version_id) for entry in body.scope]
    job_id = await enqueue_job(
        api.require_db(),
        api.core,
        api.document,
        user,
        class_id,
        spec,
        api.correlation_id,
        fact_cache=api.runtime.fact_cache_for(version_ids),
    )
    background.add_task(_run_job, api, user, class_id, job_id)
    return JobCreated(id=job_id)


@router.get("/quiz/jobs")
async def list_class_jobs(
    class_id: UUID, user: Actor, api: ApiServices, limit: JobLimit = 20
) -> list[JobView]:
    rows = await list_jobs(
        api.require_db(), api.core, user, class_id, api.correlation_id, limit=limit
    )
    return [JobView.model_validate(row) for row in rows]


@router.get("/quiz/jobs/{job_id}")
async def job_status(class_id: UUID, job_id: UUID, user: Actor, api: ApiServices) -> JobView:
    job = await get_job(api.require_db(), api.core, user, class_id, job_id, api.correlation_id)
    return JobView.model_validate(job)


@router.get("/quiz/jobs/{job_id}/items")
async def draft_items(
    class_id: UUID, job_id: UUID, user: Actor, api: ApiServices
) -> list[DraftView]:
    rows = await list_drafts(
        api.require_db(),
        api.core,
        api.document,
        user,
        class_id,
        job_id,
        api.correlation_id,
    )
    return [DraftView.model_validate(item) for item in rows]


@router.post("/quiz/items/{draft_id}/review")
async def review_item(
    class_id: UUID,
    draft_id: UUID,
    body: ReviewRequest,
    user: Actor,
    api: ApiServices,
) -> ReviewResult:
    status = await review_draft(
        api.require_db(),
        api.core,
        api.document,
        user,
        class_id,
        draft_id,
        body.decision,
        api.correlation_id,
        reason=body.reason,
        edited_item=body.edited_item,
    )
    return ReviewResult(status=status)


@router.post("/quiz/items/{draft_id}/regenerate")
async def regenerate_item(
    class_id: UUID,
    draft_id: UUID,
    user: Actor,
    api: ApiServices,
) -> DraftView:
    item = await regenerate_draft(
        api.runtime,
        api.require_db(),
        api.core,
        api.document,
        user,
        class_id,
        draft_id,
        api.correlation_id,
    )
    return DraftView.model_validate(item)


@router.post("/quiz/items/{draft_id}/export")
async def export_item(
    class_id: UUID,
    draft_id: UUID,
    user: Actor,
    api: ApiServices,
) -> QuizExportReceipt:
    return await export_draft(
        api.require_db(),
        api.core,
        api.document,
        api.learning,
        user,
        class_id,
        draft_id,
        api.correlation_id,
    )
