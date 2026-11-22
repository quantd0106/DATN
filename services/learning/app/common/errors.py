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
    413: ("PAYLOAD_TOO_LARGE", "Tệp tải lên vượt quá dung lượng cho phép."),
    422: (
        "VALIDATION_ERROR",
        "Thông tin chưa hợp lệ. Vui lòng kiểm tra các trường đã nhập.",
    ),
    429: ("RATE_LIMITED", "Bạn thao tác quá nhanh. Vui lòng chờ một chút rồi thử lại."),
    500: ("INTERNAL_ERROR", "Hệ thống đang gặp sự cố. Vui lòng thử lại sau."),
    503: ("SERVICE_UNAVAILABLE", "Dịch vụ đang tạm thời gián đoạn. Vui lòng thử lại sau."),
}

PUBLIC_MESSAGES = {
    "REVISION_CONFLICT": "Bản nháp đã thay đổi trên máy chủ. Vui lòng tải lại trước khi lưu.",
    "GRADE_VERSION_CONFLICT": "Bản điểm đã thay đổi. Vui lòng tải lại trước khi cập nhật.",
    "CLASS_ARCHIVED": "Lớp đã lưu trữ nên không thể thực hiện thao tác mới.",
    "UNREVIEWED_QUESTIONS": "Cần duyệt toàn bộ câu hỏi trước khi công bố.",
    "NO_QUESTIONS": "Cần có ít nhất một câu hỏi trước khi công bố.",
    "ATTEMPT_EXPIRED": "Đã hết thời gian làm bài. Hệ thống đã tự động nộp các câu trả lời đã lưu.",
    "ATTEMPT_LIMIT_REACHED": "Bạn đã sử dụng hết số lượt làm bài cho phép.",
    "SUBMISSION_LIMIT_REACHED": "Bạn đã sử dụng hết số lượt nộp bài cho phép.",
    "LATE_SUBMISSION_NOT_ALLOWED": "Bài tập đã quá hạn nộp và không cho phép nộp muộn.",
    "RESUBMISSION_CLOSED": "Đã quá thời hạn nộp lại bài tập.",
    "ASSESSMENT_NOT_OPEN": "Bài kiểm tra hiện không nằm trong thời gian mở.",
    "ASSESSMENT_ALREADY_PUBLISHED": "Phiên bản đề đã công bố là bất biến. Hãy tạo phiên bản mới để chỉnh sửa.",
    "PRIMARY_APPROVAL_REQUIRED": "Chỉ giáo viên chính của lớp mới có quyền phê duyệt thao tác này.",
    "SELF_APPROVAL_FORBIDDEN": "Không thể tự phê duyệt đề xuất do chính mình tạo.",
    "APPEAL_WINDOW_EXPIRED": "Đã quá thời hạn yêu cầu xem xét lại điểm.",
    "ADMIN_REASON_REQUIRED": "Vui lòng chọn mã lý do quản trị viên hợp lệ trong X-Admin-Reason.",
    "IDEMPOTENCY_PAYLOAD_MISMATCH": "Mã xác nhận này đã được dùng cho một nội dung khác.",
    "INVALID_UPLOAD_FILE": "Tệp tải lên không đúng định dạng hoặc vượt dung lượng cho phép.",
    "NOT_IN_AUDIENCE": "Bài tập này không được giao cho tài khoản của bạn.",
    "AI_HINT_UNAVAILABLE": "Chức năng gợi ý AI đang tạm gián đoạn. Giáo viên vẫn có thể chấm bài bình thường.",
}


class ErrorDetail(BaseModel):
    code: str
    message: str
    correlation_id: UUID


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class AppError(HTTPException):
    def __init__(self, status_code: int, message_key: str | None = None):
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        super().__init__(status_code=status_code, detail=message_key, headers=headers)


class AttemptClosedError(AppError):
    def __init__(self, receipt: dict):
        super().__init__(409, "ATTEMPT_EXPIRED" if receipt["reason"] == "TIMEOUT" else None)
        self.receipt = receipt


def error_response(
    correlation_id: str, status: int, detail: str | None = None, headers=None,
    *, receipt: dict | None = None,
) -> JSONResponse:
    code, default_message = ERRORS.get(status, ("HTTP_ERROR", "Đã xảy ra lỗi. Vui lòng thử lại."))
    if status == 503 and detail == "AI_HINT_UNAVAILABLE":
        code = detail
    message = PUBLIC_MESSAGES.get(detail, default_message) if (detail and status != 404) else default_message
    body = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, correlation_id=UUID(correlation_id))
    ).model_dump(mode="json")
    if receipt is not None:
        body["receipt"] = receipt
    return JSONResponse(
        status_code=status,
        content=body,
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
                headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_id)


class UnexpectedErrorMiddleware:
    """Keep arbitrary exception bodies and tracebacks out of responses and logs."""

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
        except Exception:  # noqa: BLE001
            logger.error("request_failed")
            if not started:
                response = error_response(scope["state"]["correlation_id"], 500)
                await response(scope, receive, send)
            elif not finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = str(exc.detail) if isinstance(exc, AppError) and exc.detail else None
        headers = exc.headers if isinstance(exc, AppError) else None
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            headers = {"Allow": exc.headers["Allow"]}
        receipt = exc.receipt if isinstance(exc, AttemptClosedError) else None
        return error_response(request.state.correlation_id, exc.status_code, detail, headers, receipt=receipt)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request.state.correlation_id, 422)
