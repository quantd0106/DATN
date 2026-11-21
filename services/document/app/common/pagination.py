import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.common.errors import AppError


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class Pagination(BaseModel):
    limit: int
    cursor: UUID | None


def encode_cursor(last_id: UUID) -> str:
    return base64.urlsafe_b64encode(last_id.bytes).decode("ascii").rstrip("=")


def invalid_cursor() -> AppError:
    return AppError(400, "Liên kết trang dữ liệu không còn hợp lệ. Vui lòng tải lại trang.")


def decode_cursor(cursor: str) -> UUID:
    try:
        if len(cursor) != 22:
            raise ValueError
        result = UUID(bytes=base64.b64decode(cursor + "==", altchars=b"-_", validate=True))
        if encode_cursor(result) != cursor:
            raise ValueError
        return result
    except (ValueError, binascii.Error) as exc:
        raise invalid_cursor() from exc


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> Pagination:
    return Pagination(limit=limit, cursor=decode_cursor(cursor) if cursor is not None else None)


PageRequest = Annotated[Pagination, Depends(get_pagination)]


def paginate[T](
    session: Session, query: Select[tuple[T]], id_column, pagination: Pagination
) -> tuple[list[T], str | None]:
    if pagination.cursor is not None:
        if session.scalar(query.where(id_column == pagination.cursor)) is None:
            raise invalid_cursor()
        query = query.where(id_column > pagination.cursor)
    rows = list(session.scalars(query.order_by(id_column).limit(pagination.limit + 1)))
    items = rows[: pagination.limit]
    return items, encode_cursor(items[-1].id) if len(rows) > pagination.limit else None
