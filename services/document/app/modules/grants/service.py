from uuid import UUID

from sqlalchemy.orm import Session

from app.common.audit import AuditMetadata, record_audit
from app.common.core_client import CoreClient, CoreUser, class_access, require_class_access
from app.common.db import transaction, utc_now
from app.common.errors import AppError
from app.common.outbox import record_outbox_event
from app.common.pagination import Page, Pagination
from app.common.storage import StorageBackend
from app.modules.grants.models import ClassDocumentGrant
from app.modules.grants.repository import active_document_keys, grant_for_document
from app.modules.grants.schemas import (
    ActiveGrantItem,
    ActiveGrantsResponse,
    ClassDocumentGrantDTO,
    ClassDocumentSummaryDTO,
    DocumentReaderDTO,
    ReaderDocumentDTO,
)
from app.modules.library.public import (
    find_document,
    is_document_live,
    require_owned_document,
    shared_documents,
)
from app.modules.notes.public import bookmark_version, reader_notes
from app.modules.versions.public import (
    get_serving_version,
    get_version_by_id,
    get_version_by_number,
    load_canonical_artifact,
    load_reader_projection,
    select_viewing_version,
)


def record_grant_change(
    session: Session, grant: ClassDocumentGrant, actor: CoreUser, correlation_id: UUID
) -> None:
    record_audit(
        session,
        school_id=grant.school_id,
        actor_id=actor.id,
        action="DOCUMENT_GRANTED" if grant.is_active else "DOCUMENT_GRANT_REVOKED",
        target_type="document",
        target_id=grant.document_id,
        correlation_id=correlation_id,
        metadata=AuditMetadata(class_id=grant.class_id),
    )
    # This event invalidates a class snapshot. Consumers re-read the current active set.
    record_outbox_event(
        session,
        "document.grant.changed",
        grant.class_id,
        1,
        {
            "school_id": grant.school_id,
            "class_id": grant.class_id,
            "document_id": grant.document_id,
            "is_active": grant.is_active,
            "changed_at": grant.granted_at if grant.is_active else grant.revoked_at,
        },
    )


def grant_document(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    class_id: UUID,
    document_id: UUID,
    correlation_id: UUID,
) -> tuple[ClassDocumentGrantDTO, bool]:
    with transaction(session):
        require_class_access(core, actor, class_id, "manage_class_documents")
        document = require_owned_document(session, document_id, actor, lock=True)
        serving = get_serving_version(session, document_id)
        if serving is None:
            raise AppError(409, "Tài liệu chưa có phiên bản sẵn sàng.")
        grant = grant_for_document(
            session, school_id=document.school_id, class_id=class_id, document_id=document_id
        )
        if grant is not None:
            dto = ClassDocumentGrantDTO.model_validate(grant)
            dto.serving_version_number = serving.version_number
            return dto, False

        grant = grant_for_document(
            session,
            school_id=document.school_id,
            class_id=class_id,
            document_id=document_id,
            active_only=False,
        )
        if grant is None:
            grant = ClassDocumentGrant(
                school_id=document.school_id, class_id=class_id, document_id=document_id
            )
            session.add(grant)
        grant.is_active = True
        grant.granted_at = utc_now()
        grant.granted_by_user_id = actor.id
        grant.revoked_at = None
        grant.revoked_by_user_id = None
        session.flush()
        record_grant_change(session, grant, actor, correlation_id)
        dto = ClassDocumentGrantDTO.model_validate(grant)
        dto.serving_version_number = serving.version_number
        return dto, True


def revoke_grant(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    class_id: UUID,
    document_id: UUID,
    correlation_id: UUID,
) -> None:
    with transaction(session):
        require_class_access(core, actor, class_id, "manage_class_documents")
        document = find_document(
            session, document_id, actor.school_id, include_deleted=True, lock=True
        )
        if document is None:
            raise AppError(404)
        grant = grant_for_document(
            session, school_id=document.school_id, class_id=class_id, document_id=document_id
        )
        if grant is None:
            previous = grant_for_document(
                session,
                school_id=document.school_id,
                class_id=class_id,
                document_id=document_id,
                active_only=False,
            )
            if previous is None:
                raise AppError(404)
            return
        grant.is_active = False
        grant.revoked_at = utc_now()
        grant.revoked_by_user_id = actor.id
        record_grant_change(session, grant, actor, correlation_id)


def class_summary(document, serving) -> ClassDocumentSummaryDTO:
    return ClassDocumentSummaryDTO(
        id=document.id,
        school_id=document.school_id,
        owner_user_id=document.owner_user_id,
        owner_display_name=document.owner_display_name,
        title=document.title,
        description=document.description,
        format=serving.file_type,
        created_at=document.created_at,
        updated_at=document.updated_at,
        current_version=serving.version_number,
        serving_version=serving.version_number,
        page_count=serving.page_count,
    )


