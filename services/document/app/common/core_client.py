from collections.abc import Iterator
from typing import Annotated, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import Depends
from pydantic import BaseModel, StrictBool, ValidationError

from app.common.config import Settings, get_settings
from app.common.errors import AppError

ClassAction = Literal["manage_class_documents", "read_document_source", "read_own_history"]


class CoreUser(BaseModel):
    id: UUID
    status: str
    roles: list[str]
    school_id: UUID | None
    display_name: str | None = None


class ClassAccess(BaseModel):
    allowed: StrictBool
    class_id: UUID
    school_id: UUID | None = None
    class_status: Literal["ACTIVE", "ARCHIVED"] | None = None
    teaching_role: Literal["PRIMARY", "ASSISTING"] | None = None


class CoreClient(Protocol):
    def introspect_token(self, token: str) -> CoreUser: ...

    def check_class_access(
        self, class_id: UUID, user_id: UUID, action: ClassAction
    ) -> ClassAccess: ...


class HttpCoreClient:
    def __init__(self, config: Settings, client: httpx.Client):
        self.config = config
        self.client = client

    def _request(self, method: str, path: str, **kwargs) -> dict:
        secret = self.config.internal_service_secret
        if not self.config.core_api_url or not secret or not secret.get_secret_value():
            raise AppError(503)
        try:
            response = self.client.request(
                method,
                self.config.core_api_url.rstrip("/") + path,
                headers={"X-Service-Key": secret.get_secret_value()},
                timeout=self.config.core_client_timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            return payload
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError) as exc:
            raise AppError(503) from exc

    def introspect_token(self, token: str) -> CoreUser:
        result = self._request("POST", "/internal/v1/auth/introspect", json={"token": token})
        if result.get("active") is not True:
            raise AppError(401)
        try:
            user = CoreUser.model_validate(result.get("user"))
        except ValidationError as exc:
            raise AppError(503) from exc
        if user.status != "ACTIVE":
            raise AppError(401)
        return user

    def check_class_access(self, class_id: UUID, user_id: UUID, action: ClassAction) -> ClassAccess:
        result = self._request(
            "GET",
            f"/internal/v1/classes/{class_id}/access",
            params={"user_id": str(user_id), "action": action},
        )
        try:
            decision = ClassAccess.model_validate(result)
            if (
                decision.class_id != class_id
                or UUID(result["user_id"]) != user_id
                or result["action"] != action
                or (decision.allowed and (decision.class_status is None or not decision.school_id))
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise AppError(503) from exc
        return decision


def get_core_client() -> Iterator[CoreClient]:
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        yield HttpCoreClient(get_settings(), client)


Core = Annotated[CoreClient, Depends(get_core_client)]


def class_access(
    core: CoreClient, actor: CoreUser, class_id: UUID, action: ClassAction
) -> ClassAccess:
    if actor.school_id is None:
        raise AppError(404)
    decision = core.check_class_access(class_id, actor.id, action)
    if decision.allowed and decision.school_id != actor.school_id:
        raise AppError(404)
    return decision


def require_class_access(
    core: CoreClient, actor: CoreUser, class_id: UUID, action: ClassAction
) -> ClassAccess:
    decision = class_access(core, actor, class_id, action)
    if decision.class_status is None:
        raise AppError(404)
    if decision.class_status == "ARCHIVED" and action == "manage_class_documents":
        raise AppError(409, "Lớp học đã lưu trữ.")
    if not decision.allowed:
        raise AppError(403)
    return decision
