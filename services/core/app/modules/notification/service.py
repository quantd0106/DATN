from uuid import UUID

from sqlalchemy.orm import Session

from app.common.db import transaction
from app.common.pagination import Pagination, encode_cursor
from app.modules.classroom.public import require_class_school
from app.modules.notification.repository import NotificationRepository
from app.modules.notification.schemas import (
    LearningNotificationReceipt,
    LearningNotificationRequest,
    MarkReadResponse,
    NotificationListResponse,
    NotificationResponse,
)


class NotificationService:
    def __init__(self, session: Session):
        self.session = session
        self.repository = NotificationRepository(session)

    def ingest_learning_event(
        self, body: LearningNotificationRequest
    ) -> LearningNotificationReceipt:
        with transaction(self.session):
            require_class_school(body.class_id, body.school_id, session=self.session)
            deduped = self.repository.enqueue_learning_event(
                body.source_event_id,
                event_type=body.event_type,
                aggregate_type=body.resource_type,
                aggregate_id=str(body.resource_id),
                payload=body.model_dump(
                    mode="json",
                    include={"school_id", "class_id", "resource_type", "resource_id", "student_id"},
                    exclude_none=True,
                ),
            )
        return LearningNotificationReceipt(outbox_event_id=body.source_event_id, deduped=deduped)

    def list_notifications(self, user_id: UUID, pagination: Pagination) -> NotificationListResponse:
        with transaction(self.session):
            self.repository.lock_recipient(user_id)
            rows = self.repository.list_for_user(
                user_id, limit=pagination.limit, cursor=pagination.cursor
            )
            items = rows[: pagination.limit]
            return NotificationListResponse(
                items=[
                    NotificationResponse(
                        id=row.id,
                        type=row.type,
                        payload_json=row.payload_json,
                        read=row.read_at is not None,
                        created_at=row.created_at,
                    )
                    for row in items
                ],
                unread_count=self.repository.unread_count(user_id),
                next_cursor=encode_cursor(items[-1].id) if len(rows) > pagination.limit else None,
            )

    def mark_as_read(self, notification_id: UUID, user_id: UUID) -> MarkReadResponse:
        with transaction(self.session):
            self.repository.lock_recipient(user_id)
            self.repository.mark_read(notification_id, user_id)
            return MarkReadResponse(id=notification_id, read=True)

    def mark_all_as_read(self, user_id: UUID) -> None:
        with transaction(self.session):
            self.repository.lock_recipient(user_id)
            self.repository.mark_all_read(user_id)
