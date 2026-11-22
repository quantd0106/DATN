# TASK-017F: Đồng bộ Hợp đồng và Invariants Document ↔ AI

- **Mã Task:** `TASK-017F`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `IN PROGRESS` (hợp đồng tài liệu hóa đã khóa; chờ chạy kiểm thử tự động đầu-cuối consumer `document.version.published` qua AI HTTP)
- **Phạm vi:** Hợp đồng tích hợp bất đồng bộ và đồng bộ giữa **Document Service** (`services/document/`) và **AI Service** (`services/ai/` + `services/rag/`)
- **Business Rules & QAS liên quan:** `B4`, `B6`, `B7`, `H3`, `C1–C3`, `QAS-01`, `QAS-02`, `QAS-10`, `FT-03`, `FT-05`
- **Tài liệu căn cứ:**
  - [ADR-001](../docs/architect/microservices-v1/ADR-001.md)
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md)
  - [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md)
  - [AI API Contract](../docs/architect/microservices-v1/ai/API_CONTRACT.md)
  - [TASK-018](TASK-018-ai-service-rag-tutor.md)
  - [services/rag/indexing/rag_corpus.py](../services/rag/indexing/rag_corpus.py)

---

## 1. Mục tiêu

Khóa chặt ranh giới tích hợp bất đồng bộ và đồng bộ giữa **Document Service** và **AI Service**, bảo đảm toàn bộ tài liệu kiến trúc và mã nguồn thống nhất cùng một mô hình trạng thái, quyền và truy hồi.

---

## 2. Hợp đồng giao tiếp Document ↔ AI Service (`services/document/` ↔ `services/ai/`)

1. **Ranh giới trách nhiệm (`ADR-001` & `ADR-002`):**
   - **Document Service** chỉ chạy tới giai đoạn IDP: kiểm tra đầu vào → trích xuất Canonical artifact (`canonical/{document_id}/{version_id}.json` + metadata/`canonical_sha256`) → lưu bền vững → đạt `SUCCESS` (tự động hoặc qua chủ sở hữu `/accept`).
   - **AI Service** sở hữu toàn bộ bước **lập chỉ mục dẫn xuất**: chunking, tạo embedding, dựng BM25 / vector store trong schema `ai`.
2. **Luồng sự kiện bất đồng bộ (`document.outbox_events` → AI Indexer):**
   - `document.version.published`: AI Service tải Canonical artifact qua `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` (header `X-Service-Key`), kiểm tra `canonical_sha256` và `canonical_schema_version` (`idp-canonical-1` hoặc `idp-canonical-2`), chạy chunking và build index bundle theo phiên bản.
   - **Điều kiện phục vụ truy hồi (`QAS-10`, `FT-05`):** AI chỉ chuyển con trỏ chỉ mục sang phiên bản mới sau khi ghi xong bundle hoàn chỉnh (`complete: true` theo `rag_corpus.py`). Trong khi AI đang lập chỉ mục bản mới hoặc lập chỉ mục lỗi, AI tiếp tục phục vụ phiên bản đã lập chỉ mục hoàn chỉnh trước đó nếu grant vẫn còn.
   - `document.grant.changed` / `document.deleted`: AI vô hiệu hóa ngay truy hồi/cache theo grant (`/internal/v1/grant-invalidation`) hoặc xóa dẫn xuất khi tài liệu bị xóa.
3. **Kiểm tra quyền kép tại thời điểm truy hồi (`Rule B7`, `FT-03`):**
   - Mọi yêu cầu hỏi AI Tutor trong `class_id` đều phải kiểm tra song song trước khi truy hồi và trước khi trả trích dẫn:
     1. Gọi Core `GET /internal/v1/classes/{class_id}/access?user_id=...&action=use_ai_tutor`.
     2. Gọi Document `GET /internal/v1/classes/{class_id}/documents/active-grants` để lấy đúng tập `(document_id, serving_version_id)` đang được cấp quyền hiện hành.
   - Bộ lọc tìm kiếm vector/BM25 chỉ được phép quét đúng tập `(document_id, serving_version_id)` vừa nhận.

---

## 3. Các bất biến kiến trúc đã đồng bộ giữa các Service

1. **Trạng thái phiên bản (`Rule H3`):** Backend lưu 5 trạng thái `PENDING`, `PROCESSING`, `SUCCESS`, `NEEDS_REVIEW`, `FAILED`. `SUPERSEDED` (*"Bản lưu"*) là thuộc tính tính lúc đọc khi có bản `SUCCESS` mới hơn; Web live ánh xạ `SUCCESS → READY` (*"Sẵn sàng"*). Phiên bản `NEEDS_REVIEW` chỉ lên `SUCCESS` qua `POST /api/v1/documents/{document_id}/versions/{version_id}/accept` bởi chủ nguồn (`version_id` UUID trên proxy).
2. **Chống xử lý trùng & toàn vẹn artifact:** Key lưu trữ `source/{document_id}/{version_id}` và `canonical/{document_id}/{version_id}.json`; worker dùng `FOR UPDATE SKIP LOCKED` + `pg_advisory_xact_lock`, còn `source_sha256` / `canonical_sha256` kiểm tra toàn vẹn tệp khi ghi nguyên tử.
3. **Xác thực liên service (`fail closed`):** Document, AI và Learning gọi Core `POST /internal/v1/auth/introspect` với `X-Service-Key` (tuyệt đối không chia sẻ `SECRET_KEY` JWT giữa các service); chia sẻ tài liệu kiểm tra action `manage_class_documents`.
4. **Định dạng, hạn mức & triển khai:** Hỗ trợ 5 định dạng (`PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`); hạn mức P1 công bố qua `GET /api/v1/documents/policy` là `20 MB` / `300 trang`; `apps/web` dùng 3 BFF proxies (`/api/v1`, `/api/ai/v1`, `/api/learning/v1`), chạy cùng 4 backend services qua `compose.yaml` + `compose.full.yaml`.

---

## 4. Tiêu chí nghiệm thu

- [x] `services/ai/`, [AI API Contract](../docs/architect/microservices-v1/ai/API_CONTRACT.md) và [TASK-018](TASK-018-ai-service-rag-tutor.md) phản ánh đúng hợp đồng `/canonical`, `/active-grants`, `/grant-invalidation` và kiểm tra quyền kép `B7`.
- [ ] Kiểm thử đầu-cuối tự động hoàn chỉnh luồng outbox `document.version.published` → AI worker tải `/canonical` và chuyển con trỏ phục vụ trên Compose live `compose.full.yaml`.
