# TASK-017E: Docker Compose Demo Bảo vệ & Lộ trình P2 GCS Signed URL

- **Mã Task:** `TASK-017E`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `IMPLEMENTED — VERIFIED` (phụ thuộc [TASK-017B](TASK-017B-document-service-foundation.md), [TASK-017C](TASK-017C-document-idp-worker-and-review.md), [TASK-017D](TASK-017D-web-proxy-and-document-ui.md))
- **Phạm vi tệp:** `compose.yaml`, `.env.example`, `apps/web/.env.example`, `services/document/Dockerfile`, script init schema/role PostgreSQL
- **Giải quyết vấn đề:** `#6` (Giới hạn body của Vercel Functions ~4,5 MB chặn tệp mẫu 10 MB TP-01 khi chạy cloud → Demo bảo vệ chạy bằng Docker Compose; Upload trực tiếp qua signed URL lên GCS để sang P2)
- **Business Rules & QAS liên quan:** `B3`, `B4`, `B6`, `TP-01 (10 MB sample)`, `QAS-01`, `QAS-02`, `QAS-10`
- **Tài liệu căn cứ:**
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 6)
  - [22_ENVIRONMENTS_DEPLOYMENT.md](../docs/architect/lumi-design/22_ENVIRONMENTS_DEPLOYMENT.md)
  - [QUALITY_ATTRIBUTES.md](../docs/architect/microservices-v1/QUALITY_ATTRIBUTES.md#1-cấu-hình-kiểm-thử-tp-01)

---

## 1. Mục tiêu

Hoàn thiện cấu hình đóng gói Docker và `compose.yaml` để chạy toàn bộ hệ thống demo bảo vệ đồ án (Web + Core + Core Worker + Document API + Document Worker + PostgreSQL 16 + volume lưu tệp cục bộ) trên môi trường sạch, cho phép upload mượt mà tệp mẫu `10 MB` của `TP-01` và tới trần `20 MB` mà không bị giới hạn payload serverless của Vercel. Đồng thời ghi nhận rõ hợp đồng/ràng buộc hoãn sang P2 cho upload trực tiếp qua GCS signed URL.

---

## 2. Chi tiết kỹ thuật

### 2.1. `services/document/Dockerfile`
- Multi-stage build Python 3.12-slim, chạy với user không có quyền root (`non-root`, ví dụ `uid 10002`), cài wheel từ `pyproject.toml`.
- Cài đặt công cụ hệ thống cần thiết cho `services/rag/idp/` trong runtime image:
  - `poppler-utils` (`pdfinfo`, `pdftoppm`) cho kiểm tra số trang và render trang PDF.
  - `tesseract-ocr` + `tesseract-ocr-vie` + `tesseract-ocr-eng` cho luồng OCR dự phòng.
  - `pandoc` (hoặc `pypandoc_binary`) nếu xử lý DOCX OMML → LaTeX theo [services/rag/idp/documents/digital.py](../services/rag/idp/documents/digital.py).
- Dùng chung image cho 3 vai trò trong Compose (`document-migrate`, `document`, `document-worker`) giống mô hình `core`.

### 2.2. Cập nhật `compose.yaml` & `.env.example`
- **Khởi tạo DB role & schema cô lập:** bảo đảm PostgreSQL tạo schema `document` và role riêng cho Document Service (không cấp quyền `SELECT/INSERT/UPDATE/DELETE` sang bảng trong schema `core` — thực thi đúng ADR-001).
- **Thêm 3 định nghĩa vào `compose.yaml`:**
  1. `document-migrate`: chạy `python -m alembic upgrade head` trên schema `document` (`depends_on: db healthy`).
  2. `document`: FastAPI service (`127.0.0.1:${DOCUMENT_PORT:-8001}:8001`), gắn `CORE_API_URL=http://core:8000`, `INTERNAL_SERVICE_SECRET`, `DOCUMENT_DATABASE_URL`, `DOCUMENT_STORAGE_PATH=/var/lib/lumi-document`, `DOCUMENT_MAX_SIZE_BYTES=20971520`, `DOCUMENT_MAX_PAGES=300`, mount volume `document_data:/var/lib/lumi-document`, healthcheck `/health/ready`, `read_only: true` + `tmpfs: [/tmp]`, `no-new-privileges`, `cap_drop: [ALL]`.
  3. `document-worker`: chạy `python -m app.worker`, mount cùng volume `document_data:/var/lib/lumi-document` (`depends_on: db healthy, document-migrate completed_successfully`).
- **Cập nhật `web` trong `compose.yaml` & `apps/web/.env.example`:**
  - Truyền `DOCUMENT_API_URL: ${DOCUMENT_API_URL:-http://document:8001}` cho container `web`.
  - Thêm `document: condition: service_healthy` vào `depends_on` của `web` (hoặc giữ `web` vẫn khởi động kể cả khi `document` dừng nếu muốn kiểm thử `QAS-01`/`QAS-02` — cân nhắc kiểm thử cô lập lỗi bằng `docker compose stop document`).
- **`.env.example` & `apps/web/.env.example`:** bổ sung biến mẫu `DOCUMENT_PORT=8001`, `DOCUMENT_API_URL=http://localhost:8001`, `DOCUMENT_DATABASE_URL=...` (không chứa secret thật).

### 2.3. Lộ trình P2 — Direct-to-GCS Signed URL Upload (DEFERRED, không code ở P1)
Khi triển khai Web trên Vercel + Cloud Run ở P2:
- Giới hạn request body của Vercel Functions (~4,5 MB) sẽ chặn upload nhị phân trực tiếp qua `/api/v1/documents` đối với tệp 10–20 MB.
- Thiết kế chuyển tiếp cho P2 sẽ thay bước gửi binary qua Next.js bằng 3 bước qua `StorageBackend`:
  1. `POST /api/v1/documents/uploads/init` → Document kiểm tra quyền + chính sách, trả `{upload_id, signed_put_url, expires_at}`.
  2. Trình duyệt `PUT` trực tiếp lên GCS signed URL (kèm ràng buộc `Content-Length-Range` và `Content-Type`).
  3. `POST /api/v1/documents/uploads/complete` → Document kiểm tra đối tượng trên GCS (magic-byte, ZIP-bomb, số trang <= 300, checksum) trước khi tạo `PENDING`.

---

## 3. Tiêu chí nghiệm thu & Kiểm thử Demo Bảo vệ

- [x] `docker compose config --quiet` hợp lệ.
- [x] Packaging contract test kiểm tra Dockerfile non-root, chỉ copy IDP từ RAG, LF, revoke quyền trên schema `core` và Compose hardening.
- [x] `docker compose up --build -d --wait` build và khởi động đủ service, các job migration hoàn tất và `/health/ready` của `core` và `document` trả `200`.
- [x] **Smoke HTTP với tệp 10 MB của TP-01.** Qua Web proxy, upload PDF → worker xử lý tới `SUCCESS` → cấp grant → Student đọc và tải.
- [x] **Vượt trần 20 MB.** Upload tệp `20,971,521` byte trên Compose trả `413`.
- [x] **Smoke cô lập lỗi Core/Document.** Dừng `document` và kiểm tra qua Web proxy rằng API danh sách lớp trả `200`, API policy Document trả `503`.
- [x] **DB boundary.** Xác minh role `document_svc` bị từ chối đọc `core.users` trên PostgreSQL 16.

**Bằng chứng kiểm định (2026-11-21)** · Windows 11 · Python 3.12.5 · Node 24.15.0

- `docker compose config --quiet` từ repo root → exit 0.
- `services/document/tests/test_packaging_contract.py` → **3 passed** trong suite Document. Kiểm tra user non-root, chỉ copy IDP từ RAG, LF, revoke quyền trên schema `core` và Compose hardening.
- Docker Desktop 29.7.2 (linux) · PostgreSQL 16. Với `TEST_DATABASE_URL` trỏ tới `document_test` tại `127.0.0.1:55432`, `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error -q tests/` → **`99 passed in 11.59s`** trên PostgreSQL 16 từ `services/document/`, gồm Alembic `upgrade head`/`downgrade base` và packaging contract. Ruff check và format check đạt trên 52 tệp. Xem [TASK-017B](TASK-017B-document-service-foundation.md) và [TASK-017C](TASK-017C-document-idp-worker-and-review.md).
- `_finish_version` trong `services/document/app/modules/versions/processing.py` giữ advisory lock và cập nhật trạng thái trong cùng session và transaction, loại bỏ rollback của savepoint lồng nhau trên PostgreSQL 16.
- `docker compose up --build -d --wait` build thành công và đưa `db`, `migrate`, `core`, `notification-worker`, `document-db-init`, `document-migrate`, `document`, `document-worker`, `web` tới `Healthy` hoặc `Exited 0`.
- `/health/ready` trả `200` cho Core (`{"status":"ready","db":"connected"}`) và Document (`{"status":"ready","db":"connected","storage":"ready"}`).
- `psql -U document_svc -d rag_course -c "SELECT count(*) FROM core.users;"` → `ERROR: permission denied for schema core`.
- Tệp PDF `10,500,004` byte tải qua `POST http://127.0.0.1:3000/api/v1/documents` → `201 PENDING` → `document-worker` xử lý đến `SUCCESS`. `POST /api/v1/classes/{id}/documents/grants` trả `201`. Student gọi `GET .../reader` và `GET .../download` đều nhận `200`, bản tải có `10,500,004` byte.
- Tệp `20,971,521` byte (>20 MB) bị từ chối `413`.
- Sau `docker compose stop document`, `GET http://127.0.0.1:3000/api/v1/classes` trả `200` và `GET http://127.0.0.1:3000/api/v1/documents/policy` trả `503`.

Luồng Web được kiểm chứng bằng unit tests, live build và request HTTP trực tiếp tới `web:3000`. Kiểm thử thao tác thủ công trong trình duyệt còn mở. Xem [TASK-017D](TASK-017D-web-proxy-and-document-ui.md). Nghiệm thu tải TP-01 đầy đủ tiếp tục theo tiêu chí riêng.
