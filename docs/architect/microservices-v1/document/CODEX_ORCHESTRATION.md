# Document Service — Codex CLI Orchestration & Priority Reconciliation Playbook

**Vai trò tài liệu:** Cẩm nang điều phối thực thi cho **Coordinator** (người điều phối & nghiệm thu) khi giao việc cho **Codex CLI** (`sol 6.2` / `gpt-6.2-sol`, reasoning effort `high`) để hoàn thiện 100% **Document Service**.  
**Nguồn chuẩn kiến trúc & nghiệp vụ:** [BUSINESS_RULES.md](../../BUSINESS_RULES.md) · [ADR-002](../ADR-002-document-service.md) · [DESIGN.md](DESIGN.md) · [API_CONTRACT.md](API_CONTRACT.md) · [FOLDER_STRUCTURE.md](FOLDER_STRUCTURE.md)

> [!NOTE]
> **Đối chiếu báo cáo đồ án (`Bao_cao_DATN.docx` nằm ngoài repo Git):** Toàn văn và các hình vẽ trong `C:\Users\Trinh Tuyen\OneDrive - HUCE\Desktop\Workspace\DATN_2\Bao_cao_DATN.docx` (hạn nộp `16/11–22/11/2026`, bảo vệ `28–29/11/2026`) đã được rà soát với mã nguồn thực tế; toàn bộ 5 sai lệch kỹ thuật và 11 vị trí cần cập nhật trạng thái trong báo cáo đã được lập thành checklist chi tiết tại [TASK-017F](../../../../tasks/TASK-017F-doc-ai-event-and-report-sync.md).

---

## 1. Ma trận phạm vi P0 / P1 / P2 gắn với hệ thống Task