def list_class_documents(
    session: Session,
    core: CoreClient,
    actor: CoreUser,
    class_id: UUID,
    pagination: Pagination,
    file_format: str | None,
    search: str | None,
) -> Page[ClassDocumentSummaryDTO]:
    require_class_access(core, actor, class_id, "read_document_source")
    documents, cursor = shared_documents(
        session,
        active_document_keys(class_id, actor.school_id, file_format),
        school_id=actor.school_id,
        search=search,
        pagination=pagination,
    )
    return Page[ClassDocumentSummaryDTO](
        items=[
            class_summary(document, serving)
            for document in documents
            if (serving := get_serving_version(session, document.id)) is not None
        ],
        next_cursor=cursor,
    )


def document_reader(
    session: Session,
    core: CoreClient,
    storage: StorageBackend,
    actor: CoreUser,
    class_id: UUID,
    document_id: UUID,
    version_number: int | None,
) -> DocumentReaderDTO:
    access = class_access(core, actor, class_id, "read_document_source")
    if access.class_status is None:
        raise AppError(404)
    document = find_document(session, document_id, actor.school_id, include_deleted=True)
    if document is None:
        raise AppError(404)
    active_grant = grant_for_document(
        session, school_id=document.school_id, class_id=class_id, document_id=document_id
    )
    if access.allowed and document.deleted_at is None and active_grant is not None:
        serving = get_serving_version(session, document.id)
        if serving is None:
            raise AppError(404)
        viewing = select_viewing_version(session, document.id, serving, version_number)
        projection = load_reader_projection(storage, viewing)
        return DocumentReaderDTO(
            document=class_summary(document, serving),
            is_source_accessible=True,
            serving_version=serving.version_number,
            viewing_version=viewing.version_number,
            outline=projection.outline,
            pages=projection.pages,
            notes=reader_notes(session, actor, class_id, document_id, accessible=True),
            is_bookmarked=bookmark_version(session, actor, class_id, document_id) is not None,
        )

    notes = reader_notes(session, actor, class_id, document_id, accessible=False)
    marked_version = bookmark_version(session, actor, class_id, document_id)
    if not notes and marked_version is None:
        # Class history alone cannot reveal documents that were never shared with this class.
        previous_grant = grant_for_document(
            session,
            school_id=document.school_id,
            class_id=class_id,
            document_id=document_id,
            active_only=False,
        )
        if (
            previous_grant is None
            or not class_access(core, actor, class_id, "read_own_history").allowed
        ):
            raise AppError(404)
    if document.deleted_at is not None:
        reason = "DOCUMENT_DELETED"
    elif active_grant is None:
        reason = "GRANT_REVOKED"
    else:
        reason = "CLASS_ACCESS_LOST"
    personal_version = max((note.version_number for note in notes), default=0)
    historical_number = max(personal_version, marked_version or 0)
    historical_version = (
        get_version_by_number(session, document_id, historical_number)
        if historical_number
        else get_serving_version(session, document_id)
    )
    if historical_version is None and not historical_number:
        raise AppError(404)
    return DocumentReaderDTO(
        document=ReaderDocumentDTO(
            id=document.id,
            school_id=document.school_id,
            owner_user_id=document.owner_user_id,
            owner_display_name=None,
            title="Tài liệu",
            description=None,
            format=historical_version.file_type
            if historical_version is not None
            else document.format,
            created_at=document.created_at,
            updated_at=document.created_at,
            current_version=historical_number or historical_version.version_number,
            page_count=None,
        ),
        is_source_accessible=False,
        serving_version=None,
        viewing_version=None,
        outline=[],
        pages=[],
        notes=notes,
        revoked_reason=reason,
    )


def active_grants(session: Session, class_id: UUID, school_id: UUID | None) -> ActiveGrantsResponse:
    documents, _ = shared_documents(
        session, active_document_keys(class_id, school_id), school_id=school_id
    )
    return ActiveGrantsResponse(
        class_id=class_id,
        items=[
            ActiveGrantItem(
                document_id=document.id,
                school_id=document.school_id,
                title=document.title,
                format=serving.file_type,
                serving_version_id=serving.id,
                serving_version_number=serving.version_number,
                canonical_schema_version=serving.canonical_schema_version,
                canonical_sha256=serving.canonical_sha256,
            )
            for document in documents
            if (serving := get_serving_version(session, document.id)) is not None
        ],
    )


def canonical_artifact(
    session: Session, storage: StorageBackend, document_id: UUID, version_id: UUID
) -> bytes:
    version = get_version_by_id(session, document_id, version_id)
    if version is None:
        raise AppError(404)
    if not is_document_live(session, document_id):
        raise AppError(404)
    if version.processing_status != "SUCCESS":
        raise AppError(409, "Phiên bản chưa sẵn sàng.")
    return load_canonical_artifact(storage, version)
