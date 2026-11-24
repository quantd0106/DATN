from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from rag.generation.policy.config import TutorConfig
from rag.generation.tasks.qa.render import mask_revoked
from rag.generation.tasks.qa.step import StepSession
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.common.class_access import require_active_teacher
from app.common.db import Database
from app.common.errors import AppError
from app.integrations.core_client import ClassAction, CoreClient, CurrentUser
from app.integrations.document_client import ActiveGrant, DocumentPort, GrantSnapshot
from app.integrations.learning_client import LearningPort
from app.modules.tutor.models import (
    Conversation,
    Message,
    MessageRole,
    MessageStatus,
    TutorConfigRow,
    TutorMode,
)
from app.runtime.rag_runtime import CancelRegistry


class HistoryMessage(BaseModel):
    id: UUID
    role: MessageRole
    mode: TutorMode
    status: MessageStatus
    content: str | None
    rendered: dict | None
    created_at: datetime


class ConversationSummary(BaseModel):
    id: UUID
    class_id: UUID
    title: str
    default_mode: TutorMode
    updated_at: datetime


async def owned_conversation(
    database: Database, user: CurrentUser, conversation_id: UUID
) -> Conversation:
    def read() -> Conversation:
        with database.SessionLocal() as session:
            return find_owned_conversation(session, conversation_id, user)

    return await run_in_threadpool(read)


async def list_conversations(
    database: Database,
    core: CoreClient,
    user: CurrentUser,
    class_id: UUID,
    correlation_id: str,
    *,
    limit: int,
) -> list[ConversationSummary]:
    if "STUDENT" not in user.roles or user.school_id is None:
        raise AppError(404)
    allowed = await core.check_access(class_id, user, ClassAction.READ_OWN_HISTORY, correlation_id)
    if not allowed.allowed:
        raise AppError(404)

    def read() -> list[ConversationSummary]:
        with database.SessionLocal() as session:
            rows = session.scalars(
                select(Conversation)
                .where(
                    Conversation.user_id == user.id,
                    Conversation.school_id == user.school_id,
                    Conversation.class_id == class_id,
                )
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(limit)
            )
            return [ConversationSummary.model_validate(row, from_attributes=True) for row in rows]

    return await run_in_threadpool(read)


async def load_tutor_config(
    database: Database,
    class_id: UUID,
    school_id: UUID,
) -> tuple[TutorConfig, int | None]:
    def read() -> tuple[TutorConfig, int | None]:
        with database.SessionLocal() as session:
            row = session.scalar(
                select(TutorConfigRow).where(
                    TutorConfigRow.class_id == class_id,
                    TutorConfigRow.school_id == school_id,
                )
            )
            defaults = TutorConfig()
            if row is None:
                return defaults, None
            policy = TutorConfig.from_mapping(
                {name: getattr(row, name) for name in defaults.to_dict()}
            )
            return policy, row.version

    return await run_in_threadpool(read)


async def authorize_new_turn(
    core: CoreClient,
    document: DocumentPort,
    learning: LearningPort,
    user: CurrentUser,
    class_id: UUID,
    correlation_id: str,
) -> GrantSnapshot:
    if "STUDENT" not in user.roles or user.school_id is None:
        raise AppError(403)
    access = await core.check_access(class_id, user, ClassAction.USE_AI_TUTOR, correlation_id)
    if not access.allowed or access.class_status != "ACTIVE":
        raise AppError(403)
    await learning.require_tutor_available(user.id, user.school_id, correlation_id)
    return await document.active_grants(class_id, user.school_id, correlation_id)


async def create_conversation(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    learning: LearningPort,
    user: CurrentUser,
    class_id: UUID,
    title: str,
    mode: TutorMode,
    correlation_id: str,
) -> UUID:
    if not 1 <= len(title.strip()) <= 200:
        raise AppError(422)
    scope = await authorize_new_turn(core, document, learning, user, class_id, correlation_id)

    def persist() -> UUID:
        with database.SessionLocal.begin() as session:
            conversation = Conversation(
                user_id=user.id,
                class_id=class_id,
                school_id=scope.school_id,
                title=title.strip(),
                default_mode=mode,
            )
            session.add(conversation)
            session.flush()
            return conversation.id

    return await run_in_threadpool(persist)


def find_owned_conversation(
    session: Session, conversation_id: UUID, user: CurrentUser
) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
            Conversation.school_id == user.school_id,
        )
    )
    if conversation is None:
        raise AppError(404)
    return conversation


async def require_history_owner(
    core: CoreClient, user: CurrentUser, conversation: Conversation, correlation_id: str
) -> None:
    if (
        user.id != conversation.user_id
        or user.school_id != conversation.school_id
        or "STUDENT" not in user.roles
    ):
        raise AppError(404)
    access = await core.check_access(
        conversation.class_id, user, ClassAction.READ_OWN_HISTORY, correlation_id
    )
    if not access.allowed:
        raise AppError(404)


