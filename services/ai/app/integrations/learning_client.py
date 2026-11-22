from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, StrictBool

from app.common.errors import AppError
from app.config import Settings
from app.integrations.http import request_model, service_headers


class AssessmentDecision(BaseModel):
    user_id: UUID
    school_id: UUID
    active_official_assessment: StrictBool


class QuizExportReceipt(BaseModel):
    class_id: UUID
    school_id: UUID
    idempotency_key: UUID
    learning_item_id: UUID


class LearningPort(Protocol):
    async def require_tutor_available(
        self, user_id: UUID, school_id: UUID, correlation_id: str
    ) -> None: ...

    async def export_quiz_item(
        self,
        class_id: UUID,
        school_id: UUID,
        draft_id: UUID,
        approved_payload: dict,
        correlation_id: str,
    ) -> QuizExportReceipt: ...


class HttpLearningClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    def _headers(self, correlation_id: str) -> dict[str, str]:
        if self.settings.learning_mode != "http":
            raise AppError(503)
        return service_headers(self.settings.learning_service_key, correlation_id)

    async def require_tutor_available(
        self, user_id: UUID, school_id: UUID, correlation_id: str
    ) -> None:
        decision = await request_model(
            self.client,
            self.settings.learning_api_url,
            self.settings.learning_active_assessment_path,
            AssessmentDecision,
            params={"user_id": str(user_id), "school_id": str(school_id)},
            headers=self._headers(correlation_id),
        )
        if (decision.user_id, decision.school_id) != (user_id, school_id):
            raise AppError(503)
        if decision.active_official_assessment:
            raise AppError(403)

    async def export_quiz_item(
        self,
        class_id: UUID,
        school_id: UUID,
        draft_id: UUID,
        approved_payload: dict,
        correlation_id: str,
    ) -> QuizExportReceipt:
        headers = self._headers(correlation_id)
        receipt = await request_model(
            self.client,
            self.settings.learning_api_url,
            self.settings.learning_quiz_export_path,
            QuizExportReceipt,
            headers={**headers, "Idempotency-Key": str(draft_id)},
            method="POST",
            payload={
                **approved_payload,
                "class_id": str(class_id),
                "school_id": str(school_id),
                "idempotency_key": str(draft_id),
            },
            expected_statuses=(200, 201),
        )
        if (receipt.class_id, receipt.school_id, receipt.idempotency_key) != (
            class_id,
            school_id,
            draft_id,
        ):
            raise AppError(503)
        return receipt
