# TASK-017F: Đồng bộ Hợp đồng Document↔AI và Cập nhật Báo cáo / Slide (Hình 2.16)

- **Mã Task:** `TASK-017F`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `READY FOR EXECUTION` (tài liệu hóa & đối chiếu báo cáo/slide bảo vệ)
- **Phạm vi:** Hợp đồng bàn giao Document → AI ([TASK-018](TASK-018-ai-service-rag-tutor.md)) + nội dung Báo cáo đồ án / Slide bảo vệ
- **Giải quyết vấn đề:** `#1` (nhất quán tên trạng thái giữa Web/Backend/Báo cáo), `#2` (lối thoát `NEEDS_REVIEW`), `#7` (phân định IDP và lập chỉ mục — Hình 2.16), `#8` (hạn mức 20 MB / 300 trang)
- **Business Rules & QAS liên quan:** `B4`, `B6`, `B7`, `H3`, `C1–C3`, `QAS-01`, `QAS-02`, `QAS-10`, `FT-03`, `FT-05`
- **Tài liệu căn cứ:**
  - [ADR-001](../docs/architect/microservices-v1/ADR-001.md)
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 1, 2, 7, 8)
  - [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md)
  - [TASK-018](TASK-018-ai-service-rag-tutor.md)
  - [services/rag/indexing/rag_corpus.py](../services/rag/indexing/rag_corpus.py)

---

## 1. Mục tiêu

Chốt giao diện tích hợp bất đồng bộ và đồng bộ giữa **Document Service** và **AI Service**, đồng thời lập danh sách chỉnh sửa cụ thể trong **Báo cáo đồ án** và **Slide bảo vệ** (trọng tâm là **Hình 2.16**) để không còn mâu thuẫn giữa tài liệu thuyết minh, ADR và mã nguồn triển khai.

---

## 2. Hợp đồng giao tiếp Document ↔ AI Service (bàn giao cho `TASK-018`)

1. **Ranh giới trách nhiệm (tuân thủ ADR-001 & ADR-002):**
   - **Document Service** chỉ chạy tới giai đoạn IDP: kiểm tra đầu vào → trích xuất Canonical artifact (`canonical.json` + metadata/checksum) → lưu bền vững → đạt `SUCCESS` (tự động hoặc qua chủ sở hữu `/accept`).
   - **AI Service** sở hữu toàn bộ bước **lập chỉ mục dẫn xuất**: chunking, tạo embedding, dựng BM25 / vector store.
2. **Luồng sự kiện bất đồng bộ (`document.outbox_events` → AI Indexer):**
   - `document.version.published`: AI Service tải Canonical artifact qua `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` (header `X-Service-Key`), kiểm tra `canonical_sha256` và `canonical_schema_version` (`idp-canonical-1` hiện tại hoặc `idp-canonical-2` khi bật Unified publisher), chạy chunking và build index bundle theo phiên bản.
   - **Điều kiện phục vụ truy hồi (`QAS-10`, `FT-05`):** AI chỉ chuyển con trỏ chỉ mục sang phiên bản mới sau khi ghi xong bundle hoàn chỉnh (`complete: true` theo mẫu `rag_corpus.py`). Trong khi AI đang lập chỉ mục phiên bản mới hoặc lập chỉ mục lỗi, AI tiếp tục phục vụ phiên bản đã lập chỉ mục hoàn chỉnh trước đó (nếu grant vẫn còn).
   - `document.grant.changed` / `document.deleted`: AI vô hiệu hóa ngay cache/truy hồi theo grant hoặc xóa dẫn xuất khi tài liệu bị xóa.
