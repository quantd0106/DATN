import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from rag.generation.contracts.errors import GenerationUnavailable
from rag.generation.runtime.scheduler import SchedulerBusy
from rag.generation.tasks.qa.step import StepSession
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.common.db import Database
from app.common.errors import AppError
from app.integrations.core_client import CoreClient, CurrentUser
from app.integrations.document_client import DocumentPort, GrantSnapshot
from app.integrations.learning_client import LearningPort
from app.modules.tutor.canonical_fallback import ensure_canonical_cache
from app.modules.tutor.models import Conversation, Message, MessageRole, MessageStatus, TutorMode
from app.modules.tutor.service import (
    authorize_new_turn,
    build_condense_history,
    extract_message_metadata,
    owned_conversation,
    project_history_message,
    read_step_session,
    save_step_session,
    set_message_terminal_status,
    sse_message,
)
from app.runtime.provenance import save_provenance
from app.runtime.rag_runtime import RagRuntime


@dataclass
class TurnResult:
    rendered: dict
    provenance: list[dict] = field(default_factory=list)
    step_session: StepSession | None = None


async def stream_turn(
    database: Database,
    runtime: RagRuntime,
    core: CoreClient,
    document: DocumentPort,
    learning: LearningPort,
    user: CurrentUser,
    conversation_id: UUID,
    question: str,
    mode: TutorMode,
    correlation_id: str,
    generate: Callable[..., TurnResult],
    *,
    record_question: bool = True,
    enrich: Callable[[TurnResult, GrantSnapshot], Awaitable[None]] | None = None,
    retry_of_message_id: UUID | None = None,
    use_history: bool = False,
) -> AsyncIterator[bytes]:
    """SSE body for a turn. The producer receives scope only from the backend gate."""
    if (
        retry_of_message_id is None
        and record_question
        and (not isinstance(question, str) or not 1 <= len(question.strip()) <= 8192)
    ):
        raise AppError(422)

    conversation = await owned_conversation(database, user, conversation_id)
    initial = await authorize_new_turn(
        core, document, learning, user, conversation.class_id, correlation_id
    )

    def begin() -> tuple[UUID, StepSession | None, str, list[dict[str, str]]]:
        with database.SessionLocal.begin() as session:
            row = session.get(Conversation, conversation.id, with_for_update=True)
            if row is None:
                raise AppError(404)
            existing = session.scalar(
                select(Message.id)
                .where(Message.conversation_id == row.id, Message.status == MessageStatus.STREAMING)
                .limit(1)
            )
            if existing is not None:
                raise AppError(409)
            effective_question = question
            history_before = None
            if retry_of_message_id is not None:
                target = session.scalar(
                    select(Message).where(
                        Message.id == retry_of_message_id,
                        Message.conversation_id == row.id,
                        Message.role == MessageRole.USER,
                    )
                )
                if target is None:
                    raise AppError(404)
                effective_question = question.strip() or (target.content or "").strip()
                if not 1 <= len(effective_question) <= 8192:
                    raise AppError(422)
                history_before = target.created_at
            history = (
                build_condense_history(
                    session,
                    row.id,
                    initial.source_ids,
                    exclude_message_id=retry_of_message_id,
                    before_created_at=history_before,
                )
                if use_history
                else []
            )
            step = read_step_session(row) if mode == TutorMode.STEP_BY_STEP else None
            submitted_at = datetime.now(UTC)
            row.updated_at = submitted_at
            if record_question and retry_of_message_id is None:
                session.add(
                    Message(
                        conversation_id=row.id,
                        role=MessageRole.USER,
                        mode=mode,
                        status=MessageStatus.COMPLETED,
                        content=question,
                        created_at=submitted_at,
                    )
                )
            assistant = Message(
                conversation_id=row.id,
                role=MessageRole.ASSISTANT,
                mode=mode,
                status=MessageStatus.STREAMING,
                retry_of_message_id=retry_of_message_id,
                created_at=submitted_at + timedelta(microseconds=1),
            )
            session.add(assistant)
            session.flush()
            return assistant.id, step, effective_question, history

    message_id, step, effective_question, history = await run_in_threadpool(begin)

    def finish(status: MessageStatus, result: TurnResult | None = None) -> Message:
        with database.SessionLocal.begin() as session:
            knowledge_type, step_number, total_steps = extract_message_metadata(
                result.rendered if result is not None else None
            )
            changed = set_message_terminal_status(
                session,
                conversation_id,
                message_id,
                status,
                rendered=result.rendered if result is not None else None,
                knowledge_type=knowledge_type,
                step_number=step_number,
                total_steps=total_steps,
            )
            if changed and result is not None:
                if mode == TutorMode.STEP_BY_STEP or result.step_session is not None:
                    save_step_session(
                        session.get(Conversation, conversation_id), result.step_session
                    )
                save_provenance(session, result.provenance)
            return session.get(Message, message_id)

    with runtime.cancellations.track(message_id) as cancelled:
        terminal = False
        try:
            # Announce only identifiers/status. Grounded claims are emitted after permissions are checked again.
            yield f'event: start\ndata: {{"message_id":"{message_id}"}}\n\n'.encode()
            if cancelled.is_set():
                message = await run_in_threadpool(finish, MessageStatus.STOPPED)
                terminal = True
                yield sse_message(project_history_message(message, frozenset()))
                return
            await ensure_canonical_cache(database, runtime, document, initial, correlation_id)
            args = (effective_question, mode, step, initial, cancelled)
            if use_history:
                args = (*args, history)
            result = await runtime.executor.run(generate, *args)
            if not cancelled.is_set() and enrich is not None:
                await enrich(result, initial)
            current = await authorize_new_turn(
                core, document, learning, user, conversation.class_id, correlation_id
            )
            if not set(initial.grants) <= set(current.grants):
                raise AppError(403)
            if cancelled.is_set():
                message = await run_in_threadpool(finish, MessageStatus.STOPPED)
            else:
                message = await run_in_threadpool(finish, MessageStatus.COMPLETED, result)
            terminal = True
            yield sse_message(project_history_message(message, current.source_ids))
        except (AppError, GenerationUnavailable, SchedulerBusy):
            message = await run_in_threadpool(finish, MessageStatus.FAILED)
            terminal = True
            yield sse_message(project_history_message(message, frozenset()))
        finally:
            # A disconnect or failed producer must not leave the turn in STREAMING.
            if not terminal:
                cancelled.set()
                await asyncio.shield(asyncio.to_thread(finish, MessageStatus.STOPPED))
