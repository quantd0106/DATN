from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.common.core_client import CoreClient, CoreUser, class_access, require_class_access
from app.common.errors import AppError
from app.modules.grants.repository import grant_for_document, grants_for_document
from app.modules.grants.schemas import ClassDocumentGrantDTO
from app.modules.library.public import find_document
from app.modules.versions.public import get_serving_version


def list_document_grants(
    session: Session, document_id: UUID, school_id: UUID, serving_version_number: int | None
) -> list[ClassDocumentGrantDTO]:
    return [
        ClassDocumentGrantDTO.model_validate(grant).model_copy(
            update={"serving_version_number": serving_version_number}
        )
        for grant in grants_for_document(session, document_id, school_id)
    ]


def deactivate_document_grants(
    session: Session, document_id: UUID, school_id: UUID, actor_id: UUID, revoked_at: datetime
) -> list[UUID]:
    affected = grants_for_document(session, document_id, school_id, active_only=True)
    for grant in affected:
        grant.is_active = False
        grant.revoked_at = revoked_at
        grant.revoked_by_user_id = actor_id
    return [grant.class_id for grant in affected]


def require_live_source(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    class_id: UUID,
    document_id: UUID,
    *,
    lock: bool = False,
):
    require_class_access(core, actor, class_id, "read_document_source")
    document = find_document(session, document_id, actor.school_id, lock=lock)
    if (
        document is None
        or grant_for_document(
            session, school_id=document.school_id, class_id=class_id, document_id=document_id
        )
        is None
    ):
        raise AppError(404)
    serving = get_serving_version(session, document_id)
    if serving is None:
        raise AppError(404)
    return serving


def is_source_accessible(
    session: Session, core: CoreClient, actor: CoreUser, class_id: UUID, document_id: UUID
) -> bool:
    if actor.school_id is None:
        return False
    access = class_access(core, actor, class_id, "read_document_source")
    if not access.allowed or access.class_status is None:
        return False
    return (
        find_document(session, document_id, actor.school_id) is not None
        and grant_for_document(
            session, school_id=actor.school_id, class_id=class_id, document_id=document_id
        )
        is not None
        and get_serving_version(session, document_id) is not None
    )
