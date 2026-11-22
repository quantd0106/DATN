from collections.abc import AsyncGenerator
from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from rag.generation.policy.config import TutorConfig
from starlette.types import Receive, Scope, Send

from app.common.api import ERROR_RESPONSES, Actor, ApiServices, Credentials
from app.common.class_access import require_active_teacher
from app.common.errors import AppError
from app.modules.tutor.generation import Operation, generate_turn, locate_citations
from app.modules.tutor.models import TutorMode
from app.modules.tutor.service import (
    ConversationSummary,
    HistoryMessage,
    create_conversation,
    list_conversations,
    load_tutor_config,
    owned_conversation,
    read_conversation_messages,
    stop_turn,
    update_tutor_config,
)
from app.modules.tutor.streaming import stream_turn

router = APIRouter(prefix="/api/v1", tags=["Tutor"], responses=ERROR_RESPONSES)
Limit = Annotated[int, Query(ge=1, le=100)]
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[^\x00]+$"),
]
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000, pattern=r"^[^\x00]*$")
]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Title
    default_mode: TutorMode = TutorMode.QUICK_QA


class ConversationCreated(BaseModel):
    id: UUID


class TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation = "ASK"
    mode: TutorMode | None = None
    text: Question = ""
    retry_of_message_id: UUID | None = None


class StopResult(BaseModel):
    stopped: bool


class ConfigValues(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    allow_general_knowledge: bool
    hint_reveal_after: int = Field(ge=1, le=10)
    allow_solution_reveal: bool
    guidance_level: str = Field(pattern=r"^(light|balanced|strong)$")
    max_answer_words: int = Field(ge=40, le=300)


class ConfigWrite(ConfigValues):
    expected_version: int | None = Field(ge=1)


class ConfigRead(ConfigValues):
    version: int | None


class TurnStreamResponse(StreamingResponse):
    def __init__(self, first: bytes, events: AsyncGenerator[bytes, None]):
        async def body():
            yield first
            async for event in events:
                yield event

        super().__init__(
            body(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )
        self.events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.events.aclose()


@router.get("/classes/{class_id}/tutor/conversations")
async def list_owned(
    class_id: UUID, user: Actor, api: ApiServices, limit: Limit = 50
) -> list[ConversationSummary]:
    return await list_conversations(
        api.require_db(),
        api.core,
        user,
        class_id,
        api.correlation_id,
        limit=limit,
    )


@router.post("/classes/{class_id}/tutor/conversations", status_code=201)
async def create(
    class_id: UUID, payload: ConversationCreate, user: Actor, api: ApiServices
) -> ConversationCreated:
    result = await create_conversation(
        api.require_db(),
        api.core,
        api.document,
        api.learning,
        user,
        class_id,
        payload.title,
        payload.default_mode,
        api.correlation_id,
    )
    return ConversationCreated(id=result)


@router.get("/tutor/conversations/{conversation_id}/messages")
async def history(
    conversation_id: UUID, user: Actor, api: ApiServices, limit: Limit = 50
) -> list[HistoryMessage]:
    return await read_conversation_messages(
        api.require_db(),
        api.core,
        api.document,
        user,
        conversation_id,
        api.correlation_id,
        limit=limit,
    )


@router.post(
    "/tutor/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def send_message(
    conversation_id: UUID,
    payload: TurnRequest,
    user: Actor,
    credentials: Credentials,
    api: ApiServices,
) -> StreamingResponse:
    db = api.require_db()
    conversation = await owned_conversation(db, user, conversation_id)
    mode = payload.mode or conversation.default_mode
    if payload.operation != "ASK" and mode != TutorMode.STEP_BY_STEP:
        raise AppError(422)
    if payload.retry_of_message_id is not None and payload.operation != "ASK":
        raise AppError(422)
    max_length = (
        500 if payload.operation == "CHECK" else 600 if mode == TutorMode.STEP_BY_STEP else 2000
    )
    takes_text = payload.operation in {"ASK", "CHECK"}
    if not takes_text and payload.text:
        raise AppError(422)
    if payload.operation == "CHECK" and not 1 <= len(payload.text) <= 500:
        raise AppError(422)
    if payload.operation == "ASK":
        if payload.retry_of_message_id is None and not 1 <= len(payload.text) <= max_length:
            raise AppError(422)
        if payload.retry_of_message_id is not None and len(payload.text) > max_length:
            raise AppError(422)
    token = credentials.credentials
    klass = await api.core.get_class(conversation.class_id, user, token, api.correlation_id)
    policy, _ = await load_tutor_config(db, klass.id, klass.school_id)

    async def enrich(result, snapshot):
        await api.core.verify_session(token, user.id, api.correlation_id)
        await locate_citations(api, result, snapshot)

    events = stream_turn(
        db,
        api.runtime,
        api.core,
        api.document,
        api.learning,
        user,
        conversation_id,
        payload.text,
        mode,
        api.correlation_id,
        partial(
            generate_turn,
            runtime=api.runtime,
            database=db,
            config=policy,
            grade=klass.grade_level,
            operation=payload.operation,
        ),
        record_question=takes_text and payload.retry_of_message_id is None,
        enrich=enrich,
        retry_of_message_id=payload.retry_of_message_id,
        use_history=True,
    )
    # Authorization, validation, and the DB transaction occur before the HTTP 200.
    return TurnStreamResponse(await anext(events), events)


@router.post("/tutor/conversations/{conversation_id}/messages/{message_id}/stop")
async def stop(
    conversation_id: UUID, message_id: UUID, user: Actor, api: ApiServices
) -> StopResult:
    db = api.require_db()
    conversation = await owned_conversation(db, user, conversation_id)
    return StopResult(
        stopped=await stop_turn(
            api.core,
            user,
            conversation,
            message_id,
            api.correlation_id,
            db,
            api.runtime.cancellations,
        )
    )


@router.get("/classes/{class_id}/tutor/config")
async def get_config(class_id: UUID, user: Actor, api: ApiServices) -> ConfigRead:
    access = await require_active_teacher(api.core, user, class_id, api.correlation_id)
    policy, version = await load_tutor_config(api.require_db(), class_id, access.school_id)
    return ConfigRead(**policy.to_dict(), version=version)


@router.put("/classes/{class_id}/tutor/config")
async def put_config(
    class_id: UUID, payload: ConfigWrite, user: Actor, api: ApiServices
) -> ConfigRead:
    policy = TutorConfig.from_mapping(payload.model_dump(exclude={"expected_version"}))
    version = await update_tutor_config(
        api.require_db(),
        api.core,
        user,
        class_id,
        policy,
        payload.expected_version,
        api.correlation_id,
    )
    return ConfigRead(**policy.to_dict(), version=version)
