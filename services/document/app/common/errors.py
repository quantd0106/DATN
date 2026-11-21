import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}
STATUS_MESSAGES = {
    400: "Thông tin gửi lên chưa hợp lệ. Vui lòng kiểm tra lại.",
    401: "Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Vui lòng đăng nhập lại.",
    403: "Bạn không có quyền thực hiện thao tác này.",
    404: "Không tìm thấy nội dung bạn yêu cầu.",
    409: "Yêu cầu không phù hợp với trạng thái hiện tại. Vui lòng tải lại rồi thử lại.",
    413: "Tệp vượt dung lượng cho phép.",
    422: "Thông tin chưa hợp lệ. Vui lòng kiểm tra các trường đã nhập.",
    500: "Hệ thống đang gặp sự cố. Vui lòng thử lại sau.",
    503: "Dịch vụ đang tạm thời gián đoạn. Vui lòng thử lại sau.",
}

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: UUID
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class AppError(HTTPException):
    """Application error carrying an explicitly public message and structured details."""

    def __init__(
        self,
        status_code: int,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(status_code, message)
        self.public_message = message
        self.code = code
        self.details = details


def error_response(
    request: Request, status: int, error: AppError | None = None, headers=None
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=(error.code if error else None) or ERROR_CODES.get(status, "HTTP_ERROR"),
            message=(error.public_message if error else None)
            or STATUS_MESSAGES.get(status, "Đã xảy ra lỗi. Vui lòng thử lại."),
            correlation_id=request.state.correlation_id,
            details=error.details if error else None,
        )
    )
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        try:
            correlation_id = UUID(request.headers.get("X-Correlation-ID", ""))
        except ValueError:
            correlation_id = uuid4()
        request.state.correlation_id = correlation_id
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("Document request failed (%s) [%s]", type(exc).__name__, correlation_id)
            response = error_response(request, 500)
        response.headers["X-Correlation-ID"] = str(correlation_id)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        public_error = exc if isinstance(exc, AppError) and exc.status_code != 404 else None
        return error_response(request, exc.status_code, public_error, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, 422)


def correlation_id(request: Request) -> UUID:
    return request.state.correlation_id
