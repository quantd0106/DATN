# TASK-017A: Core Prerequisites cho Document Service (`manage_class_documents` & `auth/introspect`)

- **Mã Task:** `TASK-017A`
- **Giai đoạn:** `G3 — Document + AI (tiền đề tại Core)`
- **Trạng thái:** `IMPLEMENTED — VERIFIED`
- **Service / Module sở hữu:** `services/core/` (`identity`, `classroom`, `common`, `bootstrap.py`)
- **Giải quyết vấn đề:** `#3` (Core thiếu action chia sẻ/thu hồi tài liệu) & `#4` (Document xác thực JWT và kiểm tra `session_version` mà không chia sẻ `SECRET_KEY`)
- **Business Rules liên quan:** `B2`, `B6`, `F5`, `O1`, `K2`, `QAS-04`, `FT-07`
- **Tài liệu căn cứ:**
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 3 & 4)
  - [Core API Contract](../docs/architect/microservices-v1/core/API_CONTRACT.md)
  - [Core DESIGN](../docs/architect/microservices-v1/core/DESIGN.md)
  - [TASK-008](TASK-008-classroom-public-interface.md)

---

## 1. Mục tiêu

Bổ sung hai giao diện nội bộ còn thiếu ở Core Service trước khi triển khai Document Service:
1. Thêm action `manage_class_documents` vào `ClassAccessAction` của Classroom (`ACTIVE` class + giáo viên đang được phân công, không mượn `manage_class_calendar`).
2. Thêm endpoint nội bộ `POST /internal/v1/auth/introspect` tại Identity để các microservice khác xác thực access JWT, kiểm tra chữ ký, hạn token, `status == "ACTIVE"` và `session_version` hiện hành trong DB mà **không** cần chia sẻ `SECRET_KEY`.

---

## 2. Chi tiết kỹ thuật & tệp cần sửa

### 2.1. Classroom: Action `manage_class_documents`
- **`services/core/app/modules/classroom/schemas.py`:**
  - Thêm `MANAGE_CLASS_DOCUMENTS = "manage_class_documents"` vào `ClassAccessAction`.
- **`services/core/app/modules/classroom/public.py`:**
  - Trong `check_class_action`, thêm nhánh tường minh:
    ```python
    elif action == ClassAccessAction.MANAGE_CLASS_DOCUMENTS:
        allowed = active_class and current_teacher
    ```
  - Đổi nhánh fallback cuối từ `else:` (đang mặc định dùng cho `GRADE_SUBMITTED_WORK`) thành `elif action == ClassAccessAction.GRADE_SUBMITTED_WORK:` và bổ sung `else: allowed = False` để bảo đảm fail closed nếu enum mở rộng sau này.
  - Giữ nguyên hợp đồng trả về `ClassAccessDTO` (`allowed=False, class_status=klass.status` khi cùng trường nhưng thiếu quyền hoặc lớp `ARCHIVED`; bỏ `class_status` khi không thấy/khác trường).

### 2.2. Common & Identity: `POST /internal/v1/auth/introspect`
- **`services/core/app/common/auth_dependencies.py`:**
  - Cập nhật `verify_internal_service_key` trả `frozenset({"core:check_access", "core:introspect_auth"})` (hoặc ánh xạ theo cấu hình scope nội bộ giữ tương thích caller cũ).
- **`services/core/app/modules/identity/schemas.py`:**
  - Thêm:
    - `IntrospectTokenRequest(RequestModel)`: `token: SecretStr = Field(min_length=1, max_length=4096)` (dùng `SecretStr` để không lộ JWT vào repr/log).
    - `IntrospectedUserDTO(BaseModel)`: `id: UUID`, `status: UserStatus`, `roles: list[Role]`, `school_id: UUID | None = None`, `display_name: str | None = None` (tuyệt đối không có `password_hash`, `email`, `username`).
    - `IntrospectTokenResponse(BaseModel)`: `active: bool`, `user: IntrospectedUserDTO | None = None`, `exp: int | None = None`.
- **`services/core/app/modules/identity/public.py`:**
  - Thêm helper công khai (ví dụ `introspect_access_token_subject(session: Session, user_id: UUID) -> IntrospectedUserDTO | None` hoặc mở rộng lookup chuyên biệt) đọc `User.id`, `User.status`, `User.session_version`, `User.school_id`, `User.display_name` và `RoleAssignment.role` trong **1 câu query duy nhất** (tương tự `get_current_user_snapshot`). Trả kèm `session_version` để endpoint so sánh với `claims.session_version`.
