# TASK-017B: Document Service Foundation, Policy, Validation, Grants & Notes

- **Mã Task:** `TASK-017B`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `IMPLEMENTED — VERIFIED` (phụ thuộc [TASK-017A](TASK-017A-core-document-prereqs.md))
- **Service sở hữu:** `services/document/`
- **Giải quyết vấn đề:** `#1` (5 trạng thái + `SUPERSEDED` dẫn xuất theo H3), `#3` (dùng `manage_class_documents`), `#4` (xác thực qua `/internal/v1/auth/introspect`), `#8` (chốt 20 MB / 300 trang qua `GET /api/v1/documents/policy`)
- **Business Rules liên quan:** `B1`, `B2`, `B3`, `B4`, `B6`, `B7`, `G5`, `H3`, `H6`, `L6`, `O1`, `O2`, `F7`, `K2`, `QAS-04`
- **Tài liệu căn cứ:**
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 1, 3, 4, 8)
  - [Document DESIGN](../docs/architect/microservices-v1/document/DESIGN.md)
  - [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md)
  - [Document FOLDER STRUCTURE](../docs/architect/microservices-v1/document/FOLDER_STRUCTURE.md)
  - [Test Cases DOC-T01..T13](../docs/architect/lumi-design/17_Test_Cases.md#document-service--future-proposal)

---

## 1. Mục tiêu

Khởi tạo microservice độc lập `services/document/` (FastAPI + SQLAlchemy 2 + Alembic schema `document`) với đầy đủ nghiệp vụ quản lý thư viện tài liệu cá nhân, kiểm tra tệp tải lên theo chính sách `20 MB / 300 trang`, phân quyền chia sẻ lớp qua Core, đọc/tải tài liệu theo grant hiện hành, ghi chú/bookmark gắn nguồn và hai internal endpoint phục vụ AI Service.

---

## 2. Chi tiết kỹ thuật

### 2.1. Hạ tầng lõi (`app/common/` & `migrations/`)
- Tạo cấu trúc theo [Document FOLDER STRUCTURE](../docs/architect/microservices-v1/document/FOLDER_STRUCTURE.md).
- **`migrations/`:** tạo migration `0001` khởi tạo schema `document` và 7 bảng (`documents`, `document_versions`, `class_document_grants`, `source_bound_notes`, `document_bookmarks`, `outbox_events`, `audit_events`) với đầy đủ check constraints, partial unique index `uq_active_class_document_grant` (`WHERE is_active = true`) và index tìm kiếm/claim worker.
- **`app/common/config.py`:** cấu hình `DATABASE_URL`, `CORE_API_URL`, `INTERNAL_SERVICE_SECRET`, `DOCUMENT_STORAGE_PATH`, `DOCUMENT_MAX_SIZE_BYTES=20971520`, `DOCUMENT_MAX_PAGES=300`, `CORE_CLIENT_TIMEOUT_SECONDS=3.0`.
- **`app/common/core_client.py` & `app/common/auth.py`:**
  - `introspect_token(token)` gọi `POST {CORE_API_URL}/internal/v1/auth/introspect` với `X-Service-Key`. Không cache ở P1. `active != true` → `401`; lỗi mạng/timeout/5xx → `503`.
  - `check_class_access(class_id, user_id, action)` gọi `GET {CORE_API_URL}/internal/v1/classes/{class_id}/access` với `X-Service-Key`. Ánh xạ lỗi denied: `class_status == "ARCHIVED"` → `409`; `class_status is None` → `404`; `class_status == "ACTIVE"` → `403`; lỗi Core/timeout → `503` (fail closed).
- **`app/common/storage.py`:** interface `StorageBackend` + cài đặt `LocalVolumeStorage` ghi tệp tạm rồi `os.replace` nguyên tử, tính `SHA-256`, chặn path traversal.
- **`app/common/errors.py`:** giữ chuẩn `ErrorEnvelope` (`{error:{code,message,correlation_id}}`), `X-Correlation-ID`, `Cache-Control: no-store`, không lộ stack trace hay token.
- **`app/modules/health/`:** `GET /health/live` (`200`) và `GET /health/ready` (kiểm tra kết nối PostgreSQL schema `document` + quyền ghi/đọc `StorageBackend`, ngược lại `503`).

### 2.2. Chính sách upload, validation & Thư viện (`app/modules/library/`)
- `GET /api/v1/documents/policy`: trả `max_size_bytes: 20971520`, `max_pages: 300`, `allowed_formats`, `allowed_extensions`, `allowed_mime_types`.
- `validation.py` kiểm tra đầu vào (gọi trước khi ghi DB `PENDING`):
  - Tệp rỗng (`0 bytes`) → `422`; vượt `max_size_bytes` (`20 MB`) → `413 PAYLOAD_TOO_LARGE`.
  - Kiểm tra extension + magic-byte (`%PDF-`, Office ZIP `word/document.xml` hoặc `ppt/presentation.xml`, text UTF-8/UTF-16 hợp lệ cho `.txt`/`.md`/`.markdown`).
  - Bảo vệ ZIP-bomb cho DOCX/PPTX (giới hạn tổng kích thước giải nén `<= 100 MB`, `<= 2000` entries, tỷ lệ nén an toàn).
  - Kiểm tra PDF bằng PyMuPDF/`pdfinfo`: từ chối PDF mã hóa (`ENCRYPTED_PDF`), PDF 0 trang, hoặc PDF > `300` trang (`422`).
  - Kiểm tra số slide PPTX (`ppt/slides/slide*.xml`) `<= 300` (`422`).
- `GET /api/v1/documents`, `POST /api/v1/documents`, `GET /api/v1/documents/{document_id}`, `PATCH /api/v1/documents/{document_id}`, `DELETE /api/v1/documents/{document_id}`:
  - Chỉ `LECTURER` ACTIVE có `school_id`; xem/sửa/xóa chỉ `owner_user_id == actor.id` và cùng `school_id` (lệch → `404`).
  - `DELETE`: nếu có `active_grant_count >= 1` và thiếu `?confirm_multi_class=true` → trả `409 CONFLICT` kèm `active_grant_count` và `affected_class_ids`. Khi xác nhận: set `deleted_at = now()`, set `is_active = false` cho mọi grant đang mở, ghi audit `DOCUMENT_DELETED` và outbox `document.deleted` trong cùng transaction.

### 2.3. Quản lý phiên bản dẫn xuất (`app/modules/versions/` — phần API read/upload)
- `POST /api/v1/documents/{document_id}/versions`: validate tệp theo mục 2.2, lưu tệp gốc, tạo `document_versions` với `version_number = max + 1`, `processing_status = 'PENDING'`.
- Tính `serving_version` = dòng `SUCCESS` có `version_number` lớn nhất; gắn `is_serving` và `is_superseded` trên `DocumentVersionDTO` mà **không** lưu `SUPERSEDED` vào DB (Rule H3).
- (Xử lý worker IDP, `/accept` và `/retry` thuộc [TASK-017C](TASK-017C-document-idp-worker-and-review.md)).

### 2.4. Chia sẻ lớp, Reader, Download & Internal API (`app/modules/grants/`)
- `POST /api/v1/classes/{class_id}/documents/grants`: kiểm tra Core `manage_class_documents`; kiểm tra tài liệu thuộc cùng trường, `deleted_at IS NULL`, actor là `owner_user_id` (hoặc có quyền chia sẻ hợp lệ theo B2/O1) và có ít nhất 1 phiên bản `SUCCESS`; idempotent khi grant đã `is_active = true`; ghi audit và outbox `document.grant.changed`.
- `DELETE /api/v1/classes/{class_id}/documents/grants/{document_id}`: kiểm tra Core `manage_class_documents`; cập nhật `is_active = false`, `revoked_at = now()`, `revoked_by_user_id = actor.id`; ghi audit và outbox `document.grant.changed`.
- `GET /api/v1/classes/{class_id}/documents`: kiểm tra Core `read_document_source`; trả các tài liệu có grant `is_active = true`, `deleted_at IS NULL`, và có `serving_version`.
- `GET /api/v1/classes/{class_id}/documents/{document_id}/reader`:
  - Kiểm tra Core `read_document_source` + grant hiện hành + `deleted_at IS NULL`: nếu hợp lệ, trả nội dung trang/outline từ Canonical projection + notes của actor.
  - Nếu grant đã thu hồi hoặc tài liệu đã xóa mềm nhưng actor là chủ note/bookmark lịch sử trong lớp (kiểm tra `read_own_history` hoặc ownership note): trả `is_source_accessible = false`, `outline = []`, `pages = []` và notes đã che `source_quote = null` (H6/L6). Ngoài phạm vi → `404`.
- `GET /api/v1/classes/{class_id}/documents/{document_id}/download`: bắt buộc `read_document_source` + active grant + `deleted_at IS NULL` + phiên bản `SUCCESS`; phát stream nhị phân với `Cache-Control: no-store`.
- Internal API (yêu cầu `X-Service-Key`):
  - `GET /internal/v1/classes/{class_id}/documents/active-grants`
  - `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical`

### 2.5. Ghi chú và Dấu trang cá nhân (`app/modules/notes/`)
- `GET /api/v1/users/me/notes`: owner-only; đối soát từng `(class_id, document_id)` với Core `read_document_source` + active grant + `deleted_at IS NULL`; nếu mất quyền đặt `is_source_accessible = false` và `source_quote = null` (giữ `note_text`, H6/L6).
- `POST /api/v1/classes/{class_id}/notes`: kiểm tra `read_document_source` + active grant + `deleted_at IS NULL` (hỗ trợ cả lớp `ARCHIVED` theo O2).
- `PATCH /api/v1/notes/{note_id}`, `DELETE /api/v1/notes/{note_id}`: owner-only (`note.user_id == actor.id`); cho sửa `note_text` trên lớp `ARCHIVED` hoặc sau khi nguồn bị thu hồi (không sửa `source_quote`).
- `GET /api/v1/users/me/bookmarks` và `POST /api/v1/classes/{class_id}/bookmarks/toggle`.

---

## 3. Tiêu chí nghiệm thu & Kiểm thử bắt buộc

- [x] Chạy `DOC-T01, DOC-T02, DOC-T04, DOC-T06..DOC-T13` trong suite PostgreSQL 16, gồm migration và worker locking.
- [x] Xác minh cô lập DB role trong Compose. `document_svc` bị từ chối đọc `core.users`. Xem [TASK-017E](TASK-017E-compose-demo-and-deployment.md).
- [x] Tệp `> 20 MB` bị trả `413`; tệp `> 300 trang`, PDF mã hóa, file giả mạo MIME và ZIP-bomb bị từ chối `422` trước khi lưu `PENDING`.
- [x] Contract test thu hồi grant có hiệu lực ở lượt gọi kế tiếp. Danh sách lớp và `active-grants` loại nguồn, `reader` che nội dung, `download` từ chối và `GET /users/me/notes` che `source_quote`.
- [x] Lớp `ARCHIVED` chặn `POST`/`DELETE` grant với `409`, nhưng vẫn cho học sinh sửa/tạo note cá nhân với nguồn còn quyền (`O2`).
- [x] Contract test giả lập Core `/internal/v1/auth/introspect` hoặc `/internal/v1/classes/{id}/access` timeout/5xx → `503` (fail closed). Core báo token đã bị thu hồi → `401`.

### Lệnh kiểm chứng (chạy từ `services/document`)
**Bằng chứng kiểm định (2026-11-21)** · Windows 11 · Python 3.12.5 · Node 24.15.0

- `.\.venv\Scripts\ruff.exe check --no-cache app tests migrations` → `All checks passed!` (exit 0).
- `.\.venv\Scripts\ruff.exe format --no-cache --check app tests migrations` → `52 files already formatted` (exit 0).
- Docker Desktop 29.7.2 (linux) · PostgreSQL 16. Với `TEST_DATABASE_URL` trỏ tới `document_test` tại `127.0.0.1:55432`, `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error -q tests/` → **`99 passed in 11.59s`** trên PostgreSQL 16, gồm Alembic `upgrade head`/`downgrade base`, worker lease với `FOR UPDATE SKIP LOCKED`, H6/L6/O2 và packaging contract. Bản sửa `_finish_version` dùng một session và transaction cho advisory lock và cập nhật trạng thái. Xem [TASK-017C](TASK-017C-document-idp-worker-and-review.md).
