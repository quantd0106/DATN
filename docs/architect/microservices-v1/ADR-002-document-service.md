# ADR-002 — Thiết kế triển khai G3 cho Document Service và giải quyết 8 điểm lệch trước khi code

**Trạng thái:** `ACCEPTED — P0/P1 IMPLEMENTED & VERIFIED (P2 deferred -> TASK-017I)` · **Ngày:** `2026-11-21`
**Business Rules liên quan:** `B1–B7`, `G5`, `H3`, `H6`, `L6`, `O1`, `O2`, `F5`, `F7`, `K2`, `QAS-01`, `QAS-02`, `QAS-04`, `QAS-10`, `FT-03`, `FT-05`, `FT-07`  
**Tài liệu căn cứ:** [BUSINESS_RULES.md](../BUSINESS_RULES.md), [ADR-001](ADR-001.md), [ARCHITECTURE](ARCHITECTURE.md), [QUALITY_ATTRIBUTES](QUALITY_ATTRIBUTES.md)

---

## 1. Bối cảnh

Trước khi chuyển từ Core G1/G2 sang triển khai Document Service (G3, [TASK-017](../../../tasks/TASK-017-document-service-design-impl.md)), quá trình đối chiếu giữa [BUSINESS_RULES.md](../BUSINESS_RULES.md), mã nguồn Core (`services/core/`), giao diện Web (`apps/web/`), pipeline IDP/RAG (`services/rag/`) và hồ sơ báo cáo phát hiện **8 chỗ lệch và khoảng trống** phải chốt trước khi viết mã nguồn để tránh gãy hợp đồng liên service hoặc vi phạm quy tắc nghiệp vụ.

Ghi nhận thực tế mã nguồn tại thời điểm ra quyết định:
- `services/core/` đã triển khai Identity/Classroom/Social/Notification/Administration; chưa có `POST /internal/v1/auth/introspect` và chưa có action `manage_class_documents` trong `ClassAccessAction`.
- `apps/web/` đã có giao diện tài liệu và bộ giải mã/fixture mock (`READY`, `50 MB`), nhưng `app/api/v1/[...path]/route.ts` và `src/lib/api/proxy.ts` chỉ định tuyến tới `CORE_API_URL` và đọc body bằng `request.text()`.
- `services/rag/idp/canonical.py` hiện xuất `CANONICAL_VERSION = "idp-canonical-1"`, trong khi `services/rag/retrieval/canonical_harness.py` hỗ trợ cả `idp-canonical-1` và `idp-canonical-2` (`idp.publish` chưa tồn tại trong working tree hiện tại).

---

## 2. Tám quyết định kiến trúc (P1 Design Baseline)

### Quyết định 1 — Chuẩn hóa trạng thái phiên bản và tính dẫn xuất `SUPERSEDED`
- **Vấn đề:** Web hiện dùng `READY | PROCESSING | PENDING | FAILED` (`apps/web/src/lib/domain.ts`); báo cáo và [TASK-017](../../../tasks/TASK-017-document-service-design-impl.md) dùng `SUCCESS`; riêng báo cáo còn liệt kê `NEEDS_REVIEW` và `SUPERSEDED`.
- **Quyết định:**
  1. Phía máy chủ Document (`document_versions.processing_status`) chỉ lưu đúng **5 giá trị**: `PENDING`, `PROCESSING`, `SUCCESS`, `NEEDS_REVIEW`, `FAILED`.
  2. **Không lưu trạng thái `SUPERSEDED` trong cơ sở dữ liệu.** Lý do: theo Rule **H3**, nếu phiên bản mới xử lý lỗi hoặc chưa sẵn sàng, hệ thống tiếp tục phục vụ phiên bản `SUCCESS` gần nhất. Nếu ghi đè phiên bản cũ thành `SUPERSEDED` trong DB trước hoặc trong khi phiên bản mới chưa ổn định sẽ phá vỡ tính bất biến phục vụ và làm phức tạp rollback. Thay vào đó, `serving_version` được xác định động là phiên bản có `processing_status = 'SUCCESS'` và `version_number` lớn nhất; mọi bản `SUCCESS` có `version_number < serving_version.version_number` được trả thêm thuộc tính dẫn xuất `is_superseded = true` (`is_serving = false`).
  3. Trên Web live adapter/decoder, `processing_status = "SUCCESS"` được ánh xạ thành `'READY'` và `format = "MARKDOWN"` được ánh xạ thành `'MD'`, giúp giữ nguyên gần như toàn bộ thành phần UI hiện có (`SourceDocumentSummary`, `SourceVersionHistory`, `TeacherLibraryView`).