| Mức ưu tiên | Mục tiêu & Hạng mục | Task quản lý | Trạng thái hiện tại |
|---|---|---|---|
| **P0** *(Bắt buộc cho demo bảo vệ)* | Core prerequisites (`manage_class_documents`, `POST /internal/v1/auth/introspect`, seed demo) | [TASK-017A](../../../../tasks/TASK-017A-core-document-prereqs.md) | `DONE — VERIFIED` (PG16: 25 passed; branch coverage `app.modules.identity.public` `100.00%`) |
| **P0** *(Bắt buộc cho demo bảo vệ)* | Nền tảng Document Service, thư viện, upload 5 định dạng (`PDF/PPTX/DOCX/TXT/MARKDOWN`), hạn mức `20 MB` / `300` trang (`GET /documents/policy`), grant/revoke, student reader/stream | [TASK-017B](../../../../tasks/TASK-017B-document-service-foundation.md) | `DONE — VERIFIED` (PG16 + Alembic roundtrip) |
| **P0** *(Bắt buộc cho demo bảo vệ)* | Worker IDP, lease + `SKIP LOCKED`, retry, fallback bản `SUCCESS` gần nhất theo `H3`, `SUPERSEDED` dẫn xuất, chấp nhận `NEEDS_REVIEW` (`accept`), `active-grants` + `canonical` internal, `DOC-T01..T10` | [TASK-017C](../../../../tasks/TASK-017C-document-idp-worker-and-review.md) | `DONE — VERIFIED` (PG16 tổng Document: `99 passed in 11.59s`; `ruff` 52 files: `0` lỗi) |
| **P0** *(Bắt buộc cho demo bảo vệ)* | Web binary proxy (`DOCUMENT_API_URL`, allowlist 20 thao tác), UI Thư viện giảng viên + Chi tiết phiên bản (`accept`, `retry`, `NEEDS_REVIEW`), Chia sẻ lớp, Reader/Download học sinh | [TASK-017D](../../../../tasks/TASK-017D-web-proxy-and-document-ui.md) | `DONE — VERIFIED` (`npm run check`: 277/277 tests; `live` build `32/32` pages) |
| **P0** *(Bắt buộc cho demo bảo vệ)* | Docker Compose full stack (`db`, `document-migrate`, `document`, `document-worker`, `core`, `notification-worker`, `web`), cô lập schema `document_svc`, smoke E2E `10,500,004` byte PDF & lỗi `413` / `503` | [TASK-017E](../../../../tasks/TASK-017E-compose-demo-and-deployment.md) | `DONE — VERIFIED` (Compose runtime healthy, HTTP smoke passed) |
| **P1** *(Hoàn thiện P1 đã code trong service + UI)* | Ghi chú & bookmark (`F16`, `DOC-T11..T13`), che `source_quote` khi mất quyền nguồn (`H6/L6`), giữ quyền sửa trên lớp đã lưu trữ (`O2`), xóa nguồn cảnh báo `409` khi đang chia sẻ nhiều lớp (`B6`) | [TASK-017B](../../../../tasks/TASK-017B-document-service-foundation.md) · [TASK-017D](../../../../tasks/TASK-017D-web-proxy-and-document-ui.md) · [TASK-017H](../../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md) | `DONE — VERIFIED` ở PG16 + Compose full E2E (`13/13 PASS`); **chỉ còn UI manual walkthrough theo `DEMO_RUNBOOK.md`** *(trình soạn Web chưa gửi `source_quote`, đã nêu trong Runbook)* |
| **P0 + P1** *(Chốt CI & Nghiệm thu bảo vệ)* | CI cho `services/document/` + nâng cấp `web-build` & `compose-smoke` trong GitHub Actions | [TASK-017G](../../../../tasks/TASK-017G-document-ci-and-compose-smoke.md) | `DONE — VERIFIED` (local YAML, Ruff `53` files, Web `277/277`, PG16 `99 passed` `94.19%`, Compose outage/recovery) |
| **P0 + P1** *(Chốt CI & Nghiệm thu bảo vệ)* | Script Compose E2E tự động phủ đủ 5 định dạng, `NEEDS_REVIEW -> accept`, `H3`, `B6`, `H6/L6/O2`, crash recovery `QAS-10`, benchmark `QAS-01/02` + Runbook demo trình duyệt (`DEMO_RUNBOOK.md`) | [TASK-017H](../../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md) | `DONE — VERIFIED` (`verify_compose_e2e.py` `13/13 PASS` trong `72.41s`; p95 `32.4–40.0 ms`; `DEMO_RUNBOOK.md` sẵn sàng) |
| **Đồng bộ AI & Báo cáo** | Đồng bộ Event/API với AI (`document.version.published`, `active-grants`, `canonical`) & checklist sửa báo cáo/slide ngoài Git | [TASK-017F](../../../../tasks/TASK-017F-doc-ai-event-and-report-sync.md) · [TASK-018](../../../../tasks/TASK-018-ai-service-rag-tutor.md) | `TODO / IN PROGRESS` |
| **P2** *(Sau bảo vệ)* | GCS & signed URL upload (`StorageBackend`), quét mã độc, hạn mức dung lượng, chính sách lưu giữ (`OD-02`/`OD-03`), gửi thông báo khi chia sẻ *(blocked by Core internal notification intake API)*, deploy Cloud Run | [TASK-017I](../../../../tasks/TASK-017I-document-p2-cloud-and-governance.md) | `DEFERRED (P2)` |

---

## 2. Đối chiếu Thiết kế tóm tắt (Mục 3) ↔ Hiện thực v1 đã kiểm chứng

> [!NOTE]
> Khi giao việc cho Codex CLI, **không viết lại mã nguồn đang chạy để ép theo tên gọi khái niệm trong Mục 3** nếu khác biệt chỉ nằm ở tên biểu tượng hoặc cách tổ chức hợp đồng đã được `TASK-017A..E` kiểm chứng. Bảng dưới đây khóa chặt ánh xạ 1-1 để cả Coordinator, Codex CLI và Báo cáo đồ án thống nhất hoàn toàn.