- **`services/core/app/modules/identity/router.py` & `services/core/app/bootstrap.py`:**
  - Thêm `internal_router = APIRouter(prefix="/internal/v1", tags=["Internal"], responses=...)` vào `identity/router.py` và đăng ký trong `bootstrap.py`.
  - Định nghĩa `POST /internal/v1/auth/introspect` (`response_model=IntrospectTokenResponse`):
    1. Kiểm tra `verify_internal_service_key` và `"core:introspect_auth" in scopes` (thiếu/sai `X-Service-Key` → `401`, thiếu `INTERNAL_SERVICE_SECRET` → `503`).
    2. Giải mã token bằng `decode_access_token(body.token.get_secret_value())`:
       - Nếu thiếu `SECRET_KEY` (`RuntimeError`) → `503` (fail closed).
       - Nếu `jwt.InvalidTokenError` (sai chữ ký, hết hạn, sai claims) → trả `200 {"active": false}` với header `Cache-Control: no-store`.
    3. Tra DB qua helper của `identity.public`:
       - Nếu `user is None`, `user.status != "ACTIVE"`, `user.session_version != claims.session_version`, hoặc `not user.roles` → trả `200 {"active": false}` (`Cache-Control: no-store`).
       - Nếu DB lỗi → để lỗi hạ tầng trả `503`/`500` (không nuốt exception thành `active: false` hay `active: true`).
       - Ngược lại trả `200 IntrospectTokenResponse(active=True, user=..., exp=claims.exp)` với `Cache-Control: no-store`.
  - **Không log** token đầu vào, JWT claims thô hoặc payload body trong mọi nhánh lỗi.

---

## 3. Ràng buộc & Lưu ý quan trọng

- **Không sửa `services/core/tests/identity/openapi_contract.json`:** kiểm tra `services/core/tests/identity/test_openapi.py` chỉ lọc prefix `/api/v1/auth/`, `/api/v1/users/me`, `/api/v1/admin/students`, nên `/internal/v1/auth/introspect` không phá vỡ snapshot công khai.
- **Không sửa migration đã áp dụng (`0001..0014`):** task này không cần thêm bảng hay cột DB mới.
- **Shared files (`app/common/auth_dependencies.py`, `app/bootstrap.py`):** tuân thủ quy trình review file shared theo [CONTRIBUTING.md](../docs/CONTRIBUTING.md).
- **Không cache kết quả introspect ở P1.**

---

## 4. Tiêu chí nghiệm thu & Kiểm thử bắt buộc

- [x] `ClassAccessAction.MANAGE_CLASS_DOCUMENTS` (`"manage_class_documents"`) trả `allowed=True` cho `LECTURER` ACTIVE (`PRIMARY` hoặc `ASSISTING`) trên lớp `ACTIVE` cùng trường; trả `allowed=False` (`class_status=ARCHIVED`) trên lớp `ARCHIVED`; trả `allowed=False` trên lớp `ACTIVE` cho `STUDENT`, `ADMIN`, giáo viên hết phân công, giáo viên khác trường (không lộ `class_status`), và user `LOCKED`.
- [x] `POST /internal/v1/auth/introspect` từ chối `401` khi thiếu/sai `X-Service-Key`; trả `503` khi thiếu `INTERNAL_SERVICE_SECRET` hoặc `SECRET_KEY`.
- [x] `POST /internal/v1/auth/introspect` trả `active=True` kèm `user.{id, status, roles, school_id, display_name}` và `exp` với token hợp lệ của tài khoản `ACTIVE`.
- [x] Unit test introspection trả `active=False` khi `session_version` cũ, tài khoản `LOCKED`, token hết hạn hoặc sai chữ ký.
- [x] Kiểm chứng thu hồi access JWT sau `/auth/logout-all` trên PostgreSQL 16 qua luồng login → introspect → logout-all → introspect trả `active=false`.
- [ ] Kiểm chứng thu hồi phiên qua các mutation đổi mật khẩu, khóa tài khoản và đổi vai trò trên PostgreSQL 16.
- [x] Giả lập lỗi DB trong lúc introspect → fail closed với `503`.
- [x] Unit/contract tests trong `services/core/tests/classroom/test_contract.py` và `services/core/tests/identity/test_introspect.py`.
- [x] Chạy các ca tích hợp PostgreSQL 16 trong `services/core/tests/classroom/test_integration.py` và `test_postgres_login_introspect_and_logout_all`.

### Lệnh kiểm chứng (chạy từ `services/core`)
**Bằng chứng kiểm định (2026-11-21)** · Windows 11 · Python 3.12.5 · Node 24.15.0

- `.\.venv\Scripts\ruff.exe check --no-cache app tests migrations` → `All checks passed!` (exit 0).
- `.\.venv\Scripts\ruff.exe format --no-cache --check app tests migrations` → `148 files already formatted` (exit 0).
- `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error --cov=app.modules.identity.public --cov-branch --cov-fail-under=100 tests/test_common.py tests/classroom/test_contract.py tests/identity/test_introspect.py tests/identity/test_openapi.py tests/identity/test_public.py tests/identity/test_schemas.py tests/identity/test_credential_token.py tests/security/test_boundaries.py` → **132 passed, 1 skipped** và **100.00%** statement + branch coverage cho `app.modules.identity.public`.
- Docker Desktop 29.7.2 (linux) · PostgreSQL 16. Với `TEST_DATABASE_URL` trỏ tới `core_test` tại `127.0.0.1:55432`, `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error -vv tests/classroom/test_integration.py tests/identity/test_introspect.py` → **`25 passed in 6.69s` (0 skipped)**, gồm `test_internal_document_access_tracks_current_assignments_and_class_status` và `test_postgres_login_introspect_and_logout_all`.
- **Lỗi môi trường đã có từ trước.** Ba ca SMTP dùng `strftime` với ký tự tiếng Việt trong `tests/identity/test_http_security.py` lỗi trên C-locale Windows cp1252 thiếu UTF-8, ngoài phạm vi TASK-017A.
