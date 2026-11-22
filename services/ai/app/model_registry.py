from app.common.db import Base
from app.modules.indexing.models import IndexedDocumentVersion
from app.modules.quiz.models import QuizItemDraft, QuizJob
from app.modules.tutor.models import Conversation, Message, TutorConfigRow
from app.runtime.models import GenerationProvenance

__all__ = [
    "Conversation",
    "GenerationProvenance",
    "IndexedDocumentVersion",
    "Message",
    "QuizItemDraft",
    "QuizJob",
    "TutorConfigRow",
    "load_metadata",
]


def load_metadata():
    return Base.metadata
