from uuid import UUID

from app.common.audit import ADMIN_REASON_CODES
from app.common.errors import AppError
from app.integrations.core_client import AccessDecision, ClassAction, CorePort, CurrentUser


async def require_active_teacher(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "LECTURER" not in user.roles:
        raise AppError(403)
    decision = await core.check_access(
        class_id, user, ClassAction.PARTICIPATE_CURRENT_CLASS, correlation_id
    )
    if not decision.allowed:
        if decision.class_status == "ARCHIVED":
            raise AppError(409, "CLASS_ARCHIVED")
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    if decision.class_status != "ACTIVE" or decision.teaching_role is None:
        raise AppError(403)
    return decision


async def require_primary_teacher(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    decision = await require_active_teacher(core, user, class_id, correlation_id)
    if decision.teaching_role != "PRIMARY":
        raise AppError(403, "PRIMARY_APPROVAL_REQUIRED")
    return decision


async def require_class_teacher_read(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "LECTURER" not in user.roles:
        raise AppError(403)
    decision = await core.check_access(
        class_id, user, ClassAction.READ_CLASS_CONTENT, correlation_id
    )
    if not decision.allowed or decision.teaching_role is None:
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    return decision


async def require_active_student(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "STUDENT" not in user.roles:
        raise AppError(403)
    decision = await core.check_access(
        class_id, user, ClassAction.PARTICIPATE_CURRENT_CLASS, correlation_id
    )
    if not decision.allowed:
        if decision.class_status == "ARCHIVED":
            raise AppError(409, "CLASS_ARCHIVED")
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    if decision.class_status != "ACTIVE" or decision.teaching_role is not None:
        raise AppError(403)
    return decision


async def require_class_reader(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    decision = await core.check_access(
        class_id, user, ClassAction.READ_CLASS_CONTENT, correlation_id
    )
    if not decision.allowed:
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    return decision


async def require_own_history(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "STUDENT" not in user.roles:
        raise AppError(403)
    decision = await core.check_access(
        class_id, user, ClassAction.READ_OWN_HISTORY, correlation_id
    )
    if not decision.allowed:
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    return decision


async def require_grade_submitted_work(
    core: CorePort, user: CurrentUser, class_id: UUID, correlation_id: str
) -> AccessDecision:
    if "LECTURER" not in user.roles:
        raise AppError(403)
    decision = await core.check_access(
        class_id, user, ClassAction.GRADE_SUBMITTED_WORK, correlation_id
    )
    if not decision.allowed or decision.teaching_role is None:
        if decision.class_status == "ARCHIVED":
            raise AppError(409, "CLASS_ARCHIVED")
        if decision.class_status is None:
            raise AppError(404)
        raise AppError(403)
    return decision


def require_admin_reason(user: CurrentUser, reason_header: str | None) -> str:
    if "ADMIN" not in user.roles or user.school_id is None:
        raise AppError(403)
    if reason_header not in ADMIN_REASON_CODES:
        raise AppError(400, "ADMIN_REASON_REQUIRED")
    return reason_header
