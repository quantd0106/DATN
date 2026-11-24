# Document Service API Contract v1

**Trạng thái P0/P1:** `IMPLEMENTED — VERIFIED` (đã tích hợp Core và Web live qua BFF proxy `/api/v1`)

Ngày 2026-11-21, suite Document trên PostgreSQL 16 đạt **`99 passed in 11.59s`**. Trong Compose trên Docker Desktop 29.7.2 (linux), Document `/health/ready` trả `200`. PDF `10,500,004` byte đi qua Web proxy từ `201 PENDING` đến `SUCCESS`, cấp grant trả `201`, và Student đọc/tải trả `200` với bản tải đủ `10,500,004` byte. Tệp `20,971,521` byte bị từ chối `413`. Tài liệu này khóa **22 endpoint public** (+ **2 endpoint internal** cho AI), ánh xạ 1-1 tới **14 hàm** trong `DataAdapter['document']` (`apps/web/src/lib/data/port.ts`) và các tuyến `workspace.load` qua Proxy allowlist (trong đó `accept` và `retry` qua proxy dùng **`version_id` UUID**). Xem [TASK-017](../../../../tasks/TASK-017-document-service-design-impl.md) và [TASK-017E](../../../../tasks/TASK-017E-compose-demo-and-deployment.md).

Quyết định thiết kế tại [ADR-002](../ADR-002-document-service.md).

**Tài liệu căn cứ:** [DESIGN.md](DESIGN.md), [Core API Contract](../core/API_CONTRACT.md), [BUSINESS_RULES.md](../../BUSINESS_RULES.md).

---

## 1. Quy ước chung và ánh xạ Web Live

- **Health (không dùng prefix `/api/v1`):**
  - `GET /health/live` → `200 {"status": "live"}`
  - `GET /health/ready` → `200 {"status": "ready", "db": "connected", "storage": "ready"}` khi PostgreSQL schema `document` và `StorageBackend` khả dụng; ngược lại trả `503 SERVICE_UNAVAILABLE`.
- **Public API (`/api/v1`):** truy cập từ trình duyệt thông qua Next.js proxy same-origin (`/api/v1/*` → `DOCUMENT_API_URL`), xác thực bằng `Authorization: Bearer <access_jwt>` (Document kiểm tra qua Core `POST /internal/v1/auth/introspect`).
- **Internal API (`/internal/v1`):** chỉ dành cho giao tiếp liên service (AI Service → Document), bắt buộc header `X-Service-Key` (`INTERNAL_SERVICE_SECRET`), bị chặn ở Web proxy.
- **Định danh & thời gian:** Mọi ID trên đường dẫn đều là **UUID** (khớp ràng buộc kiểm tra UUID của `apps/web/src/lib/api/operations.ts`); thời gian chuẩn ISO-8601 UTC.
- **Pagination:** `?limit=20&cursor=<opaque>` (`limit` `1..100`), trả `{"items": [...], "next_cursor": null | "<opaque>"}`. Cursor thuộc phạm vi người gọi/lớp khác hoặc sai định dạng trả `400 BAD_REQUEST`.
- **Error Envelope & Headers:** chuẩn thống nhất giống Core:
  ```json
  {"error": {"code": "FORBIDDEN", "message": "Bạn không có quyền thực hiện thao tác này.", "correlation_id": "uuid"}}
  ```
  Mọi response mang `X-Correlation-ID` và `Cache-Control: no-store`. Không phản chiếu token hoặc nội dung tài liệu trong thông báo lỗi.
- **Quy ước ánh xạ Web Live Decoder (`apps/web/src/lib/data/live-document.ts`):**
  - Backend `processing_status`: `PENDING | PROCESSING | SUCCESS | NEEDS_REVIEW | FAILED` → Web map **`SUCCESS` → `'READY'`**; giữ nguyên `PENDING`, `PROCESSING`, `NEEDS_REVIEW`, `FAILED`.
  - Backend `format`: `PDF | PPTX | DOCX | TXT | MARKDOWN` → Web map **`MARKDOWN` → `'MD'`**.
  - Backend `DocumentVersionDTO` trả `is_serving: bool` và `is_superseded: bool` tính dẫn xuất từ quy tắc **H3** (`SUPERSEDED` không lưu trong DB).

