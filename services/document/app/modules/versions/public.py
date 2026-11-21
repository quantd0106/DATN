"""Version interfaces for callers that have already checked document access."""

from app.modules.versions.processing import process_next_version
from app.modules.versions.repository import (
    get_serving_version,
    get_version_by_id,
    get_version_by_number,
    latest_status,
    serving_file_type,
    serving_version_id,
)
from app.modules.versions.schemas import DocumentVersionDTO, ReaderOutlineEntry, ReaderPage
from app.modules.versions.service import (
    create_pending_version,
    list_versions_with_derived_state,
    load_canonical_artifact,
    load_reader_projection,
    open_original,
    select_viewing_version,
    source_storage_key,
    version_dto,
)

__all__ = [
    "DocumentVersionDTO",
    "ReaderOutlineEntry",
    "ReaderPage",
    "create_pending_version",
    "get_serving_version",
    "get_version_by_id",
    "get_version_by_number",
    "latest_status",
    "list_versions_with_derived_state",
    "load_canonical_artifact",
    "load_reader_projection",
    "open_original",
    "process_next_version",
    "select_viewing_version",
    "serving_file_type",
    "serving_version_id",
    "source_storage_key",
    "version_dto",
]