async def history_grant_snapshot(
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    conversation: Conversation,
    correlation_id: str,
) -> GrantSnapshot | None:
    await require_history_owner(core, user, conversation, correlation_id)
    scope = await core.check_access(
        conversation.class_id, user, ClassAction.READ_DOCUMENT_SOURCE, correlation_id
    )
    if not scope.allowed:
        return None
    try:
        return await document.active_grants(
            conversation.class_id, conversation.school_id, correlation_id
        )
    except AppError as exc:
        if exc.status_code != 503:
            raise
        # The owner can still read their personal history with all sources hidden.
        return None


async def history_sources(
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    conversation: Conversation,
    correlation_id: str,
) -> frozenset[str]:
    snapshot = await history_grant_snapshot(core, document, user, conversation, correlation_id)
    return snapshot.source_ids if snapshot is not None else frozenset()


def attach_citation_source_metadata(
    citation: dict, grant: ActiveGrant | None, document_port: object | None = None
) -> None:
    if citation.get("masked"):
        return
    if grant is not None:
        citation.setdefault("document_id", str(grant.document_id))
        if (
            citation.get("version_number") is None
            and document_port is not None
            and hasattr(document_port, "version_number")
        ):
            version_num = document_port.version_number(grant.document_version_id)
            if isinstance(version_num, int) and version_num >= 1:
                citation["version_number"] = version_num
    location = citation.get("location")
    if isinstance(location, dict) and "page" not in location and "slide" not in location:
        spans = citation.get("source_spans") or ()
        page = next(
            (
                span.get("page")
                for span in spans
                if isinstance(span, dict)
                and isinstance(span.get("page"), int)
                and span["page"] >= 1
            ),
            None,
        )
        if page is not None:
            location["page"] = page


def project_history_message(
    message: Message,
    source_ids: frozenset[str],
    *,
    grants_by_source: Mapping[str, ActiveGrant] | None = None,
    document_port: object | None = None,
) -> HistoryMessage:
    rendered = None
    if message.role == MessageRole.ASSISTANT and message.rendered_payload_json is not None:
        rendered = mask_revoked(message.rendered_payload_json, source_ids)
        required_sources = frozenset(rendered.pop("_required_sources", ()))
        if not required_sources <= source_ids:
            rendered = {
                "status": "source_access_revoked",
                "claims": [],
                "missing_parts": [],
                "notice": "Nội dung đã được ẩn do quyền truy cập nguồn thay đổi.",
            }
        else:
            masked = False
            for claim in rendered["claims"]:
                citations = claim["citations"]
                if (claim["basis"] == "source" and not citations) or any(
                    citation.get("masked") for citation in citations
                ):
                    claim["text"] = "Nội dung đã được ẩn do quyền truy cập nguồn thay đổi."
                    masked = True
                elif grants_by_source:
                    for citation in citations:
                        attach_citation_source_metadata(
                            citation,
                            grants_by_source.get(citation.get("source_id")),
                            document_port,
                        )
            if masked:
                rendered["missing_parts"] = []
    return HistoryMessage(
        id=message.id,
        role=message.role,
        mode=message.mode,
        status=message.status,
        content=message.content if message.role == MessageRole.USER else None,
        rendered=rendered,
        created_at=message.created_at,
    )


async def read_conversation_messages(
    database: Database,
    core: CoreClient,
    document: DocumentPort,
    user: CurrentUser,
    conversation_id: UUID,
    correlation_id: str,
    *,
    limit: int = 50,
) -> list[HistoryMessage]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AppError(422)

    conversation = await owned_conversation(database, user, conversation_id)
    snapshot = await history_grant_snapshot(core, document, user, conversation, correlation_id)
    source_ids = snapshot.source_ids if snapshot is not None else frozenset()
    grants_by_source = (
        {grant.source_id: grant for grant in snapshot.grants} if snapshot is not None else {}
    )

    def read() -> list[HistoryMessage]:
        with database.SessionLocal() as session:
            messages = session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            ).all()
            return [
                project_history_message(
                    message,
                    source_ids,
                    grants_by_source=grants_by_source,
                    document_port=document,
                )
                for message in reversed(messages)
            ]

    return await run_in_threadpool(read)


def sse_message(message: HistoryMessage) -> bytes:
    return f"event: message\ndata: {message.model_dump_json()}\n\n".encode()


def read_step_session(conversation: Conversation) -> StepSession | None:
    data = conversation.step_session_json
    return StepSession.from_dict(data) if data is not None else None


def save_step_session(conversation: Conversation, step: StepSession | None) -> None:
    conversation.step_session_json = step.to_dict() if step is not None else None