3. **Kiểm tra quyền kép tại thời điểm truy hồi (Rule B7, `FT-03`):**
   - Mọi yêu cầu hỏi AI Tutor trong `class_id` đều phải kiểm tra song song trước khi truy hồi và trước khi trả trích dẫn:
     1. Gọi Core `GET /internal/v1/classes/{class_id}/access?user_id=...&action=use_ai_tutor`.
     2. Gọi Document `GET /internal/v1/classes/{class_id}/documents/active-grants` để lấy đúng tập `(document_id, serving_version_id)` đang được cấp quyền hiện hành.
   - Bộ lọc tìm kiếm vector/BM25 chỉ được phép quét đúng tập `(document_id, serving_version_id)` vừa nhận.

---

## 3. Checklist bắt buộc sửa trong Báo cáo đồ án & Slide bảo vệ

> **Lưu ý:** Các tệp slide/báo cáo `.docx`/`.pptx` nằm ngoài mã nguồn git (hoặc do nhóm tác giả chỉnh trực tiếp); kiểm tra từng mục dưới đây trước khi chốt bản nộp:

- [ ] **Sửa Hình 2.16 (Sơ đồ luồng xử lý tài liệu & vòng đời phiên bản):**
  - Đặt mốc **`SUCCESS`** tại Document Service ngay sau bước **Trích xuất Canonical & Lưu bền vững** (trước bước lập chỉ mục).
  - Vẽ mũi tên sự kiện bất đồng bộ **`document.version.published`** đi từ Document Service sang **AI Service**, nơi thực hiện **Chia đoạn (Chunking) → Tạo Embedding / BM25 → Đánh dấu bundle hoàn chỉnh (`complete: true`) mới phục vụ RAG**.
  - Bổ sung nhánh **`NEEDS_REVIEW`** từ bước kiểm định chất lượng IDP (trường hợp fast OCR hoặc công thức/bảng DOCX) nối qua thao tác **"Giáo viên sở hữu chấp nhận"** → **`SUCCESS`**.
- [ ] **Chuẩn hóa bảng tên trạng thái phiên bản trong Báo cáo & Slide:**
  - Nêu rõ 5 trạng thái lưu trữ tại Backend: `PENDING`, `PROCESSING`, `SUCCESS`, `NEEDS_REVIEW`, `FAILED`.
  - Giải thích tường minh: **`SUPERSEDED`** ("Bản lưu") là **trạng thái hiển thị dẫn xuất** tính lúc đọc cho các bản `SUCCESS` cũ hơn `serving_version`, **không** lưu cột DB để bảo toàn Rule **H3** khi phiên bản mới thất bại.
  - Ghi chú quy ước ánh xạ ở Web UI: Backend `SUCCESS` ↔ Web hiển thị **`READY` ("Sẵn sàng")**.
- [ ] **Đồng bộ thông số kỹ thuật trong Báo cáo & Slide:**
  - Giới hạn upload P1: **`20 MB`**, tối đa **`300 trang`** (PDF/PPTX), công bố qua `GET /api/v1/documents/policy` (phân biệt rõ với tệp mẫu trung bình `10 MB` dùng để đo tải ở kịch bản `TP-01`).
  - Xác thực liên service: Core `POST /internal/v1/auth/introspect` (`X-Service-Key`, kiểm tra `session_version`, fail closed), **không** dùng mô tả chia sẻ `SECRET_KEY` HS256 sang các service khác.
  - Phân quyền chia sẻ/thu hồi lớp: Core action **`manage_class_documents`** (`ACTIVE` class + giáo viên hiện hành).
  - Môi trường triển khai: P1 demo bảo vệ chạy bằng **Docker Compose** (tránh trần kích thước body của Vercel Functions); P2 cloud dùng **direct-to-GCS signed URL**.

---

## 4. Tiêu chí nghiệm thu

- [ ] [TASK-018](TASK-018-ai-service-rag-tutor.md) đã tham chiếu đầy đủ `ADR-002`, `document.version.published`, `active-grants` và `canonical` endpoint.
- [ ] Hình 2.16, bảng trạng thái và mô tả kiến trúc trong báo cáo + slide khớp 100% với [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md).
