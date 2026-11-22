from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.db import utcnow, write_transaction
from app.common.errors import AppError
from app.common.idempotency import payload_sha256
from app.modules.bank.grading_engine import normalize_ai_item_to_bank
from app.modules.bank.models import QuestionItem, QuestionOrigin


def _imported_item(session: Session, draft_id: UUID) -> QuestionItem | None:
    return session.scalar(select(QuestionItem).where(QuestionItem.ai_draft_id == draft_id))


def _replay(
    row: QuestionItem, class_id: UUID, school_id: UUID, request_hash: str
) -> tuple[UUID, bool]:
    if (row.class_id, row.school_id) != (class_id, school_id):
        raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
    previous_hash = (row.ai_provenance_json or {}).get("import_payload_sha256")
    if previous_hash is not None and request_hash != previous_hash:
        raise AppError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH")
    return row.id, False


def import_quiz_item(
    session: Session,
    class_id: UUID,
    school_id: UUID,
    draft_id: UUID,
    approved_by: UUID,
    payload: dict,
) -> tuple[UUID, bool]:
    request_hash = payload_sha256(payload)
    try:
        with write_transaction(session):
            row = _imported_item(session, draft_id)
            if row is not None:
                return _replay(row, class_id, school_id, request_hash)
            try:
                item = normalize_ai_item_to_bank(payload)
            except (ValueError, TypeError, AttributeError, KeyError):
                raise AppError(422) from None
            row = QuestionItem(
                class_id=class_id,
                school_id=school_id,
                origin=QuestionOrigin.AI,
                ai_draft_id=draft_id,
                ai_provenance_json={
                    **payload["provenance"],
                    "level": payload["level"],
                    "sources": payload["sources"],
                    "edited_after_generation": payload["edited_after_generation"],
                    "import_payload_sha256": request_hash,
                },
                reviewed_by=approved_by,
                created_by=approved_by,
                reviewed_at=utcnow(),
                **item,
            )
            session.add(row)
            session.flush()
            return row.id, True
    except IntegrityError:
        row = _imported_item(session, draft_id)
        if row is None:
            raise
        return _replay(row, class_id, school_id, request_hash)
