"""Load the service's model metadata for migrations and test setup."""

from app.common.audit import AuditEvent
from app.common.db import Base
from app.common.outbox import OutboxEvent
from app.modules.grants.models import ClassDocumentGrant
from app.modules.library.models import Document
from app.modules.notes.models import DocumentBookmark, SourceBoundNote
from app.modules.versions.models import DocumentVersion

__all__ = [
    "AuditEvent",
    "Base",
    "ClassDocumentGrant",
    "Document",
    "DocumentBookmark",
    "DocumentVersion",
    "OutboxEvent",
    "SourceBoundNote",
]
