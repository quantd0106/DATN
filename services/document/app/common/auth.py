from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.common.config import get_settings
from app.common.core_client import Core, CoreUser
from app.common.errors import AppError

bearer = HTTPBearer(auto_error=False)
service_key = APIKeyHeader(name="X-Service-Key", auto_error=False)
DOCUMENT_SCOPES = frozenset({"document:read_grants", "document:read_canonical"})


def get_current_user(
    core: Core, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]
) -> CoreUser:
    if credentials is None:
        raise AppError(401)
    user = core.introspect_token(credentials.credentials)
    if user.status != "ACTIVE":
        raise AppError(401)
    return user


Actor = Annotated[CoreUser, Depends(get_current_user)]


def require_lecturer(actor: Actor) -> CoreUser:
    if actor.status != "ACTIVE":
        raise AppError(401)
    if "LECTURER" not in actor.roles or actor.school_id is None:
        raise AppError(403)
    return actor


Lecturer = Annotated[CoreUser, Depends(require_lecturer)]


def verify_internal_service_key(
    key: Annotated[str | None, Security(service_key)],
) -> frozenset[str]:
    secret = get_settings().internal_service_secret
    if secret is None or not secret.get_secret_value():
        raise AppError(503)
    if key is None or not compare_digest(
        key.encode("utf-8"), secret.get_secret_value().encode("utf-8")
    ):
        raise AppError(401)
    return DOCUMENT_SCOPES


InternalScopes = Annotated[frozenset[str], Depends(verify_internal_service_key)]
