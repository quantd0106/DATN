# TASK-017: Document Service Specification & Implementation (G3)

- **Mã Task:** `TASK-017`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái P1:** `IMPLEMENTED — VERIFIED` (code và runtime)
- **Business Rules liên quan:** `B1`, `B2`, `B3`, `B4`, `B5`, `B6`, `B7`, `H3`, `H6`, `L6`, `O1`, `O2`, `G5`
- **Tài liệu căn cứ:** [BUSINESS_RULES.md](../docs/architect/BUSINESS_RULES.md#b-tài-liệu), [ARCHITECTURE.md](../docs/architect/microservices-v1/ARCHITECTURE.md#2-ownership), [ADR-001](../docs/architect/microservices-v1/ADR-001.md)

## Rà soát đồng bộ & Chốt thiết kế P1 — 2026-11-21

**Hiện trạng P1.** TASK-017A–E đã triển khai và được kiểm chứng qua integration tests trên PostgreSQL 16, Web unit/build và Compose HTTP smoke. TASK-017F vẫn `READY FOR EXECUTION`, với phần cập nhật báo cáo và slide nằm ngoài repo mã nguồn. Quyết định thiết kế và hợp đồng tại [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md), [Document DESIGN](../docs/architect/microservices-v1/document/DESIGN.md), [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md) và [Document FOLDER STRUCTURE](../docs/architect/microservices-v1/document/FOLDER_STRUCTURE.md).

**Tám điểm lệch đã chốt trước khi code:**
1. **Trạng thái phiên bản:** Backend lưu 5 giá trị `PENDING | PROCESSING | SUCCESS | NEEDS_REVIEW | FAILED`. Không lưu `SUPERSEDED` trong DB (tính dẫn xuất `is_serving` / `is_superseded` lúc đọc để bảo toàn Rule `H3`). Web live decoder map `SUCCESS → READY` và `MARKDOWN → MD`.
2. **Lối thoát `NEEDS_REVIEW`:** IDP gắn cờ cần soát cho fast OCR và công thức/bảng DOCX (`review_assisted_pass = true`). Chủ sở hữu tài liệu dùng `POST /api/v1/documents/{document_id}/versions/{version_id}/accept` (nút nhỏ trên UI) để chuyển `NEEDS_REVIEW → SUCCESS` + phát `document.version.published`.
3. **Action chia sẻ/thu hồi ở Core:** Bổ sung `manage_class_documents` vào `ClassAccessAction` (`ACTIVE` class + giáo viên hiện hành), không mượn `manage_class_calendar`.
4. **Xác thực người dùng không chia sẻ `SECRET_KEY`:** Core thêm `POST /internal/v1/auth/introspect` (`X-Service-Key`, kiểm tra chữ ký + `session_version` + `status == ACTIVE`, fail closed, không cache ở P1).
5. **Web Proxy đa upstream & thân nhị phân:** `apps/web` định tuyến tới `CORE_API_URL` hoặc `DOCUMENT_API_URL` theo bảng allowlist, đọc thân bằng `arrayBuffer()` thay `text()`, thêm đủ operations Document và cô lập lỗi 503.
6. **Môi trường Demo P1 vs Cloud P2:** Demo bảo vệ chạy trên **Docker Compose** (tránh trần ~4,5 MB của Vercel Functions với tệp mẫu 10 MB `TP-01`); upload trực tiếp lên **GCS qua signed URL** chuyển sang **P2**.
7. **Ranh giới IDP vs AI Indexing:** `SUCCESS` nghĩa là Canonical artifact đã trích xuất và lưu bền vững (`services/rag/idp/canonical.py` hiện xuất `idp-canonical-1`; harness AI hỗ trợ `idp-canonical-1` và `idp-canonical-2`). Document phát `document.version.published`; AI tự lập chỉ mục bất đồng bộ và chỉ phục vụ khi bundle đủ (`complete: true`). Sửa Hình 2.16 trong báo cáo/slide cho khớp.
8. **Chính sách upload P1:** **`20 MB` (`20_971_520` bytes)** và **`300 trang`**, công bố qua `GET /api/v1/documents/policy` (đồng bộ mock từ 50 MB về 20 MB).

**Kế hoạch triển khai chia nhỏ thành 6 sub-task:**

| Sub-task | Phạm vi chính | Phụ thuộc |
|---|---|---|
| [TASK-017A](TASK-017A-core-document-prereqs.md) | Core: thêm `manage_class_documents` và `POST /internal/v1/auth/introspect` | Core G1/G2 |
| [TASK-017B](TASK-017B-document-service-foundation.md) | Document Backend: `services/document/`, schema `document`, policy `20 MB/300 trang`, validation, grants, reader/download, notes/bookmarks, internal API | `TASK-017A` |
| [TASK-017C](TASK-017C-document-idp-worker-and-review.md) | Document Worker & Review: `worker.py`, tích hợp `services/rag/idp`, `NEEDS_REVIEW`, `/accept`, `/retry`, outbox `document.version.published` | `TASK-017B` |
| [TASK-017D](TASK-017D-web-proxy-and-document-ui.md) | Web Proxy & Live UI: `DOCUMENT_API_URL`, `arrayBuffer()`, `FormData`, decoder `SUCCESS→READY`, nút chấp nhận `NEEDS_REVIEW`, sync 20 MB/300 trang | `TASK-017B`, `TASK-017C` |
| [TASK-017E](TASK-017E-compose-demo-and-deployment.md) | Docker Compose Demo & P2 GCS roadmap: `Dockerfile`, `compose.yaml`, test TP-01 10 MB, cô lập lỗi QAS-01/02 | `TASK-017B`, `TASK-017C`, `TASK-017D` |
| [TASK-017F](TASK-017F-doc-ai-event-and-report-sync.md) | Hợp đồng Document↔AI (`TASK-018`) và Checklist sửa Báo cáo / Slide (Hình 2.16) | Chạy song song từ `ADR-002` |

**Phụ thuộc và nghiệm thu bổ sung:**
- [x] Hoàn tất triển khai và kiểm chứng code/runtime của `TASK-017A` → `TASK-017E`.
- [ ] Hoàn tất `TASK-017F` (hợp đồng Document↔AI và cập nhật báo cáo/slide ngoài repo).
- [x] Document suite kiểm tra IDP thật cho 5 định dạng, `NEEDS_REVIEW` accept, H3 fallback/crash, cross-school/class, thu hồi grant trước lượt mở nguồn kế tiếp, owner-only notes/bookmarks và O2.
- [ ] Kiểm chứng thu hồi quyền giữa lúc AI truy hồi và người dùng mở nguồn trong luồng Document↔AI.
- [x] Core, Document và Web đã có kết quả unit/contract tests.
- [x] Chạy PostgreSQL 16 integration cho quyền tài liệu, thu hồi phiên qua `logout-all`, worker locking và phục hồi lease.
- [x] Chạy Compose smoke cho startup/health, phân quyền DB, luồng PDF 10 MB, giới hạn upload và cô lập lỗi Document.

---

## Lịch sử rà soát — 2026-10-16 (giữ nguyên đối chiếu)

- Chỉ triển khai sau nghiệm thu Core G1/G2; không truy cập SQL Core hoặc coi metadata môn/khối là quyền.
- Bổ sung bookmark bên cạnh SourceBoundNote; ARCHIVED cho sửa notes/bookmarks với nguồn còn quyền (O2); source revoke vẫn giữ chữ tự viết nhưng che quote/open-source.
- Phiên bản chỉ được công bố sau xử lý thành công; retry/crash không mất file hoặc phát index dở; source revoke thắng fallback phiên bản cũ. AI index là dữ liệu dẫn xuất, Document sở hữu file/version/grant.

---

## 1. Mục tiêu
Thiết kế và triển khai Document Service (`services/document/`) độc lập trong monorepo, quản lý thư viện tài liệu cá nhân của Giảng viên, hỗ trợ 5 định dạng tệp, quản lý đa phiên bản, chia sẻ tài liệu cho lớp học (`ClassDocumentGrant`), xử lý tài liệu bất đồng bộ và quản lý ghi chú/bookmark gắn nguồn.

---

## 2. Tổng hợp thiết kế P1 đã chốt (xem chi tiết tại `document/DESIGN.md` & `document/API_CONTRACT.md`)

- **Mô hình dữ liệu (schema `document`):** `documents` (soft-delete `deleted_at` giữ toàn vẹn notes H6/L6), `document_versions` (`PENDING | PROCESSING | SUCCESS | NEEDS_REVIEW | FAILED`, không lưu `SUPERSEDED` mà tính dẫn xuất `is_serving`/`is_superseded`), `class_document_grants` (`partial unique` khi `is_active = true`), `source_bound_notes`, `document_bookmarks`, `outbox_events`, `audit_events`. Chi tiết tại [Document DESIGN](../docs/architect/microservices-v1/document/DESIGN.md#4-mô-hình-dữ-liệu-logic-schema-document-uuid-pk-utc-timestamptz).
- **Quy tắc upload & 5 định dạng (Rule B3):** hỗ trợ `PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN`; giới hạn P1 **20 MB (`20_971_520` bytes)** và **300 trang** công bố qua `GET /api/v1/documents/policy`; kiểm tra magic-byte, ZIP-bomb và từ chối PDF mã hóa trước khi ghi `PENDING`.
- **Vòng đời & lối thoát `NEEDS_REVIEW` (Rule B4, H3):** `PENDING → PROCESSING → SUCCESS | NEEDS_REVIEW | FAILED`. Chỉ chủ sở hữu nguồn (`owner_user_id`) được gọi `POST /api/v1/documents/{document_id}/versions/{version_id}/accept` để duyệt `NEEDS_REVIEW → SUCCESS` (kèm nút nhỏ trên UI); chỉ chủ sở hữu được gọi `POST .../retry` cho `FAILED → PENDING`. Khi v2 lỗi hoặc đang ở `NEEDS_REVIEW`, lớp vẫn đọc v1 `SUCCESS`.
- **Phân quyền liên service (Fail Closed):** xác thực Bearer JWT qua Core `POST /internal/v1/auth/introspect` (`X-Service-Key`, kiểm tra `session_version`, không chia sẻ `SECRET_KEY`); kiểm tra chia sẻ/thu hồi qua Core `manage_class_documents` (`ACTIVE` class + giáo viên hiện hành); kiểm tra đọc/tải/note qua `read_document_source` + grant hiện hành (`is_active = true`).
- **Tách IDP và Lập chỉ mục:** `SUCCESS` nghĩa là Canonical artifact đã trích xuất và lưu bền vững, phát sự kiện outbox `document.version.published`. AI Service tự lập chỉ mục bất đồng bộ và chỉ phục vụ khi bundle đủ (`complete: true`).
- **Hợp đồng API chính thức & Web Proxy:** xem danh sách đầy đủ endpoint Public (`/api/v1`), Internal (`/internal/v1`) và bảng Web Proxy allowlist (`DOCUMENT_API_URL`, `arrayBuffer()`) tại [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md).

---

## 3. Tiêu chí nghiệm thu (Acceptance Criteria)
- [x] Contract tests cho `manage_class_documents` và `POST /internal/v1/auth/introspect` đạt ở [TASK-017A](TASK-017A-core-document-prereqs.md).
- [x] Chạy integration tests PostgreSQL 16 cho hai giao diện nội bộ của Core.
- [x] Round trip IDP thật cho `PDF`, `PPTX`, `DOCX`, `TXT`, `MARKDOWN` trên PostgreSQL 16 + local storage. Contract test chặn tệp >20 MB (`413`) và >300 trang/ZIP-bomb/PDF mã hóa (`422`).
- [x] Qua Web proxy trong Docker Compose, tải lên PDF 10 MB, chờ `SUCCESS`, cấp grant và kiểm chứng Student đọc/tải. Tệp vượt 20 MB trả `413`.
- [ ] Mở rộng smoke trên Docker Compose sang `PPTX`, `DOCX`, `TXT` và `MARKDOWN`.
- [x] Contract test `NEEDS_REVIEW` → chủ sở hữu gọi `/accept` → `SUCCESS` và phát outbox `document.version.published`. Đồng giáo viên bị từ chối.
- [ ] Chạy luồng review trên trình duyệt với mẫu fast OCR hoặc DOCX có công thức kích hoạt `NEEDS_REVIEW` trên Compose.
- [x] Test Document kiểm tra lỗi phiên bản (Rule H3): upload v2 bị `FAILED` hoặc đang `NEEDS_REVIEW` → lớp học vẫn đọc được v1 `SUCCESS`.
- [x] Contract test thu hồi chia sẻ (Rule B6). Lượt đọc kế tiếp che nội dung nguồn, API tải từ chối và `active-grants` loại nguồn ngay.
- [x] Contract test ghi chú cá nhân (H6/L6/O2). Thu hồi grant giữ chữ tự viết và che `source_quote`. Lớp `ARCHIVED` cho sửa ghi chú khi còn quyền nguồn.
- [x] Web live build, binary proxy test (`arrayBuffer()`) và decoder (`SUCCESS → READY`, `MARKDOWN → MD`) đạt.
- [x] Kiểm chứng tuyến Core/Document bằng request HTTP tới `web:3000` khi dừng Document.
- [ ] Chạy manual browser E2E với Core + Document trong Docker Compose, gồm upload tệp mẫu 10 MB.

### Bằng chứng kiểm định (2026-11-21)

Windows 11 · Python 3.12.5 · Node 24.15.0 · Docker Desktop 29.7.2 (linux) · PostgreSQL 16.

| Phạm vi | Lệnh và kết quả |
|---|---|
| Core và Document (chạy tại mỗi service) | `.\.venv\Scripts\ruff.exe check --no-cache app tests migrations` → `All checks passed!` (exit 0). `.\.venv\Scripts\ruff.exe format --no-cache --check app tests migrations` → `148 files already formatted` (Core), `52 files already formatted` (Document), exit 0 cho cả hai. |
| Core | `.\.venv\Scripts\pytest.exe -p no:cacheprovider -W error --cov=app.modules.identity.public --cov-branch --cov-fail-under=100 tests/test_common.py tests/classroom/test_contract.py tests/identity/test_introspect.py tests/identity/test_openapi.py tests/identity/test_public.py tests/identity/test_schemas.py tests/identity/test_credential_token.py tests/security/test_boundaries.py` → **132 passed, 1 skipped**, **100.00%** statement + branch coverage cho `app.modules.identity.public`. |
| Core · PostgreSQL 16 | **`25 passed in 6.69s` (0 skipped)** trong `tests/classroom/test_integration.py` và `tests/identity/test_introspect.py`. Xem [TASK-017A](TASK-017A-core-document-prereqs.md). |
| Document · PostgreSQL 16 | **`99 passed in 11.59s`**, gồm Alembic `upgrade head`/`downgrade base`, `FOR UPDATE SKIP LOCKED`, IDP thật cho 5 định dạng, `NEEDS_REVIEW → /accept → SUCCESS`, H3 fallback, H6/L6/O2 redaction và packaging contract. Xem [TASK-017B](TASK-017B-document-service-foundation.md) và [TASK-017C](TASK-017C-document-idp-worker-and-review.md). |
| Web | `npm run check` → `check:boundaries` đạt, `tsc --noEmit` có 0 lỗi, **277/277 tests passed** (exit 0). `NEXT_PUBLIC_DATA_MODE=live npm run build` → biên dịch thành công, **32/32** trang, `check:release` đạt (`Release output contains no preview fixture marker.`), exit 0. |
| Compose (repo root) | `docker compose config --quiet` → exit 0. `docker compose up --build -d --wait` → tất cả service `Healthy` hoặc `Exited 0`, Core/Document readiness đều `200`, `document_svc` bị từ chối đọc `core.users`. Smoke HTTP xác nhận PDF `10,500,004` byte đi hết luồng upload/xử lý/grant/đọc/tải, tệp `20,971,521` byte trả `413` và khi dừng Document, `/api/v1/classes` trả `200`, `/api/v1/documents/policy` trả `503`. Xem [TASK-017E](TASK-017E-compose-demo-and-deployment.md). |

Luồng Web được kiểm chứng bằng unit tests, live build và request HTTP trực tiếp tới `web:3000`. Kiểm thử thao tác thủ công trong trình duyệt còn mở. Nghiệm thu toàn bộ tải TP-01 còn mở theo [Quality Attributes](../docs/architect/microservices-v1/QUALITY_ATTRIBUTES.md#1-cấu-hình-kiểm-thử-tp-01).