---

## 2. Public Endpoints (`/api/v1`)

### 2.1. Chính sách upload và Thư viện cá nhân

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `GET` | `/documents/policy` | Bearer ACTIVE; không cần phân trang → `200 DocumentPolicyResponse` |
| `GET` | `/documents` | Bearer `LECTURER` ACTIVE (có `school_id`); `?limit=&cursor=&status=&format=&search=` → `200 Page[DocumentSummaryDTO]` |
| `POST` | `/documents` | Bearer `LECTURER` ACTIVE (có `school_id`); `multipart/form-data`: `file` (bắt buộc), `title` (1..200, bắt buộc), `description?` (0..2000) → `201 DocumentSummaryDTO` |
| `GET` | `/documents/{document_id}` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường → `200 DocumentDetailResponse` |
| `GET` | `/documents/{document_id}/reader` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; `?version=` → `200 DocumentReaderDTO` (xem trước phiên bản `SUCCESS` hoặc `NEEDS_REVIEW`) |
| `GET` | `/documents/{document_id}/download` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; `?version=` → `200` binary stream |
| `PATCH` | `/documents/{document_id}` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; JSON `{title?, description?}` → `200 DocumentSummaryDTO` |
| `DELETE` | `/documents/{document_id}` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; `?confirm_multi_class=true\|false` → `204` hoặc `409 CONFLICT` |
| `POST` | `/documents/{document_id}/versions` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; `multipart/form-data`: `file` → `201 DocumentVersionDTO` |
| `POST` | `/documents/{document_id}/versions/{version_id}/accept` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; body `{}` (tùy chọn) → `200 DocumentVersionDTO` |
| `POST` | `/documents/{document_id}/versions/{version_id}/retry` | Bearer `LECTURER` ACTIVE + là `owner_user_id` cùng trường; body `{}` (tùy chọn) → `200 DocumentVersionDTO` |

**Chi tiết hợp đồng chính:**
- `GET /documents/policy` trả về:
  ```json
  {
    "max_size_bytes": 20971520,
    "max_pages": 300,
    "allowed_formats": ["PDF", "PPTX", "DOCX", "TXT", "MARKDOWN"],
    "allowed_extensions": [".pdf", ".pptx", ".docx", ".txt", ".md", ".markdown"],
    "allowed_mime_types": [
      "application/pdf",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "text/plain",
      "text/markdown"
    ]
  }
  ```
- **Kiểm tra upload (`POST /documents`, `POST /documents/{document_id}/versions`):**
  - Vượt `max_size_bytes` (`20 MB`) → `413 PAYLOAD_TOO_LARGE` (`FILE_TOO_LARGE`).
  - Rỗng (`0 bytes`), sai phần mở rộng, không khớp magic-byte, ZIP-bomb, PDF mã hóa, hoặc PDF/PPTX vượt `max_pages` (`300`) → `422 VALIDATION_ERROR`.
  - Khi thêm phiên bản mới (`POST .../versions`), định dạng tệp mới phải tương thích hoặc tuân thủ 5 định dạng được hỗ trợ; phiên bản mới bắt đầu ở `PENDING` với `version_number = max + 1`. Phiên bản `SUCCESS` trước đó (nếu có) vẫn giữ nguyên `is_serving = true`.
