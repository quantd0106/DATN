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

## 3. Checklist bắt buộc sửa trong `Bao_cao_DATN.docx` & Slide bảo vệ

> **Kết quả đối chiếu thực tế (2026-11-22):** Đã đọc toàn văn và các hình vẽ trong tệp báo cáo đồ án nằm ngoài Git tại `C:\Users\Trinh Tuyen\OneDrive - HUCE\Desktop\Workspace\DATN_2\Bao_cao_DATN.docx` (Bảng 5.3 ghi hạn nộp `16/11–22/11/2026`, bảo vệ dự kiến `28–29/11/2026`). Mã nguồn `services/document/` không cần sửa lại theo báo cáo; ngược lại cần cập nhật `Bao_cao_DATN.docx` và slide theo 2 nhóm mục bên dưới trước khi nộp.

### Nhóm A — 5 sai lệch kỹ thuật cần sửa trong `Bao_cao_DATN.docx` (và slide tương ứng)

- [ ] **A1. Sửa `Hình 2.16` & `Mục 2.7.2` (trạng thái phiên bản & ranh giới IDP/Index):**
  - Đổi nhãn nhánh `PROCESSING -> SUCCESS` từ *"đạt, lập chỉ mục"* thành **`đạt, lưu Canonical`** (Document Service chỉ chịu trách nhiệm trích xuất + lưu Canonical + phát sự kiện `document.version.published` sang AI Service lập chỉ mục).
  - Sửa mũi tên xuống `SUPERSEDED`: đổi nhãn *"có bản mới"* thành **`có bản SUCCESS mới hơn`** và chú thích rõ `Backend` lưu 5 trạng thái (`PENDING`, `PROCESSING`, `SUCCESS`, `NEEDS_REVIEW`, `FAILED`); `SUPERSEDED` ("Bản lưu") là **trạng thái hiển thị dẫn xuất** tính lúc đọc theo Rule `H3` (không lưu cột DB), còn Web live decoder map `SUCCESS → READY` ("Sẵn sàng").
  - Giữ nhánh `NEEDS_REVIEW` → **"Giảng viên sở hữu chấp nhận (`/accept`)"** → `SUCCESS` kèm chú thích nút thao tác trên UI (`version_id` UUID).
- [ ] **A2. Sửa `Hình 3.6` (sơ đồ tuần tự nạp, xử lý và lập chỉ mục tài liệu):**
  - Bước 3 trả về **`201 Created`** (hiện hình đang ghi `202 Accepted`).
  - Bỏ đường gọi thẳng từ `Worker IDP` sang `Worker lập chỉ mục` và bỏ mũi tên `indexed` quay lại `Document Service` trước bước 14; thay bằng `Worker IDP` lưu Canonical + commit `SUCCESS` + ghi outbox **`document.version.published`**, sau đó `Worker lập chỉ mục` (AI Service) bất đồng bộ tải Canonical qua `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` (`X-Service-Key`).
- [ ] **A3. Sửa `Bảng 3.10` (dòng `Xử lý lại tài liệu`):**
  - Hiện ghi *"Nhận diện theo SHA-256 nội dung ... Tải lại cùng tệp không xử lý và lập chỉ mục hai lần"*. Sửa cho khớp mã nguồn: khóa lưu tệp là `source/{document_id}/{version_id}` và `canonical/{document_id}/{version_id}.json`, `source_sha256` / `canonical_sha256` dùng để **kiểm chứng toàn vẹn tệp (`StorageIntegrityError`) và ghi nguyên tử (`os.replace`)**; re-upload vẫn tạo phiên bản mới qua `FOR UPDATE SKIP LOCKED` + `pg_advisory_xact_lock` (chống xử lý trùng cùng phiên bản), còn AI lập chỉ mục dùng `canonical_sha256` + bundle bất biến theo phiên bản.
- [ ] **A4. Bổ sung vào `Bảng 3.2`, `Mục 3.3.2` & `Bảng 2.17`:**
  - Thêm `POST /internal/v1/auth/introspect` (`X-Service-Key`, kiểm tra chữ ký + `session_version` + `ACTIVE`, fail closed) và action **`manage_class_documents`** (`ACTIVE` class + giáo viên hiện hành) — tuyệt đối không ghi chia sẻ `SECRET_KEY` HS256 sang Document/AI.
  - Bổ sung bảng tóm tắt API Document Service (**20 public + 2 internal endpoints**), hạn mức P1 **`20 MB` / `300 trang`** qua `GET /api/v1/documents/policy` (phân biệt rõ với tệp mẫu `10 MB` trong `TP-01`), **12 hàm** Web port (`DataAdapter['document']`) và Proxy allowlist đọc nội dung nhị phân qua `arrayBuffer()`.
- [ ] **A5. Cập nhật `Hình 2.9`, `Mục 3.4.1` & chú thích `Canonical v2`:**
  - `Hình 2.9` & `Mục 3.4.1` bổ sung đủ **5 định dạng** `PDF, PPTX, DOCX, TXT, Markdown` (`F13` / Rule `B3`).
  - Ghi chú rõ mã nguồn `services/rag/idp/canonical.py` hiện xuất `idp-canonical-1` (worker `idp_runner.py` & AI harness tương thích cả `idp-canonical-1` và `idp-canonical-2`).

### Nhóm B — 11 vị trí cần cập nhật từ "Thiết kế / Đề xuất / Chưa có hợp đồng API" sang hiện thực `P0/P1 IMPLEMENTED — VERIFIED`

