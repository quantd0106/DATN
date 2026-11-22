import base64
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Query

from app.common.errors import AppError


@dataclass(frozen=True)
class Pagination:
    limit: int
    cursor: UUID | None


def encode_cursor(item_id: UUID) -> str:
    return base64.urlsafe_b64encode(str(item_id).encode("ascii")).decode("ascii")


def decode_cursor(cursor: str | None) -> UUID | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        return UUID(raw)
    except Exception:  # noqa: BLE001
        raise AppError(400) from None


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
) -> Pagination:
    return Pagination(limit=limit, cursor=decode_cursor(cursor))