- **Hệ quả:** An toàn tuyệt đối với Rule H3; mở rộng thêm một trạng thái hiển thị `NEEDS_REVIEW` ở Web mà không phải đổi enum phục vụ `READY`.

### Quyết định 2 — Lối thoát cho `NEEDS_REVIEW`: Chủ sở hữu chấp nhận thủ công
- **Vấn đề:** Pipeline IDP hiện tại (`services/rag/idp/`) gắn cờ rà soát (`review_assisted_pass = true`, `auto_publish_pass = false`) cho các trang OCR nhanh (`FAST_OCR_REVIEW`), engine fallback hoặc bảng/công thức trong DOCX (`TABLE_STRUCTURE_REVIEW`, `FORMULA_*`). Nhiều tài liệu học liệu thật sẽ dừng ở `NEEDS_REVIEW` và không bao giờ tự lên `SUCCESS`, gây tắc luồng demo và vận hành thực tế.
- **Quyết định:**
  1. Bổ sung thao tác **`POST /api/v1/documents/{document_id}/versions/{version_id}/accept`** tại Document Service.
  2. Chỉ **chủ sở hữu tài liệu nguồn** (`document.owner_user_id == actor.id`, đúng Rule **B2** và **O1**; giáo viên phối hợp trong lớp không có quyền duyệt phiên bản nguồn của người khác) được phép chấp nhận phiên bản đang ở trạng thái `NEEDS_REVIEW` (với điều kiện artifact Canonical đã được lưu hợp lệ).
  3. Thao tác chuyển `processing_status` từ `NEEDS_REVIEW → SUCCESS`, ghi `acceptance_mode = 'OWNER_ACCEPTED'`, `accepted_by_user_id`, `accepted_at`, ghi bản ghi kiểm toán `DOCUMENT_VERSION_ACCEPTED` và chèn sự kiện transactional outbox `document.version.published` trong cùng một transaction. Các trạng thái khác (`PENDING`, `PROCESSING`, `SUCCESS`, `FAILED`) trả `409 CONFLICT`.
  4. Nếu phiên bản trước đó đã là `SUCCESS`, phiên bản đó vẫn tiếp tục phục vụ lớp trong suốt thời gian phiên bản mới nằm ở `NEEDS_REVIEW` (tuân thủ H3) cho tới khi chủ sở hữu chấp nhận.
  5. Web UI bổ sung nhãn `NEEDS_REVIEW` ("Cần rà soát") và nút thao tác nhỏ **"Chấp nhận"** ngay trong bảng lịch sử phiên bản (`SourceVersionHistory`).
- **Hệ quả:** Học liệu thực tế có OCR nhanh hoặc công thức DOCX có lối ra tường minh do chính giáo viên sở hữu chịu trách nhiệm xác nhận.

### Quyết định 3 — Bổ sung `manage_class_documents` vào `ClassAccessAction` của Core
- **Vấn đề:** Core Classroom hiện có 8 action (`read_class_content`, `participate_current_class`, `moderate_class_content`, `manage_class_calendar`, `read_own_history`, `read_document_source`, `use_ai_tutor`, `grade_submitted_work`) nhưng chưa có action riêng cho việc cấp quyền hoặc thu hồi chia sẻ tài liệu vào lớp (`POST`/`DELETE` grant).
- **Quyết định:**
  1. Bổ sung giá trị **`manage_class_documents`** vào `ClassAccessAction` trong Core (`services/core/app/modules/classroom/schemas.py` và `public.py`).
  2. Điều kiện cho phép: `active_class and current_teacher` — tức tài khoản `ACTIVE`, cùng `school_id`, lớp ở trạng thái `ACTIVE`, vai trò kỹ thuật có `LECTURER` và đang có `TeachingAssignment` (`PRIMARY` hoặc `ASSISTING`) còn hiệu lực (khớp Rule **B2**, **O1**, **F5**).
  3. **Không mượn `manage_class_calendar`** để giữ ranh giới ngữ nghĩa quyền tách bạch giữa Social Calendar và Document Grants (nếu sau này chính sách phân quyền lịch và học liệu khác nhau sẽ không bị gãy hợp đồng).
  4. Document Service khi nhận denied từ Core áp dụng đúng quy tắc che lỗi của Core (`class_status == ARCHIVED` → `409 CONFLICT`; không có `class_status` → `404 NOT_FOUND` để che lớp khác trường/không tồn tại; `class_status == ACTIVE` → `403 FORBIDDEN`).
