from contextlib import nullcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.common.db import SessionLocal
from app.modules.social.calendar.repository import CalendarRepository

__all__ = ["CalendarEventRefDTO", "get_visible_calendar_event_ref"]


class CalendarEventRefDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    class_id: UUID


def get_visible_calendar_event_ref(
    class_id: UUID,
    event_id: UUID,
    *,
    session: Session | None = None,
) -> CalendarEventRefDTO | None:
    """Verify a calendar event still exists in the class before notification delivery."""
    with SessionLocal() if session is None else nullcontext(session) as db:
        event = CalendarRepository(db).get(event_id)
        if event is None or event.class_id != class_id:
            return None
        return CalendarEventRefDTO(id=event.id, class_id=event.class_id)