| Thành phần trong Mục 3 / `Bao_cao_DATN.docx` | Hiện thực đã kiểm chứng trong `services/document/` & `apps/web/` | Quy tắc điều phối |
|---|---|---|
| Bảng riêng `processing_jobs` (lease, `SKIP LOCKED`, `attempts`) | Cột claim/lease (`attempt_count`, `available_at`) nằm trực tiếp trên `document.document_versions`; worker dùng `SELECT ... FOR UPDATE SKIP LOCKED` khi claim và `pg_advisory_xact_lock` + một transaction trong `processing.py` khi commit kết quả | **Giữ nguyên hiện thực v1** ở P0/P1 (tránh migration phá schema 7 bảng đã kiểm chứng trong `0001_initialize_document_schema.py`). Nếu lên P2 cần tách bảng `processing_jobs` đa tác vụ, tạo migration mới (không sửa `0001`). |
| Interface `BlobStore` + khóa theo `SHA-256` | Interface `StorageBackend` + `LocalVolumeStorage` trong `services/document/app/common/storage.py`; khóa lưu là `source/{document_id}/{version_id}` và `canonical/{document_id}/{version_id}.json` (ghi tạm + `fsync` + `os.replace`); `source_sha256`/`canonical_sha256` kiểm chứng toàn vẹn (`StorageIntegrityError`), re-upload vẫn tạo phiên bản mới chứ không bỏ qua IDP | **Giữ tên `StorageBackend` / `LocalVolumeStorage`**. Trong báo cáo/slide giải thích `StorageBackend` là lớp trừu tượng BlobStore (`LocalVolumeStorage` ở P1, `GCSStorageBackend` ở P2) và `SHA-256` dùng để kiểm chứng toàn vẹn tệp + tránh AI lập chỉ mục trùng bản Canonical. |
| 6 nhóm bảng (`documents`, `document_versions`, `class_document_grants`, `processing_jobs`, `source_notes`/`bookmarks`, `outbox_events`) | `0001_initialize_document_schema.py` tạo **7 bảng** trong schema `document`: `documents`, `document_versions`, `class_document_grants` (partial unique `UNIQUE(class_id, document_id) WHERE is_active = true`, vẫn giữ lịch sử qua `is_active` & `revoked_at`), `source_bound_notes`, `document_bookmarks`, `outbox_events`, `audit_events` | Mọi truy vấn và kiểm thử tích hợp phải bám đúng 7 bảng thực tế trong [DESIGN.md](DESIGN.md). Tuyệt đối không truy vấn sang schema `core`. |
| Phục vụ người đọc từ trang Canonical + stream file gốc sau kiểm tra quyền | Hợp đồng Canonical trang khớp `pageContents` của Web; tải file gốc stream trực tiếp qua Document sau khi kiểm tra đồng thời `Core read_document_source` (fail closed `503`) + grant còn hiệu lực | Không dùng signed URL cho luồng tải ở P0/P1; giữ không cache quyết định quyền đọc. |
| "10 hàm của Web port" & đường dẫn rút gọn `/versions/{n}/accept`, `/versions/{n}/retry`, `/documents/{id}/grants` | [port.ts](../../../../apps/web/src/lib/data/port.ts#L74-L87) có **12 hàm** trong `DataAdapter['document']` (`previewClassSource`, `uploadDocument`, `uploadVersion`, `acceptVersion`, `retryProcessing`, `downloadClassDocument`, `shareDocument`, `revokeShare`, `deleteDocument`, `saveNote`, `deleteNote`, `toggleBookmark`) + các màn `workspace.load`; proxy [API_CONTRACT.md](API_CONTRACT.md#4-hợp-đồng-web-proxy-allowlist-appswebsrclibapioperationsts--proxyts) mở **20 thao tác public** (+ 2 internal endpoint cho AI), trong đó `acceptVersion`/`retryProcessing` dùng **`versionId` (UUID)** | **Giữ nguyên UUID `version_id` và hợp đồng 20 public + 2 internal endpoints** trong [API_CONTRACT.md](API_CONTRACT.md) để không làm gãy allowlist của `operations.ts`. Khi sửa báo cáo đồ án, cập nhật con số 12 hàm port & UUID `version_id`. |
| Khác biệt trong `Bao_cao_DATN.docx` (`Hình 3.6`, `Bảng 2.4`, `Bảng 3.10`, `Canonical v2`) | Upload trả `201 Created` (`PENDING`), worker chỉ xuất Canonical (`idp-canonical-1` hiện hành; `idp_runner.py` hỗ trợ `idp-canonical-1`/`2`) và ghi outbox `document.version.published` — **không** gọi thẳng Worker lập chỉ mục hay chờ callback `indexed`; `GET /internal/v1/documents/{document_id}/versions/{version_id}/canonical` phục vụ AI đọc Canonical; `document.grant.changed` đã ghi outbox nhưng push thông báo in-app sang Core thuộc P2 | **Không sửa code `services/document/`**. Đồng bộ các sơ đồ/bảng trong `Bao_cao_DATN.docx` theo checklist [TASK-017F](../../../../tasks/TASK-017F-doc-ai-event-and-report-sync.md) (mục 3). |

---

## 3. Lịch trình điều phối Codex CLI theo Wave

### Quy tắc vàng khi gọi Codex CLI
1. **Model & reasoning:** dùng Codex CLI với model `sol 6.2` / `gpt-6.2-sol`, reasoning effort `high`.
2. **Tách quyền Sandbox:** Codex CLI chạy cục bộ không có mạng (hoặc bị chặn Docker/pip tùy môi trường) — **Coordinator chuẩn bị sẵn `.venv`, PostgreSQL/Docker daemon bên ngoài sandbox trước**, sau đó giao Codex CLI chỉnh đúng phạm vi file cho phép (**File Allowlist**).
3. **1 task = 1 wave hẹp:** Không gộp `TASK-017G` và `TASK-017H` vào cùng 1 phiên Codex nếu chạm chéo file; chỉ chạy song song khi tập file sửa hoàn toàn rời nhau (non-overlapping allowlist).
4. **Cấm tuyệt đối:** Codex không được sửa `docs/architect/BUSINESS_RULES.md`, không sửa migration đã áp dụng (`services/core/migrations/*`, `0001_initialize_document_schema.py`), không chạy `git push`/`git add -A`/commit secret hay `.env`.

### Bảng phân công & File Allowlist từng Wave

| Wave | Task | Độ ưu tiên | File Allowlist cho Codex CLI | Tiêu chí Coordinator nghiệm thu |
|---|---|---|---|---|
| **Wave 1A** *(chạy // 1B)* | [TASK-017G](../../../../tasks/TASK-017G-document-ci-and-compose-smoke.md) | P0/P1 | `.github/workflows/core-ci.yml`, `.github/workflows/document-ci.yml` *(nếu tách job)*, `services/document/pyproject.toml` *(chỉ nếu cần CI script metadata)* | YAML hợp lệ; toàn bộ lệnh mà CI sẽ gọi (Core suite, Document suite + ruff + coverage, Web `npm run check` + `live` build, Compose smoke) chạy pass ngay trên máy cục bộ/CI |
| **Wave 1B** *(chạy // 1A)* | [TASK-017H](../../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md) | P0/P1 | `services/document/scripts/*` *(hoặc script E2E chuyên biệt theo spec 017H)*, `docs/architect/microservices-v1/document/DEMO_RUNBOOK.md` | Script chạy **live trên `docker compose`** pass đủ 5 định dạng, `NEEDS_REVIEW -> accept`, `H3` fallback, `B6` `409` + `confirm_multi_class`, `H6/L6/O2`, crash recovery `QAS-10`, benchmark `QAS-01/02`; Coordinator walkthrough runbook trình duyệt |
| **Wave 1C** *(song song ngoài repo)* | [TASK-017F](../../../../tasks/TASK-017F-doc-ai-event-and-report-sync.md) | P0 + Báo cáo | `Bao_cao_DATN.docx` *(trước hạn 22/11/2026)* + slide (ngoài Git) + [TASK-018](../../../../tasks/TASK-018-ai-service-rag-tutor.md) | Khớp Hình 2.16, Hình 3.6, Bảng 2.8 (7 bảng) & 3.10, `SUPERSEDED` dẫn xuất, `NEEDS_REVIEW -> accept`, 12 hàm Web port, `StorageBackend` / `LocalVolumeStorage` |
| **Wave 2** *(chỉ mở sau bảo vệ / P2)* | [TASK-017I](../../../../tasks/TASK-017I-document-p2-cloud-and-governance.md) | P2 | Chưa mở trước khi hoàn thành Wave 1A–1C và chốt quyết định `OD-02`, `OD-03`, Core internal notification API | Chia thành từng PR nhỏ độc lập (`P2-1`..`P2-6`), không trộn hạ tầng cloud với thay đổi nghiệp vụ Core |

---

## 4. Mẫu Prompt chuẩn cho Coordinator giao việc Codex CLI

Khi giao [TASK-017G](../../../../tasks/TASK-017G-document-ci-and-compose-smoke.md) hoặc [TASK-017H](../../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md), Coordinator dùng khung lệnh sau (điền đúng đường dẫn task đang chạy):

```text
Đọc AGENTS.md, docs/architect/BUSINESS_RULES.md, docs/architect/microservices-v1/ADR-002-document-service.md,
docs/architect/microservices-v1/document/CODEX_ORCHESTRATION.md và tasks/TASK-017<G|H>-...md.
Chỉ được phép tạo/sửa các tệp trong File Allowlist của TASK-017<G|H>.
Không sửa BUSINESS_RULES.md, không sửa migration đã áp dụng, không thêm secret hay tệp .env vào repo.
Sau khi code xong, chạy đầy đủ lệnh kiểm chứng trong mục Verification của TASK-017<G|H> và báo cáo nguyên văn kết quả thực tế (pass/fail/skipped).
```

---

## 5. Sổ tay lỗi môi trường Windows / PG16 / Docker (Bắt buộc nhớ trước khi nghiệm thu)

Các lỗi dưới đây đã gặp thực tế trong `TASK-017A..E` và cần đưa trực tiếp vào prompt/kiểm định:

1. **OneDrive / Windows file lock với cache tool Python:** Luôn truyền `ruff ... --no-cache` và `pytest -p no:cacheprovider ...` để tránh lỗi `PermissionError` hoặc khóa thư mục `.pytest_cache` / `.ruff_cache` khi OneDrive đang đồng bộ.
2. **Cảnh báo thành lỗi (`-W error`):** Bộ `dev` dependencies của service cần `httpx2>=2,<3` / tương thích để chạy `pytest -W error` sạch cảnh báo deprecation.
3. **Bẫy savepoint với `pg_advisory_xact_lock` trên PostgreSQL:** Không bao giờ lồng 2 context `session_factory()` chồng nhau quanh lệnh giữ transaction/advisory lock vì `join_transaction_mode="create_savepoint"` trong fixture test sẽ rollback savepoint bên trong. Transaction worker/state trong Document giữ mô hình 1 transaction nhất quán.
4. **Line ending cho script shell bind-mount vào Linux container:** Mọi `.sh` chạy trong container (`services/document/docker/init-document-db.sh`, script CI/E2E `.sh`) bắt buộc giữ `LF` (`*.sh text eol=lf` đã khóa trong `.gitattributes`), tránh lỗi `/bin/sh^M: bad interpreter` hoặc fail `initdb`.
5. **Biến môi trường mạng trong Docker Compose:** Không để `CORE_API_URL` hoặc `DOCUMENT_API_URL` trỏ về `127.0.0.1` bên trong container `web` / `document`; khi chạy Compose phải dùng tên service nội bộ (`http://core:8000`, `http://document:8001`). Luôn dọn `.env` tạm nếu chỉ tạo để test cục bộ trước khi kết thúc phiên.
6. **Cô lập quyền CSDL (`document_svc`):** Tài khoản DB của Document chỉ có quyền trên schema `document`, **bị từ chối** đọc ghi schema `core` (ví dụ `core.users`). Mọi bài test/script Compose E2E nên có bước xác minh âm tính quyền DB này.
