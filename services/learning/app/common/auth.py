import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.common.errors import AppError
from app.integrations.core_client import CorePort, CurrentUser, Role

bearer = HTTPBearer(auto_error=False)


class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    sub: UUID
    roles: list[Role]
    session_version: int = Field(strict=True, ge=0)
    exp: int = Field(strict=True)
    iat: int = Field(strict=True)


@dataclass(frozen=True)
class AuthenticatedActor:
    user: CurrentUser
    token: str


async def get_authenticated_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedActor:
    if credentials is None:
        raise AppError(401)
    key = request.app.state.settings.core_jwt_secret
    if key is None:
        raise AppError(503)
    token = credentials.credentials
    try:
        claims = AccessClaims.model_validate(
            jwt.decode(
                token,
                key.get_secret_value(),
                algorithms=["HS256"],
                options={"require": ["sub", "roles", "session_version", "exp", "iat"]},
            )
        )
    except (jwt.InvalidTokenError, ValidationError, TypeError, OverflowError):
        raise AppError(401) from None
    if claims.exp <= claims.iat:
        raise AppError(401)
    core: CorePort = request.app.state.core
    user = await core.verify_session(token, claims.sub, request.state.correlation_id)
    if user.id != claims.sub or user.status != "ACTIVE":
        raise AppError(401)
    return AuthenticatedActor(user=user, token=token)


async def get_current_user(
    actor: Annotated[AuthenticatedActor, Depends(get_authenticated_actor)],
) -> CurrentUser:
    return actor.user


def verify_internal_service_key(
    request: Request,
    service_key: Annotated[str | None, Header(alias="X-Service-Key")] = None,
) -> None:
    _require_service_key(service_key, request.app.state.settings.learning_internal_service_secret)


def verify_analytics_service_key(
    request: Request,
    service_key: Annotated[str | None, Header(alias="X-Service-Key")] = None,
) -> None:
    _require_service_key(service_key, request.app.state.settings.learning_analytics_service_secret)


def _require_service_key(service_key: str | None, expected: SecretStr | None) -> None:
    if expected is None:
        raise AppError(503)
    if service_key is None or not secrets.compare_digest(
        service_key.encode("utf-8"), expected.get_secret_value().encode("utf-8")
    ):
        raise AppError(401)