- **Hệ quả:** Chỉ tác động nhỏ, additive vào Core Classroom (`TASK-017A`), giữ nguyên toàn bộ 8 action hiện hành và tương thích ngược OpenAPI công khai.

### Quyết định 4 — Xác thực người dùng liên service qua `POST /internal/v1/auth/introspect`
- **Vấn đề:** Access JWT của Core được ký HS256 bằng `SECRET_KEY`. Nếu chia sẻ `SECRET_KEY` sang Document Service (hoặc các service khác), mọi service giữ secret đều có khả năng tự ký giả mạo JWT của bất kỳ user nào (vi phạm nguyên tắc tối thiểu đặc quyền) và không kiểm tra được thu hồi phiên tức thì qua `session_version`, khóa tài khoản (`LOCKED`) hoặc đổi vai trò trong DB Core.
- **Quyết định:**
  1. **Không chia sẻ `SECRET_KEY` ngoài Core.** Core cung cấp endpoint nội bộ **`POST /internal/v1/auth/introspect`** xác thực qua header `X-Service-Key` (`INTERNAL_SERVICE_SECRET`, scope `core:introspect_auth`).
  2. Request body JSON: `{"token": "<bearer_jwt>"}` (tuyệt đối không truyền token qua query string, URL hoặc ghi log).
  3. Core kiểm tra chữ ký JWT, `exp`/`iat`, và truy vấn bảng `users` + `role_assignments` hiện hành để xác minh `user.status == "ACTIVE"` và `user.session_version == claims.session_version`.
  4. Response `200 OK` (`Cache-Control: no-store`):
     - Hợp lệ: `{"active": true, "user": {"id": "...", "status": "ACTIVE", "roles": [...], "school_id": "...", "display_name": "..."}, "exp": 1700000000}`.
     - Không hợp lệ / hết hạn / bị thu hồi / khóa: `{"active": false}` (Document ánh xạ thành `401 UNAUTHENTICATED`).
  5. **Không cache kết quả introspect ở P1** để bảo đảm thao tác `logout-all`, `LOCKED`, đổi role hoặc mapping school có hiệu lực ngay ở request kế tiếp.
  6. **Fail closed:** nếu Core mất kết nối, timeout hoặc trả `5xx` hoặc thiếu `INTERNAL_SERVICE_SECRET`, Document Service từ chối yêu cầu với `503 SERVICE_UNAVAILABLE` (không tự giải mã JWT cục bộ).
- **Hệ quả:** Tăng 1 lượt gọi nội bộ nhẹ trên mỗi request sang Document (chấp nhận được trên mạng Docker Compose nội bộ và đúng nguyên tắc fail-closed của ADR-001). Nếu về sau cần tách key bất đối xứng (RS256/EdDSA + JWKS) hoặc Redis revocation pub/sub, có thể nâng cấp ở P2 mà không đổi hợp đồng quyền của Document.

### Quyết định 5 — Mở rộng Next.js Web Proxy hỗ trợ đa upstream và thân nhị phân
- **Vấn đề:** Proxy hiện tại (`apps/web/app/api/v1/[...path]/route.ts`, `apps/web/src/lib/api/proxy.ts`, `apps/web/src/lib/api/operations.ts`) chỉ đọc `CORE_API_URL`, kiểm tra allowlist chỉ gồm Core operations và đọc body bằng `await request.text()`. Gọi `request.text()` làm hỏng byte nhị phân (`multipart/form-data`) khi upload PDF/DOCX/PPTX.
- **Quyết định:**
  1. Mở rộng bảng `operations` trong `operations.ts` để mỗi operation khai báo thêm đích `upstream: 'core' | 'document'` (mặc định rõ ràng, không suy đoán theo tiền tố lỏng lẻo vì Document có cả `/documents/*`, `/classes/:classId/documents/*`, `/classes/:classId/notes*`, `/classes/:classId/bookmarks*`, `/notes/*`, `/users/me/notes`, `/users/me/bookmarks`). Proxy vẫn giữ kiểm tra UUID cho mọi path parameter và allowlist query parameter như hiện tại.
  2. Router `/api/v1/[...path]/route.ts` truyền bộ resolver upstream `{ core: () => process.env.CORE_API_URL, document: () => process.env.DOCUMENT_API_URL }`.
  3. Đọc thân yêu cầu bằng **`await request.arrayBuffer()`** thay vì `request.text()`, chuyển tiếp nguyên vẹn `Content-Type` (bao gồm `multipart/form-data; boundary=...`) khi chuyển tiếp tới upstream; khi trả về cho trình duyệt giữ thêm các header tải tệp an toàn (`Content-Disposition`, `Content-Length`) khi response là binary stream.
  4. Cô lập cấu hình giữa các service: nếu thiếu `DOCUMENT_API_URL`, chỉ các route có `upstream: 'document'` trả `503 CONFIGURATION_ERROR`; các route Core tiếp tục hoạt động bình thường. Không bao giờ forward `X-Service-Key` từ trình duyệt.
  5. Nâng timeout proxy/HTTP client cho các thao tác upload/download tệp (ví dụ `60_000 ms` cho upload/download, giữ `15_000 ms` cho JSON thông thường).
