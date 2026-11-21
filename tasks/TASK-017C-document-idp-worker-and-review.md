# TASK-017C: Document IDP Worker, Chấp nhận `NEEDS_REVIEW` & Thử lại phiên bản

- **Mã Task:** `TASK-017C`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `IMPLEMENTED — VERIFIED` (phụ thuộc [TASK-017B](TASK-017B-document-service-foundation.md))
- **Service / Module sở hữu:** `services/document/` (`app/worker.py`, `app/modules/versions/`) và tích hợp đọc thư viện `services/rag/idp/`
- **Giải quyết vấn đề:** `#1` (5 trạng thái backend), `#2` (lối thoát `NEEDS_REVIEW` cho fast OCR & DOCX công thức), `#7` (`SUCCESS` dừng ở Canonical + outbox `document.version.published`)
- **Business Rules liên quan:** `B3`, `B4`, `B5`, `H3`, `O1`, `F7`, `QAS-01`, `QAS-02`, `QAS-10`, `FT-05`
- **Tài liệu căn cứ:**
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 1, 2, 7)
  - [Document DESIGN](../docs/architect/microservices-v1/document/DESIGN.md#5-vòng-đời-phiên-bản-quy-tắc-h3-và-tính-dẫn-xuất-superseded)
  - [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md)
  - [services/rag/idp/ingest.py](../services/rag/idp/ingest.py)
  - [services/rag/idp/canonical.py](../services/rag/idp/canonical.py)

---

## 1. Mục tiêu

Xây dựng worker bất đồng bộ xử lý 5 định dạng tài liệu bằng pipeline IDP (`services/rag/idp`), ánh xạ chính xác kết quả sang `SUCCESS` / `NEEDS_REVIEW` / `FAILED` theo Rule H3, cung cấp thao tác **chủ sở hữu chấp nhận** (`POST .../accept`) làm lối thoát cho `NEEDS_REVIEW` và thao tác thử lại (`POST .../retry`), đồng thời phát sự kiện `document.version.published` qua transactional outbox mà không chạy lập chỉ mục AI trong Document Service.

---

## 2. Chi tiết kỹ thuật

### 2.1. Worker nền (`services/document/app/worker.py`)
- Chạy vòng lặp kép (hỗ trợ cờ `--once` phục vụ kiểm thử và CI smoke):
  1. **Job IDP (`document_versions`):** claim bằng `SELECT ... FROM document.document_versions WHERE processing_status = 'PENDING' AND available_at <= now() ORDER BY created_at ASC FOR UPDATE SKIP LOCKED LIMIT 1`. Chuyển trạng thái sang `PROCESSING`, tăng `attempt_count`, đặt `available_at = now() + lease_timeout` rồi commit lease trước khi xử lý nặng.
  2. **Outbox relay (`outbox_events`):** phát hành / đánh dấu các sự kiện chưa công bố (hoặc hỗ trợ endpoint/broker theo hợp đồng G3).
- **Cô lập lỗi và Crash recovery (`QAS-10`, `FT-05`):**
  - Mọi tệp tạm của lượt xử lý đặt trong thư mục `/tmp/doc-worker/<version_id>/` và được dọn dẹp ở khối `finally`.
  - Nếu worker bị tắt đột ngột giữa lúc `PROCESSING`, khi quá `lease_timeout` (hoặc khi khởi động lại), job quá hạn vẫn có thể được phục hồi an toàn hoặc đánh dấu `FAILED` có thông báo để chủ sở hữu `retry`. Phiên bản `SUCCESS` cũ (nếu có) **không bị ảnh hưởng** trong suốt quá trình xử lý.

### 2.2. Tích hợp `services/rag/idp` (`app/modules/versions/idp_runner.py`)
- Gọi `idp.ingest.ingest_file(source_path, work_out_dir, limits=InputLimits(max_bytes=20_971_520, max_pages=300), ...)` cho cả 5 định dạng (`PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`).
- Đọc `canonical.json` và `report.json` / `ingestion_report.json` đầu ra:
  - Tạo projection `reader_pages` + `outline` nhẹ từ `canonical["pages"]` để phục vụ nhanh `GET /classes/{class_id}/documents/{document_id}/reader`.
  - Lưu bền vững `canonical.json` (và assets nếu có) vào `StorageBackend` **trước** khi commit DB `SUCCESS` hoặc `NEEDS_REVIEW`.
  - Ghi nhận `canonical_schema_version = canonical["schema_version"]` (lưu ý: `services/rag/idp/canonical.py` hiện sinh `"idp-canonical-1"`, và harness hỗ trợ cả `"idp-canonical-1"` lẫn `"idp-canonical-2"`).
- **Quy tắc ánh xạ trạng thái:**
  1. Nếu `ingestion_auto_publish_pass is True` → `processing_status = "SUCCESS"`, `acceptance_mode = "AUTO_PUBLISHED"`, và chèn sự kiện `document.version.published` vào `outbox_events` trong cùng transaction.
  2. Nếu `ingestion_review_assisted_pass is True` (nhưng `ingestion_auto_publish_pass` là `False` — trường hợp fast OCR, fallback engine hoặc công thức/bảng DOCX) → `processing_status = "NEEDS_REVIEW"`, lưu `review_summary_json` gồm `{review_item_count, flag_codes, flagged_pages}` (tuyệt đối không lưu nội dung thô nhạy cảm), **chưa** phát `document.version.published`.
  3. Trường hợp còn lại hoặc `InputError` / exception không mong đợi → `processing_status = "FAILED"`, gán `error_code` và `error_message` tiếng Việt an toàn cho người dùng (không lộ stack trace hoặc đường dẫn nội bộ).

### 2.3. Endpoint `/accept` & `/retry` (`app/modules/versions/router.py`, `service.py`)
- **`POST /api/v1/documents/{document_id}/versions/{version_id}/accept`:**
  - Xác thực Bearer qua Core `introspect`, kiểm tra role `LECTURER` ACTIVE và `document.owner_user_id == actor.id` & cùng `school_id` (lệch trả `404` che). Giáo viên phối hợp trong lớp **không** được gọi endpoint này (`O1`).
  - Khóa `document_versions` `FOR UPDATE`: nếu `processing_status != "NEEDS_REVIEW"` → `409 CONFLICT`.
  - Kiểm tra `canonical_storage_key` & `canonical_sha256` tồn tại hợp lệ (nếu thiếu artifact fail closed `409`/`500`).
  - Cập nhật `processing_status = "SUCCESS"`, `acceptance_mode = "OWNER_ACCEPTED"`, `accepted_by_user_id = actor.id`, `accepted_at = now()`.
  - Ghi `audit_events` (`DOCUMENT_VERSION_ACCEPTED`) và `outbox_events` (`document.version.published`) **trong cùng transaction**.
- **`POST /api/v1/documents/{document_id}/versions/{version_id}/retry`:**
  - Chỉ `owner_user_id`, chỉ khi `processing_status == "FAILED"` (`409 CONFLICT` nếu trạng thái khác). Đặt lại `PENDING`, `available_at = now()`, xóa `error_code/error_message`.

---

## 3. Tiêu chí nghiệm thu & Kiểm thử bắt buộc

- [x] `DOC-T03` (PostgreSQL 16 + local storage, IDP thật): Upload thành công đủ 5 định dạng (`PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`); worker trích xuất và lưu Canonical artifact + reader projection.
- [x] `DOC-T05` (Rule H3): Có v1 `SUCCESS`, upload v2 lỗi (`FAILED`) hoặc v2 đang ở `NEEDS_REVIEW` → lớp học vẫn đọc và `active-grants` vẫn trả v1 `SUCCESS`.
- [x] Contract test luồng `NEEDS_REVIEW` với kết quả IDP có cờ review. Worker lưu Canonical artifact, chủ sở hữu gọi `/accept` → `SUCCESS` (`OWNER_ACCEPTED`), ghi audit và phát đúng 1 event `document.version.published`.
- [ ] Kiểm chứng trên Compose bằng mẫu IDP thực tế kích hoạt cờ fast OCR hoặc DOCX bảng/công thức, qua thao tác **Chấp nhận** trên trình duyệt.
- [x] Đồng giáo viên (`ASSISTING`) hoặc học sinh gọi `/accept` hoặc `/retry` bị từ chối; gọi `/accept` trên phiên bản `FAILED` hoặc `SUCCESS` trả `409`.
- [x] `QAS-10 / FT-05` (test worker trên PostgreSQL 16): giả lập crash giữa lúc xử lý v2 → không phát event `document.version.published`, v1 giữ nguyên, không mất tệp gốc.
- [x] Kiểm tra AST ranh giới Document Worker với các module chunking/embedding/BM25.
- [x] Kiểm chứng worker locking với `FOR UPDATE SKIP LOCKED` và phục hồi lease trong suite PostgreSQL 16.

### Lệnh kiểm chứng (chạy từ `services/document`)
**Bằng chứng kiểm định (2026-11-21)** · Windows 11 · Python 3.12.5 · Node 24.15.0

- `.\.venv\Scripts\ruff.exe check --no-cache app tests migrations` → `All checks passed!` (exit 0).
- `.\.venv\Scripts\ruff.exe format --no-cache --check app tests migrations` → `52 files already formatted` (exit 0).
- Docker Desktop 29.7.2 (linux) · PostgreSQL 16. Với `TEST_DATABASE_URL` trỏ tới `document_test` tại `127.0.0.1:55432`, `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error -q tests/` → **`99 passed in 11.59s`** trên PostgreSQL 16, gồm IDP thật qua `services/rag/idp/ingest.py` cho `PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`, `NEEDS_REVIEW → /accept → SUCCESS`, H3 fallback và worker lease với `FOR UPDATE SKIP LOCKED`.
- `_finish_version` trong `services/document/app/modules/versions/processing.py` giữ advisory lock và cập nhật trạng thái trong cùng session và transaction, loại bỏ rollback của savepoint lồng nhau trên PostgreSQL 16.
