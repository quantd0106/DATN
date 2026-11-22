from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel

from app.common.errors import AppError
from app.config import Settings
from app.integrations.http import request_model, service_headers


class ExplainMistakeResponse(BaseModel):
    explanation: str | None = None
    evidence: list[dict[str, Any]] = []
    fallback: bool = False


class GradeHintResponse(BaseModel):
    feedback: str
    limitations: str


class AiPort(Protocol):
    async def explain_mistake(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        grade_level: int | None,
        question: str,
        student_choice: str,
        correct_answer: str,
        misconception: str,
        approved_explanation: str,
        pinned_evidence: list[dict[str, Any]],
        correlation_id: str,
    ) -> ExplainMistakeResponse: ...

    async def grade_hint(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        assignment_title: str,
        instructions: str,
        submission_note: str,
        correlation_id: str,
    ) -> GradeHintResponse: ...


class HttpAiClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def explain_mistake(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        grade_level: int | None,
        question: str,
        student_choice: str,
        correct_answer: str,
        misconception: str,
        approved_explanation: str,
        pinned_evidence: list[dict[str, Any]],
        correlation_id: str,
    ) -> ExplainMistakeResponse:
        if self.settings.ai_mode != "http":
            return ExplainMistakeResponse(
                explanation=approved_explanation or None, evidence=[], fallback=True
            )
        try:
            # Privacy rule: never send student_id or student PII to explain_mistake.
            return await request_model(
                self.client,
                self.settings.ai_api_url,
                "/internal/v1/explain-mistake",
                ExplainMistakeResponse,
                method="POST",
                headers=service_headers(self.settings.ai_service_key, correlation_id),
                payload={
                    "school_id": str(school_id),
                    "class_id": str(class_id),
                    "grade_level": grade_level,
                    "question": question,
                    "student_choice": student_choice,
                    "correct_answer": correct_answer,
                    "misconception": misconception,
                    "approved_explanation": approved_explanation,
                    "pinned_evidence": pinned_evidence,
                },
            )
        except AppError:
            # Fault isolation QAS-01: degrade gracefully to teacher-approved explanation.
            return ExplainMistakeResponse(
                explanation=approved_explanation or None, evidence=[], fallback=True
            )

    async def grade_hint(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        assignment_title: str,
        instructions: str,
        submission_note: str,
        correlation_id: str,
    ) -> GradeHintResponse:
        if self.settings.ai_mode != "http":
            raise AppError(503, "AI_HINT_UNAVAILABLE")
        try:
            return await request_model(
                self.client,
                self.settings.ai_api_url,
                "/internal/v1/assignment/grade-hint",
                GradeHintResponse,
                method="POST",
                headers=service_headers(self.settings.ai_service_key, correlation_id),
                payload={
                    "school_id": str(school_id),
                    "class_id": str(class_id),
                    "assignment_title": assignment_title,
                    "instructions": instructions,
                    "submission_note": submission_note,
                },
            )
        except AppError:
            raise AppError(503, "AI_HINT_UNAVAILABLE") from None