def store_tutor_config(
    session: Session,
    *,
    class_id: UUID,
    school_id: UUID,
    editor_id: UUID,
    config: TutorConfig,
    expected_version: int | None,
) -> int:
    values = config.to_dict()
    if expected_version is None:
        statement = (
            insert(TutorConfigRow)
            .values(class_id=class_id, school_id=school_id, updated_by=editor_id, **values)
            .on_conflict_do_nothing(index_elements=[TutorConfigRow.class_id])
        )
    else:
        statement = (
            update(TutorConfigRow)
            .where(
                TutorConfigRow.class_id == class_id,
                TutorConfigRow.school_id == school_id,
                TutorConfigRow.version == expected_version,
            )
            .values(version=TutorConfigRow.version + 1, updated_by=editor_id, **values)
        )
    version = session.scalar(statement.returning(TutorConfigRow.version))
    if version is None:
        raise AppError(409)
    return version


async def update_tutor_config(
    database: Database,
    core: CoreClient,
    user: CurrentUser,
    class_id: UUID,
    config: TutorConfig,
    expected_version: int | None,
    correlation_id: str,
) -> int:
    access = await require_active_teacher(core, user, class_id, correlation_id)

    def persist() -> int:
        with database.SessionLocal.begin() as session:
            return store_tutor_config(
                session,
                class_id=class_id,
                school_id=access.school_id,
                editor_id=user.id,
                config=config,
                expected_version=expected_version,
            )

    return await run_in_threadpool(persist)


def set_message_terminal_status(
    session: Session,
    conversation_id: UUID,
    message_id: UUID,
    status: MessageStatus,
    *,
    rendered: dict | None = None,
    content: str | None = None,
    knowledge_type: str | None = None,
    step_number: int | None = None,
    total_steps: int | None = None,
) -> bool:
    if status == MessageStatus.STREAMING:
        raise ValueError("Terminal status required")
    result = session.execute(
        update(Message)
        .where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
            Message.role == MessageRole.ASSISTANT,
            Message.status == MessageStatus.STREAMING,
        )
        .values(
            status=status,
            rendered_payload_json=rendered,
            content=content,
            knowledge_type=knowledge_type,
            step_number=step_number,
            total_steps=total_steps,
        )
        .returning(Message.id)
    )
    return result.scalar_one_or_none() is not None


def extract_message_metadata(rendered: dict | None) -> tuple[str | None, int | None, int | None]:
    if not isinstance(rendered, dict):
        return None, None, None
    claims = rendered.get("claims") or []
    bases = {
        claim.get("basis")
        for claim in claims
        if isinstance(claim, dict) and claim.get("basis") in {"source", "general"}
    }
    knowledge_type = (
        "CLASS_SOURCE" if "source" in bases else "GENERAL_KNOWLEDGE" if "general" in bases else None
    )
    step = rendered.get("step")
    step_number = total_steps = None
    if isinstance(step, dict):
        sn, ts = step.get("step"), step.get("total_steps")
        if type(sn) is int and type(ts) is int and 1 <= sn <= ts:
            step_number, total_steps = sn, ts
    return knowledge_type, step_number, total_steps


def build_condense_history(
    session: Session,
    conversation_id: UUID,
    source_ids: frozenset[str],
    *,
    limit: int = 6,
    exclude_message_id: UUID | None = None,
    before_created_at: datetime | None = None,
) -> list[dict[str, str]]:
    filters = [
        Message.conversation_id == conversation_id,
        Message.status == MessageStatus.COMPLETED,
    ]
    if exclude_message_id is not None:
        filters.append(Message.id != exclude_message_id)
    if before_created_at is not None:
        filters.append(Message.created_at < before_created_at)
    rows = list(
        reversed(
            session.scalars(
                select(Message)
                .where(*filters)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            ).all()
        )
    )
    turns: list[dict[str, str]] = []
    for row in rows:
        if row.role == MessageRole.USER:
            text = (row.content or "").strip()
            if text:
                turns.append({"role": "user", "content": text})
            continue
        projected = project_history_message(row, source_ids)
        rendered = projected.rendered
        if not isinstance(rendered, dict) or rendered.get("status") == "source_access_revoked":
            continue
        texts = [
            claim["text"].strip()
            for claim in rendered.get("claims", [])
            if isinstance(claim, dict)
            and isinstance(claim.get("text"), str)
            and claim["text"].strip()
            and claim["text"] != "Nội dung đã được ẩn do quyền truy cập nguồn thay đổi."
        ]
        if texts:
            turns.append({"role": "assistant", "content": "\n".join(texts)})
    return turns


async def stop_turn(
    core: CoreClient,
    user: CurrentUser,
    conversation: Conversation,
    message_id: UUID,
    correlation_id: str,
    database: Database,
    cancellations: CancelRegistry,
) -> bool:
    await require_history_owner(core, user, conversation, correlation_id)
    conversation_id = conversation.id

    def persist_stop() -> bool:
        with database.SessionLocal.begin() as session:
            return set_message_terminal_status(
                session, conversation_id, message_id, MessageStatus.STOPPED
            )

    stopped = await run_in_threadpool(persist_stop)
    if stopped:
        cancellations.cancel(message_id)
    return stopped
