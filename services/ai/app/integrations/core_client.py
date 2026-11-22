from enum import StrEnum
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.common.errors import AppError
from app.config import Settings
from app.integrations.http import request_model, service_headers

Role = Literal["STUDENT", "LECTURER", "ADMIN"]
ClassStatus = Literal["ACTIVE", "ARCHIVED"]
TeachingRole = Literal["PRIMARY", "ASSISTING"]


class ClassAction(StrEnum):
    USE_AI_TUTOR = "use_ai_tutor"
    READ_OWN_HISTORY = "read_own_history"
    READ_DOCUMENT_SOURCE = "read_document_source"
    PARTICIPATE_CURRENT_CLASS = "participate_current_class"


class CurrentUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    school_id: UUID | None
    roles: tuple[Role, ...]
    status: Literal["ACTIVE", "LOCKED", "PENDING_VERIFICATION"]


class ClassInfo(BaseModel):
    id: UUID
    school_id: UUID
    status: ClassStatus
    grade_level: int = Field(strict=True, ge=6, le=12)
    teaching_role: TeachingRole | None = None


class AccessDecision(BaseModel):
    allowed: StrictBool
    class_id: UUID
    user_id: UUID
    action: ClassAction
    school_id: UUID | None = None
    class_status: ClassStatus | None = None
    teaching_role: TeachingRole | None = None


class CoreClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.origin = settings.core_api_url
        self.key = settings.core_service_key

    async def verify_session(self, token: str, user_id: UUID, correlation_id: str) -> CurrentUser:
        user = await request_model(
            self.client,
            self.origin,
            "/api/v1/users/me",
            CurrentUser,
            headers={"Authorization": f"Bearer {token}", "X-Correlation-ID": correlation_id},
            public_core=True,
        )
        if user.id != user_id or user.status != "ACTIVE":
            raise AppError(401)
        return user

    async def get_class(
        self, class_id: UUID, user: CurrentUser, token: str, correlation_id: str
    ) -> ClassInfo:
        result = await request_model(
            self.client,
            self.origin,
            f"/api/v1/classes/{class_id}",
            ClassInfo,
            headers={"Authorization": f"Bearer {token}", "X-Correlation-ID": correlation_id},
            public_core=True,
        )
        if result.id != class_id or result.school_id != user.school_id:
            raise AppError(404)
        return result

    async def check_access(
        self, class_id: UUID, user: CurrentUser, action: ClassAction, correlation_id: str
    ) -> AccessDecision:
        if user.school_id is None or user.status != "ACTIVE":
            raise AppError(404)
        decision = await request_model(
            self.client,
            self.origin,
            f"/internal/v1/classes/{class_id}/access",
            AccessDecision,
            params={"user_id": str(user.id), "action": action.value},
            headers=service_headers(self.key, correlation_id),
        )
        if (decision.class_id, decision.user_id, decision.action) != (class_id, user.id, action):
            raise AppError(503)
        if decision.allowed and (
            decision.school_id != user.school_id or decision.class_status is None
        ):
            raise AppError(503)
        return decision