- **Hệ quả:** Trình duyệt giữ nguyên kiến trúc same-origin `/api/v1/*` với CSRF/Origin check tập trung, không lộ `DOCUMENT_API_URL` hoặc `X-Service-Key` ra client bundle.

### Quyết định 6 — Môi trường Demo P1 (Docker Compose) vs Cloud Upload (P2)
- **Vấn đề:** Giới hạn kích thước request body của Vercel Functions (khoảng 4,5 MB; cần kiểm chứng lại từ tài liệu Vercel hiện hành khi triển khai cloud) sẽ chặn tệp mẫu 10 MB của cấu hình kiểm thử `TP-01` (`QUALITY_ATTRIBUTES.md`) cũng như hạn mức 20 MB nếu chạy upload qua Next.js API route trên Vercel.
- **Quyết định:**
  1. **Giai đoạn P1 (Docker Compose & nghiệm thu G3):** Chạy toàn bộ Web (Next.js standalone), Core, Core Worker, Document API, Document Worker và PostgreSQL 16 trên **Docker Compose** (`compose.yaml`, và `compose.full.yaml` khi chạy đủ 4 backend services), lưu tệp gốc và Canonical artifact trên Docker volume cục bộ thông qua abstraction `StorageBackend`. Trong môi trường Docker Compose, Next.js Node server không chịu trần 4,5 MB của Vercel Serverless Functions nên upload trực tiếp qua `/api/v1/documents` tới 20 MB hoạt động thông suốt.
  2. **Giai đoạn P2 (triển khai cloud mở rộng):** Tách luồng upload sang **direct-to-GCS signed URL** (`POST /documents/uploads/init` → client `PUT` trực tiếp lên GCS signed URL → `POST /documents/uploads/complete`) để bỏ qua trần kích thước body của tầng Web/Serverless proxy.
- **Hệ quả:** P1 đơn giản, khép kín, không phụ thuộc cloud storage bên ngoài khi chạy cục bộ/Compose; `StorageBackend` giữ đường nâng cấp P2 sạch sẽ.

