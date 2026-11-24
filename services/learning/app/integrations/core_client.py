from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
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
    READ_CLASS_CONTENT = "read_class_content"
    PARTICIPATE_CURRENT_CLASS = "participate_current_class"
    READ_OWN_HISTORY = "read_own_history"
    READ_DOCUMENT_SOURCE = "read_document_source"
    GRADE_SUBMITTED_WORK = "grade_submitted_work"


class CurrentUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    school_id: UUID | None
    roles: tuple[Role, ...]
    status: Literal["ACTIVE", "LOCKED", "PENDING_VERIFICATION"]
    display_name: str | None = None


class ClassInfo(BaseModel):
    id: UUID
    school_id: UUID
    name: str = ""
    status: ClassStatus
    grade_level: int = Field(strict=True, ge=6, le=12)
    teaching_role: TeachingRole | None = None
    primary_teacher_name: str | None = None


class AccessDecision(BaseModel):
    allowed: StrictBool
    class_id: UUID
    user_id: UUID
    action: ClassAction
    school_id: UUID | None = None
    class_status: ClassStatus | None = None
    teaching_role: TeachingRole | None = None


class DeadlineProjectionReceipt(BaseModel):
    event_id: UUID
    version: int


class NotificationDispatchReceipt(BaseModel):
    outbox_event_id: UUID
    deduped: StrictBool


class CorePort(Protocol):
    async def verify_session(
        self, token: str, user_id: UUID, correlation_id: str
    ) -> CurrentUser: ...

    async def get_class(
        self, class_id: UUID, user: CurrentUser, token: str, correlation_id: str
    ) -> ClassInfo: ...

    async def check_access(
        self, class_id: UUID, user: CurrentUser, action: ClassAction, correlation_id: str
    ) -> AccessDecision: ...

    async def upsert_learning_deadline(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        external_ref: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        version: int,
        activity_kind: str | None = None,
        correlation_id: str,
    ) -> DeadlineProjectionReceipt: ...

    async def publish_learning_notification(
        self,
        *,
        source_event_id: UUID,
        event_type: str,
        school_id: UUID,
        class_id: UUID,
        resource_type: str,
        resource_id: UUID,
        student_id: UUID | None,
        correlation_id: str,
    ) -> NotificationDispatchReceipt: ...


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

    async def upsert_learning_deadline(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        external_ref: str,
        title: str,
        starts_at: datetime,
        ends_at: datetime,
        version: int,
        activity_kind: str | None = None,
        correlation_id: str,
    ) -> DeadlineProjectionReceipt:
        payload: dict[str, object] = {
            "school_id": str(school_id),
            "class_id": str(class_id),
            "external_ref": external_ref,
            "title": title,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "version": version,
        }
        if activity_kind is not None:
            payload["activity_kind"] = activity_kind
        return await request_model(
            self.client,
            self.origin,
            "/internal/v1/calendar/learning-deadlines",
            DeadlineProjectionReceipt,
            method="PUT",
            headers=service_headers(self.key, correlation_id),
            payload=payload,
            expected_statuses=(200, 201),
        )

    async def publish_learning_notification(
        self,
        *,
        source_event_id: UUID,
        event_type: str,
        school_id: UUID,
        class_id: UUID,
        resource_type: str,
        resource_id: UUID,
        student_id: UUID | None,
        correlation_id: str,
    ) -> NotificationDispatchReceipt:
        receipt = await request_model(
            self.client,
            self.origin,
            "/internal/v1/notifications/learning-events",
            NotificationDispatchReceipt,
            method="POST",
            headers=service_headers(self.key, correlation_id),
            payload={
                "source_event_id": str(source_event_id),
                "event_type": event_type,
                "school_id": str(school_id),
                "class_id": str(class_id),
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "student_id": str(student_id) if student_id is not None else None,
            },
            expected_statuses=(200, 202),
        )
        if receipt.outbox_event_id != source_event_id:
            raise AppError(503)
        return receipt
