from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, CreatedAt, UpdatedAt, UUIDPrimaryKey


class TutorMode(StrEnum):
    QUICK_QA = "QUICK_QA"
    STEP_BY_STEP = "STEP_BY_STEP"


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MessageStatus(StrEnum):
    STREAMING = "STREAMING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class TutorConfigRow(Base, UpdatedAt):
    __tablename__ = "tutor_configs"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_tutor_config_version"),
        CheckConstraint("hint_reveal_after BETWEEN 1 AND 10", name="ck_tutor_config_hint"),
        CheckConstraint("max_answer_words BETWEEN 40 AND 300", name="ck_tutor_config_word_limit"),
        CheckConstraint(
            "guidance_level IN ('light', 'balanced', 'strong')", name="ck_tutor_config_guidance"
        ),
    )

    class_id: Mapped[UUID] = mapped_column(primary_key=True)
    school_id: Mapped[UUID]
    allow_general_knowledge: Mapped[bool]
    hint_reveal_after: Mapped[int]
    allow_solution_reveal: Mapped[bool]
    guidance_level: Mapped[str] = mapped_column(String(8))
    max_answer_words: Mapped[int]
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    updated_by: Mapped[UUID]

    __mapper_args__: ClassVar = {"version_id_col": version}


class Conversation(Base, UUIDPrimaryKey, CreatedAt, UpdatedAt):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_owner_class", "school_id", "user_id", "class_id"),)

    user_id: Mapped[UUID]
    class_id: Mapped[UUID]
    school_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(String(200))
    default_mode: Mapped[TutorMode] = mapped_column(
        Enum(TutorMode, native_enum=False, create_constraint=True, name="conversation_mode")
    )
    step_session_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class Message(Base, UUIDPrimaryKey, CreatedAt):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "id", name="uq_messages_conversation_id"),
        ForeignKeyConstraint(
            ["conversation_id", "retry_of_message_id"],
            ["ai.messages.conversation_id", "ai.messages.id"],
            name="fk_message_retry_same_conversation",
        ),
        CheckConstraint("retry_of_message_id IS DISTINCT FROM id", name="ck_message_retry_target"),
        CheckConstraint(
            "(role = 'ASSISTANT' OR status = 'COMPLETED')", name="ck_message_user_completed"
        ),
        CheckConstraint(
            "(step_number IS NULL OR step_number >= 1) AND (total_steps IS NULL OR total_steps >= 1)"
            " AND (step_number IS NULL OR total_steps IS NULL OR step_number <= total_steps)",
            name="ck_message_step_bounds",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at", "id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai.conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, create_constraint=True, name="message_role")
    )
    mode: Mapped[TutorMode] = mapped_column(
        Enum(TutorMode, native_enum=False, create_constraint=True, name="message_mode")
    )
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False, create_constraint=True, name="message_status")
    )
    content: Mapped[str | None] = mapped_column(Text)
    rendered_payload_json: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    knowledge_type: Mapped[str | None] = mapped_column(String(32))
    step_number: Mapped[int | None]
    total_steps: Mapped[int | None]
    pedagogical_hint: Mapped[str | None] = mapped_column(Text)
    retry_of_message_id: Mapped[UUID | None]