- **Chấp nhận phiên bản cần rà soát (`POST .../versions/{version_id}/accept`):**
  - Chỉ thành công khi `processing_status == "NEEDS_REVIEW"` và Canonical artifact tồn tại hợp lệ. Nếu trạng thái là `PENDING`, `PROCESSING`, `SUCCESS` hoặc `FAILED` → trả `409 CONFLICT`.
  - Chỉ `owner_user_id` được phép gọi; giáo viên khác (kể cả `ASSISTING` trong lớp đang chia sẻ) nhận `404 NOT_FOUND` (che thư viện riêng).
  - Sau khi chấp nhận: `processing_status = "SUCCESS"`, `acceptance_mode = "OWNER_ACCEPTED"`, ghi audit `DOCUMENT_VERSION_ACCEPTED` và outbox `document.version.published`.
- **Thử lại phiên bản lỗi (`POST .../versions/{version_id}/retry`):**
  - Chỉ hợp lệ khi `processing_status == "FAILED"`, ngược lại trả `409 CONFLICT`. Đặt lại `PENDING` và reset `error_code/error_message`.
- **Xóa tài liệu đang chia sẻ (`DELETE /documents/{document_id}`):**
  - Nếu tài liệu đang có `active_grant_count >= 1` và không có `confirm_multi_class=true` → trả `409 CONFLICT` với thông tin ảnh hưởng (`active_grant_count` và danh sách `affected_class_ids`) theo Rule **B6** và `DOC-T02`.
  - Khi xác nhận xóa: soft-delete `documents.deleted_at`, thu hồi ngay mọi `class_document_grants` đang hoạt động (`is_active = false`), ghi audit và outbox `document.deleted`.

### 2.2. Chia sẻ tài liệu theo lớp & Đọc/Tải tài liệu lớp

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `POST` | `/classes/{class_id}/documents/grants` | Core `manage_class_documents` + actor là owner nguồn hoặc có quyền chia sẻ hợp lệ; JSON `{document_id: UUID}` → `201` (hoặc `200` nếu đã active) `ClassDocumentGrantDTO` |
| `DELETE` | `/classes/{class_id}/documents/grants/{document_id}` | Core `manage_class_documents` → `204` (hiệu lực ngay) |
| `GET` | `/classes/{class_id}/documents` | Core `read_document_source`; `?limit=&cursor=&format=&search=` → `200 Page[ClassDocumentSummaryDTO]` |
| `GET` | `/classes/{class_id}/documents/{document_id}/reader` | Core `read_document_source` (hoặc kiểm tra `read_own_history` theo chính sách bên dưới); `?version=` → `200 DocumentReaderDTO` |
| `GET` | `/classes/{class_id}/documents/{document_id}/download` | Core `read_document_source` + grant đang active + `deleted_at IS NULL`; `?version=` → `200` binary stream |

**Ràng buộc quan trọng:**
- Không được cấp grant (`POST /classes/{class_id}/documents/grants`) nếu tài liệu chưa có bất kỳ phiên bản `SUCCESS` nào (`409 CONFLICT`), hoặc tài liệu thuộc trường khác (`404 NOT_FOUND`), hoặc lớp đang `ARCHIVED` (`409 CONFLICT` từ quyết định Core `manage_class_documents`).
- Với `DELETE .../grants/{document_id}`: nếu lớp `ARCHIVED` trả `409 CONFLICT`; nếu không có quyền trên lớp `ACTIVE` trả `403 FORBIDDEN`; nếu lớp không tồn tại/khác trường trả `404 NOT_FOUND`. Thu hồi grant có hiệu lực ngay lập tức: lần gọi `GET .../reader`, `GET .../download` hoặc `GET /internal/v1/.../active-grants` kế tiếp lập tức từ chối/loại bỏ.
- `GET .../reader`:
  - Khi nguồn còn grant và người dùng đạt `read_document_source`: trả `is_source_accessible: true`, `serving_version`, `viewing_version`, `outline`, `pages` (đọc từ Canonical projection, mặc định chọn `serving_version` nếu không truyền `?version=`), và `notes` của chính actor tại tài liệu/lớp đó.
  - Khi nguồn đã bị thu hồi hoặc xóa mềm nhưng actor là chủ của các note cá nhân trước đó (`read_own_history` / owner-only): trả `is_source_accessible: false`, `outline: []`, `pages: []` (tuyệt đối không rò nội dung nguồn), kèm danh sách `notes` với **`source_quote: null`** theo Rule **H6/L6**.
  - Khi người dùng chưa từng có quyền/lịch sử tại lớp hoặc khác trường: trả `404 NOT_FOUND`.