### Quyết định 7 — Tách rạch ròi ranh giới Document (IDP) và AI (Lập chỉ mục)
- **Vấn đề:** Cần bảo đảm bước "lập chỉ mục" tách biệt rõ sau trạng thái `SUCCESS` của phiên bản tài liệu, tuân thủ [ADR-001](ADR-001.md) và [ARCHITECTURE.md](ARCHITECTURE.md#2-ownership) (Document sở hữu tài liệu/phiên bản/grant; AI sở hữu chỉ mục RAG dẫn xuất).
- **Quyết định:**
  1. Trong Document Service, **`SUCCESS`** có nghĩa là: tệp nguồn đã qua kiểm tra hợp lệ, pipeline IDP đã trích xuất xong **Canonical artifact** (`canonical.json` + assets + outline/page projection cho Reader) và lưu bền vững cùng checksum `canonical_sha256`.
  2. Thực tế mã nguồn: `services/rag/idp/canonical.py` hiện xuất `schema_version = "idp-canonical-1"`, trong khi `services/rag/retrieval/canonical_harness.py` hỗ trợ cả `idp-canonical-1` và `idp-canonical-2`. Document Service lưu tường minh `canonical_schema_version` vào `document_versions` và phát trong payload sự kiện mà không giả định tính năng `idp.publish` chưa có trong workspace.
  3. Khi một phiên bản chuyển sang `SUCCESS` (tự động từ worker hoặc do chủ sở hữu chấp nhận từ `NEEDS_REVIEW`), trong **cùng transaction**, Document ghi sự kiện transactional outbox **`document.version.published`** (kèm `document.grant.changed` khi cấp/thu hồi chia sẻ, `document.deleted` khi xóa).
  4. **AI Service** tiêu thụ sự kiện bất đồng bộ để tự chia đoạn (chunking), nhúng vector/BM25 và **chỉ phục vụ truy hồi phiên bản mới khi bundle chỉ mục đã hoàn chỉnh** (`complete: true`, đúng `QAS-10` và `FT-05`).
  5. **Đồng bộ tài liệu & hợp đồng tích hợp:** Toàn bộ sơ đồ/đặc tả đặt bước lập chỉ mục **sau** sự kiện `document.version.published` thuộc trách nhiệm AI Service (chi tiết đối chiếu trong [TASK-017F](../../../tasks/TASK-017F-doc-ai-event-and-report-sync.md) và [AI API Contract](ai/API_CONTRACT.md)).
- **Hệ quả:** Cô lập lỗi đúng theo `QAS-01` và `QAS-02`: nếu AI Service hoặc vector index gặp sự cố, giáo viên vẫn tải lên, xem và chia sẻ tài liệu cho học sinh đọc bình thường.

### Quyết định 8 — Chốt hạn mức Upload P1 và Endpoint Chính sách (`GET /api/v1/documents/policy`)
- **Vấn đề:** Mock adapter trên Web (`apps/web/dev/mocks/fixtures.ts`) đang đặt trần `50 MB` (`52428800`), trong khi [04_FRS_Document.md](../athena-design/04_FRS_Document.md) nêu rõ không lấy mức mẫu `10 MB` của `TP-01` làm giới hạn upload tối đa nhưng chưa chốt con số cụ thể.
- **Quyết định:**
  1. Chốt giới hạn upload mặc định giai đoạn P1 (cấu hình được qua biến môi trường của Document Service):
     - **`max_size_bytes = 20_971_520` (20 MB)** — đủ bao phủ tệp mẫu trung bình 10 MB của `TP-01` và tài liệu giảng dạy THCS/THPT thông thường, đồng thời an toàn cho bộ nhớ container Docker Compose khi chạy IDP/OCR cục bộ.
     - **`max_pages = 300`** — áp dụng cho số trang PDF và số slide PPTX (ngăn tài liệu hàng nghìn trang làm nghẽn worker OCR).
     - **5 định dạng được phép (Rule B3):** `PDF` (`.pdf`), `PPTX` (`.pptx`), `DOCX` (`.docx`), `TXT` (`.txt`), `MARKDOWN` (`.md`, `.markdown`).
  2. Document Service cung cấp endpoint **`GET /api/v1/documents/policy`** trả về chính sách hiện hành (`max_size_bytes`, `max_pages`, `allowed_formats`, `allowed_extensions`, `allowed_mime_types`) để Web hiển thị gợi ý và kiểm tra sớm thống nhất với backend.
  3. Đồng bộ mock fixture của Web (`apps/web/dev/mocks/fixtures.ts`) về `20 MB` (`20_971_520`) và `300` trang để chế độ preview và live nhất quán.
- **Hệ quả:** Chấm dứt sự không thống nhất giữa mock `50 MB`, mẫu `10 MB` và FRS; mọi kiểm tra kích thước/định dạng/số trang được thực thi nhất quán ở cả Web và Document Service.

---

## 3. Tài liệu và Task dẫn xuất

- Thiết kế chi tiết Document Service: [document/DESIGN.md](document/DESIGN.md)
- Hợp đồng API Document Service & bảng Web Proxy allowlist: [document/API_CONTRACT.md](document/API_CONTRACT.md)
- Cấu trúc mã nguồn `services/document/`: [document/FOLDER_STRUCTURE.md](document/FOLDER_STRUCTURE.md)
- Cập nhật Core contract & design: [core/API_CONTRACT.md](core/API_CONTRACT.md), [core/DESIGN.md](core/DESIGN.md)
- Cập nhật FRS & môi trường: [04_FRS_Document.md](../athena-design/04_FRS_Document.md), [22_ENVIRONMENTS_DEPLOYMENT.md](../athena-design/22_ENVIRONMENTS_DEPLOYMENT.md)
- Kế hoạch triển khai theo từng công việc: [TASK-017](../../../tasks/TASK-017-document-service-design-impl.md) (`TASK-017A` → `TASK-017I`, gồm P0/P1 CI/E2E [TASK-017G](../../../tasks/TASK-017G-document-ci-and-compose-smoke.md), [TASK-017H](../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md) và P2 cloud/governance [TASK-017I](../../../tasks/TASK-017I-document-p2-cloud-and-governance.md)) và [TASK-018](../../../tasks/TASK-018-ai-service-rag-tutor.md).
