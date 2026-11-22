from uuid import UUID

from app.common.errors import AppError
from app.integrations.core_client import AccessDecision, ClassAction, CoreClient, CurrentUser


async def require_active_teacher(
    core: CoreClient, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "LECTURER" not in user.roles:
        raise AppError(403)
    access = await core.check_access(
        class_id, user, ClassAction.PARTICIPATE_CURRENT_CLASS, correlation_id
    )
    if not access.allowed or access.class_status != "ACTIVE" or access.teaching_role is None:
        raise AppError(403)
    return access