- `GET .../download`: luôn xác minh `read_document_source` + `class_document_grants.is_active == true` + `deleted_at IS NULL` ngay tại thời điểm phục vụ stream; trả `403`/`404` nếu bị thu hồi, không bao giờ phát file gốc nếu mất quyền.

### 2.3. Ghi chú và Dấu trang cá nhân (Source-Bound Notes & Bookmarks)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `GET` | `/users/me/notes` | Bearer ACTIVE; `?limit=&cursor=&class_id=&document_id=` → `200 Page[PersonalNoteDTO]` |
| `POST` | `/classes/{class_id}/notes` | Core `read_document_source` + active grant + nguồn chưa xóa; JSON `{document_id, version_number?, locator, page_number?, note_text, source_quote?}` → `201 PersonalNoteDTO` |
| `PATCH` | `/notes/{note_id}` | Owner-only (`note.user_id == actor.id`); JSON `{note_text}` → `200 PersonalNoteDTO` |
| `DELETE` | `/notes/{note_id}` | Owner-only (`note.user_id == actor.id`) → `204` |
| `GET` | `/users/me/bookmarks` | Bearer ACTIVE; `?limit=&cursor=&class_id=` → `200 Page[BookmarkDTO]` |
| `POST` | `/classes/{class_id}/bookmarks/toggle` | Core `read_document_source` + active grant + nguồn chưa xóa; JSON `{document_id, version_number?, locator?, page_number?}` → `200 {"bookmarked": bool, "bookmark": BookmarkDTO \| null}` |

- **Rule H6 / L6 / O2:**
  - `GET /users/me/notes` và `PATCH /notes/{note_id}` kiểm tra lại quyền nguồn hiện hành `(class_id, document_id)` (grant active + `deleted_at IS NULL` + Core `read_document_source`). Nếu mất quyền nguồn, `is_source_accessible = false` và `source_quote = null`, đồng thời **`note_text` vẫn đọc/sửa/xóa được** bởi chủ sở hữu.
  - Khi lớp ở trạng thái `ARCHIVED` nhưng grant nguồn còn hiệu lực, `read_document_source` vẫn cho phép học sinh tạo/sửa ghi chú và bookmark cá nhân (Rule **O2**).
  - Không bao giờ chấp nhận `user_id` hoặc `is_source_accessible` từ body client.

---

## 3. Internal Endpoints (`/internal/v1`) — Phục vụ AI Service

| Method | Endpoint | Xác thực & Scope → Kết quả |
|---|---|---|
| `GET` | `/internal/v1/classes/{class_id}/documents/active-grants` | `X-Service-Key` (`document:read_grants`); `?school_id=` (tùy chọn kiểm tra chéo) → `200 ActiveGrantsResponse` |
| `GET` | `/internal/v1/documents/{document_id}/versions/{version_id}/canonical` | `X-Service-Key` (`document:read_canonical`) → `200 CanonicalArtifactDTO` |

- `GET /internal/v1/classes/{class_id}/documents/active-grants` trả về:
  ```json
  {
    "class_id": "uuid",
    "items": [
      {
        "document_id": "uuid",
        "school_id": "uuid",
        "title": "Hàm số bậc hai",
        "format": "PDF",
        "serving_version_id": "uuid",
        "serving_version_number": 2,
        "canonical_schema_version": "idp-canonical-1",
        "canonical_sha256": "hex64"
      }
    ]
  }
  ```
  Chỉ bao gồm tài liệu có `documents.deleted_at IS NULL`, `class_document_grants.is_active == true`, và có ít nhất 1 phiên bản `SUCCESS` (`serving_version`).
