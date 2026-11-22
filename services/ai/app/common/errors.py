from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.logging import logger

ERRORS = {
    400: ("BAD_REQUEST", "Thông tin gửi lên chưa hợp lệ. Vui lòng kiểm tra lại."),
    401: (
        "UNAUTHENTICATED",
        "Phiên đăng nhập không hợp lệ hoặc đã hết hạn. Vui lòng đăng nhập lại.",
    ),
    403: ("FORBIDDEN", "Bạn không có quyền thực hiện thao tác này."),
    404: ("NOT_FOUND", "Không tìm thấy nội dung bạn yêu cầu."),
    409: (
        "CONFLICT",
        "Dữ liệu vừa thay đổi hoặc yêu cầu không còn phù hợp. Vui lòng tải lại rồi thử lại.",
    ),
    422: (
        "VALIDATION_ERROR",
        "Thông tin chưa hợp lệ. Vui lòng kiểm tra các trường đã nhập.",
    ),
    429: ("RATE_LIMITED", "Bạn thao tác quá nhanh. Vui lòng chờ một chút rồi thử lại."),
    500: ("INTERNAL_ERROR", "Hệ thống đang gặp sự cố. Vui lòng thử lại sau."),
    503: ("SERVICE_UNAVAILABLE", "Dịch vụ đang tạm thời gián đoạn. Vui lòng thử lại sau."),
}


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: UUID


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class AppError(HTTPException):
    def __init__(self, status_code: int):
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        super().__init__(status_code=status_code, headers=headers)


def error_response(correlation_id: str, status: int, headers=None) -> JSONResponse:
    code, message = ERRORS.get(status, ("HTTP_ERROR", "Đã xảy ra lỗi. Vui lòng thử lại."))
    return JSONResponse(
        status_code=status,
        content=ErrorEnvelope(
            error=ErrorDetail(code=code, message=message, correlation_id=UUID(correlation_id))
        ).model_dump(mode="json"),
        headers={**(headers or {}), "Cache-Control": "no-store"},
    )


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            correlation_id = str(UUID(Headers(scope=scope).get("X-Correlation-ID", "")))
        except ValueError:
            correlation_id = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Correlation-ID"] = correlation_id
                if scope["path"].startswith(("/api/v1/", "/internal/v1/")):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_id)


class UnexpectedErrorMiddleware:
    """Keep exception bodies and tracebacks out of responses and server logs."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = finished = False

        async def track_response(message: Message) -> None:
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body":
                finished = not message.get("more_body", False)
            await send(message)

        try:
            await self.app(scope, receive, track_response)
        except Exception:  # noqa: BLE001 - final boundary for arbitrary ASGI handler failures
            logger.error("request_failed")
            if not started:
                response = error_response(scope["state"]["correlation_id"], 500)
                await response(scope, receive, send)
            elif not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        headers = exc.headers if isinstance(exc, AppError) else None
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            headers = {"Allow": exc.headers["Allow"]}
        return error_response(request.state.correlation_id, exc.status_code, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request.state.correlation_id, 422)