- [ ] **B1. `Tóm tắt`:** Bổ sung Document Service đã triển khai kèm `Core` và `Web` trên `Docker Compose` + kết quả kiểm thử `99` ca PG16 và Web `277/277`.
- [ ] **B2. `Bảng 2.1` (`UC10`, `UC11`, `UC12`):** Chuyển `UC11` (Chia sẻ/thu hồi tài liệu) và `UC12` (Đọc, ghi chú, đánh dấu) từ *"Thiết kế"* sang **`Đã triển khai`**; `UC10` ghi rõ chạy qua `Document Service` + `Worker IDP`.
- [ ] **B3. `Bảng 2.4` (`UC11`):** Bỏ chữ *"(UC11, thiết kế)"*; làm rõ bước 3 hiện ghi sự kiện outbox `document.grant.changed` (còn gửi thông báo in-app qua `Core` chuyển sang **P2** tại [TASK-017I](TASK-017I-document-p2-cloud-and-governance.md) vì `Core` chưa mở internal notification intake API).
- [ ] **B4. `Hình 2.8` & `Bảng 2.7`:** Tô xanh lá `Document Service` (cùng `LocalVolumeStorage` / schema `document`) và bổ sung luồng `introspect` sang Core; giữ nguyên trạng thái thử nghiệm/thiết kế cho phần HTTP `AI Service`, và ngoài phạm vi cho `Learning` / `Learner Intelligence`.
- [ ] **B5. `Hình 2.12` & `Bảng 2.8`:** Cập nhật nhóm `Tài liệu` sang **`Đã triển khai`** với đủ **7 bảng** trong schema `document`: `documents`, `document_versions` (chứa luôn `attempt_count` & `available_at` cho lease/`SKIP LOCKED` thay cho bảng `processing_jobs` tách riêng), `class_document_grants` (partial unique giữ lịch sử), `source_bound_notes`, `document_bookmarks`, `outbox_events`, `audit_events`. Giải thích lớp `StorageBackend` (= khái niệm `BlobStore` với `LocalVolumeStorage` ở P1 và `GCSStorageBackend` ở P2).
- [ ] **B6. `Hình 2.14`, `Mục 4.1`, `Bảng B.1` & `Phụ lục B`:** Cập nhật mô hình `Docker Compose` lên **8 đơn vị** (`db`, `migrate`, `document-migrate`, `core`, `document`, `notification-worker`, `document-worker`, `web` với `CORE_API_URL` + `DOCUMENT_API_URL` và volume `document_data`); thêm thư mục `services/document/` vào `Bảng B.1`. Ghi rõ demo bảo vệ P1 chạy trên **Docker Compose** (tránh trần body ~4,5 MB của Vercel Functions); P2 cloud dùng **GCS signed URL upload**.
- [ ] **B7. `Bảng 4.3`, `Mục 4.4.1` & `Mục 4.6`:** Bổ sung suite Document PG16 **`99 đạt`** (`11.59s`, Ruff `52` files `0` lỗi), cập nhật Web từ `226/226` lên **`277/277 đạt`** (`32/32` trang `live` build), cập nhật Core PG16 thêm **`25` ca** `classroom/introspect`, và bổ sung smoke `Compose` PDF `10,500,004` byte + test `413` / cô lập `503`. *(Ghi trung thực phần `manual browser E2E` / full 5-format Compose E2E theo tiến độ thực tế của [TASK-017H](TASK-017H-document-compose-e2e-and-demo-runbook.md)).*
- [ ] **B8. `Mục 4.9` & `Kết luận (Mục 1–3)`:** Bỏ nhận định *"Document Service ... chưa có hợp đồng API được duyệt"* và thay phần *Hướng phát triển* từ *"Hoàn thiện Document Service"* sang các hạng mục **P2** ([TASK-017I](TASK-017I-document-p2-cloud-and-governance.md): GCS signed URL, malware scan, quota, retention `OD-02`/`OD-03`, Cloud Run) + lớp HTTP của `AI Service`.
- [ ] **B9. `Bảng 5.2`, `Bảng 5.3` & `Phụ lục A`:** Bổ sung `services/document/` (và `ADR-002`, `TASK-017A..E`) vào bảng khối lượng bàn giao và mốc giai đoạn 5 (`16/11–22/11/2026`).
- [ ] **B10. `Bảng C.1` (Ma trận truy vết):** Thay dấu `"—"` ở các dòng `F13`, `F14`, `F16` bằng kiểm thử thực tế `services/document/tests/` (`DOC-T01..DOC-T13`) & cập nhật đường dẫn thiết kế (`Mục 3.3`, `Hình 2.16`, `DOC-T01..T13`).
- [ ] **B11. `Mục 5.7` (Kịch bản demo 5 phút, 7 bước):** Giữ nhịp 7 bước bảo vệ khi quay video / chạy demo trực tiếp (tích hợp `DEMO_RUNBOOK.md` ở [TASK-017H](TASK-017H-document-compose-e2e-and-demo-runbook.md)).

---

## 4. Tiêu chí nghiệm thu

- [x] [TASK-018](TASK-018-ai-service-rag-tutor.md) đã tham chiếu đầy đủ `ADR-002`, `document.version.published`, `active-grants` và `/internal/v1/documents/{document_id}/versions/{version_id}/canonical`.
- [ ] Toàn bộ **Nhóm A (A1–A5)** và **Nhóm B (B1–B11)** trong `Bao_cao_DATN.docx` + slide bảo vệ khớp 100% với [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md), [CODEX_ORCHESTRATION.md](../docs/architect/microservices-v1/document/CODEX_ORCHESTRATION.md) và mã nguồn `services/document/`.