- `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` chỉ trả Canonical artifact khi tài liệu `deleted_at IS NULL` và phiên bản có `processing_status == "SUCCESS"` (trạng thái khác trả `409 CONFLICT`, không tồn tại trả `404 NOT_FOUND`).

---

## 4. Hợp đồng Web Proxy Allowlist (`apps/web/src/lib/api/operations.ts` & `proxy.ts`)

Mọi route Document qua Next.js proxy `/api/v1/*` đều định tuyến tới `DOCUMENT_API_URL`, truyền body nhị phân qua `arrayBuffer()` cho multipart upload, và áp dụng đúng kiểm tra UUID + query parameter allowlist:

| OperationId | Method | Path Template | Upstream | Query Allowlist | Success Status |
|---|---|---|---|---|---|
| `documentPolicy` | `GET` | `/documents/policy` | `document` | — | `200` |
| `documents` | `GET` | `/documents` | `document` | `cursor`, `limit`, `status`, `format`, `search` | `200` |
| `createDocument` | `POST` | `/documents` | `document` | — | `201` |
| `documentDetail` | `GET` | `/documents/:documentId` | `document` | — | `200` |
| `libraryDocumentReader` | `GET` | `/documents/:documentId/reader` | `document` | `version` | `200` |
| `libraryDocumentDownload` | `GET` | `/documents/:documentId/download` | `document` | `version` | `200` |
| `updateDocument` | `PATCH` | `/documents/:documentId` | `document` | — | `200` |
| `deleteDocument` | `DELETE` | `/documents/:documentId` | `document` | `confirm_multi_class` | `204` |
| `uploadDocumentVersion` | `POST` | `/documents/:documentId/versions` | `document` | — | `201` |
| `acceptDocumentVersion` | `POST` | `/documents/:documentId/versions/:versionId/accept` | `document` | — | `200` |
| `retryDocumentVersion` | `POST` | `/documents/:documentId/versions/:versionId/retry` | `document` | — | `200` |
| `grantClassDocument` | `POST` | `/classes/:classId/documents/grants` | `document` | — | `201` (hoặc `200`) |
| `revokeClassDocument` | `DELETE` | `/classes/:classId/documents/grants/:documentId` | `document` | — | `204` |
| `classDocuments` | `GET` | `/classes/:classId/documents` | `document` | `cursor`, `limit`, `format`, `search` | `200` |
| `classDocumentReader` | `GET` | `/classes/:classId/documents/:documentId/reader` | `document` | `version` | `200` |
| `classDocumentDownload` | `GET` | `/classes/:classId/documents/:documentId/download` | `document` | `version` | `200` |
| `myNotes` | `GET` | `/users/me/notes` | `document` | `cursor`, `limit`, `class_id`, `document_id` | `200` |
| `createClassNote` | `POST` | `/classes/:classId/notes` | `document` | — | `201` |
| `updateNote` | `PATCH` | `/notes/:noteId` | `document` | — | `200` |
| `deleteNote` | `DELETE` | `/notes/:noteId` | `document` | — | `204` |
| `myBookmarks` | `GET` | `/users/me/bookmarks` | `document` | `cursor`, `limit`, `class_id` | `200` |
| `toggleClassBookmark` | `POST` | `/classes/:classId/bookmarks/toggle` | `document` | — | `200` |

> **Lưu ý về `grantClassDocument`:** nếu API trả `201` cho tạo grant mới và `200` cho trường hợp idempotent (hoặc thống nhất `200`/`201` qua `successStatuses`), `TASK-017D` cần cấu hình `http.ts` chấp nhận cả `200` và `201` (hoặc Backend luôn trả `201` / `200` nhất quán) theo đúng contract cuối. Khuyến nghị thống nhất Backend trả `201` khi cấp mới hoặc tái kích hoạt và `200` không lỗi khi đã active, hoặc Web cho phép `[200, 201]` cho `grantClassDocument`.
