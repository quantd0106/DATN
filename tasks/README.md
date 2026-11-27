# Danh mục công việc — Athena / RAG Course Assistant

**Trạng thái nguồn chuẩn (2026-11-22):** `main` + hiện trạng triển khai 4 backend services (`Core`, `Document`, `AI` + `RAG`, `Learning`) và `apps/web` (`71` routes, `3` BFF proxies); chỉ `Learner Intelligence` (`TASK-020`) còn ở trạng thái `PROPOSED`. Policy/data/production và các benchmark/E2E gates vẫn giữ nguyên điều kiện nghiệm thu riêng.

### Bằng chứng hiện hành
- Core PostgreSQL 16 **360 passed/0 skipped**, Identity public interface **100%**, tổng coverage **88%**; Web 251 test, live build, Compose smoke và Vercel checks xanh.
- Core PostgreSQL 16 **368 passed/0 skipped**, total coverage **89%**.
- Core PostgreSQL 16 **380 passed/0 skipped**, total coverage **88%**; Web build, Compose smoke và Vercel checks xanh.
- Mốc Core-06 subset (2026-10-30, CKEY VM KR with provider-shared host not isolated, main `ec10fa9`, PostgreSQL 16.15/migration 0013): **58.521/58.521 HTTP 200**, backend p95 **21,84 ms**, client loopback p95 **672,04 ms**, client max **4,65 giây**, scope mismatch **0**; Core class-list threshold đạt theo harness. Host-side CPU/RAM utilization không được đo. Đây không phải user-facing/Cloud Run p95 và không xác nhận full TP-01 (`full_tp01_verified=false`). Xem [report](../services/core/test-evidence/task015/core-load-ckey-2026-10-30.json).
- Working-tree Core verification (2026-10-30, CKEY PostgreSQL 16.15/Python 3.12.16): migration 0014 `upgrade head` + `alembic check`, synthetic mapping/FK validation regression, Ruff check/format and **381 passed, 0 failed**. Sau đó production-bootstrap regression riêng trên PostgreSQL 16 Neon staging đạt **8 passed**; fixture chỉ tạo/xóa DB `test_maintenance_*` riêng.
- Staging Core API revision `lumi-core-api-00011-xss`, health/DB ready; SMTP version 2 đã gửi invitation, người nhận redeem và Admin staging ở trạng thái ACTIVE/email verified (kèm recovery-delivery và retry/reissue coverage).
- Production mock: Neon PostgreSQL 16 `lumi-production-mock/neondb` đã migrate tới head 0014; Cloud Run `lumi-core-api-production` revision `lumi-core-api-production-00001-qcm`, `/health/live`, `/health/ready`, `/docs`, và Vercel Production CORS preflight đều 200. Admin pending và Lumi Mock School đã bootstrap; worker batch thành công, chưa xác minh người nhận thấy email.
- Vercel Production `CORE_API_URL`/`WEB_ORIGIN` đã áp dụng, Preview vẫn giữ Core staging. Sau redeploy, trang trả 200 và `/api/v1/auth/refresh` qua proxy trả `401 UNAUTHENTICATED` khi không có session, không còn upstream 503.
- Production image được build từ working tree chưa commit/push (`production-mock-20261030`). Trước lần build/deploy tiếp theo cần đồng bộ source để runtime không lệch repo.
- Browser E2E Preview: Admin tạo lớp AUTO nối homeroom/giáo viên; Teacher thấy lớp và trạng thái archive; Student mở lớp trước archive và sau đó dashboard còn 0 lớp hoạt động. Thông báo test của Teacher/Student đã đọc; lớp test được archive. Activation/redeem invitation và full workflow acceptance chưa kiểm tra.

### Bằng chứng kiểm định (2026-11-21)

TASK-017 P1 · Windows 11 · Python 3.12.5 · Node 24.15.0 · Docker Desktop 29.7.2 (linux) · PostgreSQL 16.

- **Core.** PostgreSQL 16 đạt **`25 passed in 6.69s` (0 skipped)** cho bộ ca Classroom và introspection. Ruff check/format đạt, 148 tệp đúng định dạng. Lần chạy unit/contract trước đó đạt **132 passed, 1 skipped**, coverage `app.modules.identity.public` **100.00%** cả statement và branch. Chi tiết lệnh tại [TASK-017A](TASK-017A-core-document-prereqs.md).
- **Document.** Suite PostgreSQL 16 đạt **`99 passed in 11.59s`**, gồm migration, worker lease, IDP thật cho 5 định dạng, `/accept`, H3, H6/L6/O2 và packaging contract. Ruff check/format đạt, 52 tệp đúng định dạng. Chi tiết lệnh tại [TASK-017B](TASK-017B-document-service-foundation.md) và [TASK-017C](TASK-017C-document-idp-worker-and-review.md).
- **Web.** `npm run check` đạt boundary, 0 lỗi TypeScript, **277/277 tests passed**. `NEXT_PUBLIC_DATA_MODE=live npm run build` đạt **32/32** trang và `check:release`. Luồng live được kiểm chứng qua HTTP tới `web:3000`. Các thao tác tay trên trình duyệt còn mở. Chi tiết tại [TASK-017D](TASK-017D-web-proxy-and-document-ui.md).
- **Compose.** `docker compose up --build -d --wait` đưa tất cả service tới `Healthy` hoặc `Exited 0`. Readiness Core/Document trả `200`, role `document_svc` bị từ chối đọc `core.users`. PDF `10,500,004` byte qua upload/xử lý/grant/đọc/tải, tệp `20,971,521` byte trả `413`. Khi dừng Document, API lớp trả `200` và Document policy trả `503`. Chi tiết tại [TASK-017E](TASK-017E-compose-demo-and-deployment.md).

### G1/G2 gates còn mở
Mapping School dữ liệu mẫu/cơ sở đã hoàn tất ở tầng công cụ maintenance offline; chi tiết manifest không lưu trong repo. Production mock chưa có legacy user rows để backfill; nhập tài khoản tương lai vẫn cần report/plan/apply với manifest có thẩm quyền. Policy thủ công cho ACCESS/EXPORT/CORRECTION đã được duyệt và kiểm thử; DELETE/retention liên service tiếp tục deferred. Core Production bootstrap đã triển khai với `Lumi Mock School`; Vercel proxy đã nối Core. SMTP invitation/recovery/retry đã được kiểm thử staging. Benchmark Core class-list subset mới dừng ở phạm vi subset (`full TP-01` chưa nghiệm thu đầy đủ). Hiện tại cả `Document` (`P0/P1 IMPLEMENTED — VERIFIED`), `AI` (`services/ai` + `services/rag`) và `Learning` (`services/learning`) đều đã triển khai; chỉ còn `Learner Intelligence` (`TASK-020`) ở trạng thái `PROPOSED`. Các bằng chứng theo task có ngày bên dưới là lịch sử nếu chưa được cập nhật riêng.

## 1. Nguồn chuẩn và phạm vi

1. [Business Rules](../docs/architect/BUSINESS_RULES.md) quyết định nghiệp vụ; không sửa rule để khớp code.
2. [ADR-001](../docs/architect/microservices-v1/ADR-001.md), [Architecture](../docs/architect/microservices-v1/ARCHITECTURE.md) quyết định ranh giới năm service.
3. [Core Design](../docs/architect/microservices-v1/core/DESIGN.md), [API Contract](../docs/architect/microservices-v1/core/API_CONTRACT.md), [Folder Structure](../docs/architect/microservices-v1/core/FOLDER_STRUCTURE.md) hướng dẫn triển khai Core; phân biệt IMPLEMENTED/PROPOSED/LEGACY.
4. [Roadmap](../docs/architect/microservices-v1/IMPLEMENTATION_ROADMAP.md), [Quality Attributes](../docs/architect/microservices-v1/QUALITY_ATTRIBUTES.md), [ARCHITECTURE](../docs/architect/microservices-v1/ARCHITECTURE.md) quy định gate, bằng chứng và ranh giới kiến trúc.
5. [Core README](../services/core/README.md) mô tả command, public signatures và giới hạn thực tế. Checklist/bằng chứng có ngày trong task là lịch sử của phạm vi được ghi, không tự áp dụng cho revision mới.

Athena phục vụ một trường THCS/THPT, khối 6–12. Giữ ba role ADMIN/LECTURER/STUDENT; co-teacher là assignment theo lớp. Admin cấp Student username/mã kích hoạt, quản lý roster, phân công, enrollment AUTO/MANUAL và archive/restore. Không triển khai join code, teacher-created class, Student self-join/leave hay teacher ownership transfer trong MVP.

## 2. Ownership và hiện trạng

Repo main có Common, Identity, Classroom Admin/access API, Social, Notification inbox/worker, Administration backend, health, migration 0001–0013 và Compose/CI; working tree hiện có thêm migration 0014. Administration có import Student CSV/XLSX, staff/role/status/audit và data-request review; ACCESS/EXPORT/CORRECTION có quy trình Admin thủ công, DELETE/retention vẫn giữ IN_REVIEW. Web MVP được khôi phục và tích hợp live Core. UI demo riêng bị gitignore và không phải Web nghiệp vụ. Production mock API/bootstrap đã triển khai; #13 đóng theo xác nhận , còn browser E2E đầy đủ cần hoàn tất. Image Production hiện chưa đồng bộ với source trên GitHub main.

## 3. Ma trận 20 task và phần cần đồng bộ

| Task / giai đoạn | Hiện trạng và việc cần làm | Phụ thuộc / follow-up |
|---|---|---|
| [001 Common](TASK-001-core-common-foundation.md) · G0 | Có nền tảng; giữ DB-based auth/school scope, audit allowlist, không đưa nghiệp vụ lớp vào Common | #13 legacy school scope |
| [002 Identity core](TASK-002-identity-auth-core.md) · G1 | Student activation/recovery và staff identity có trong Core; Gmail staging delivery/retry/reissue đã xác nhận | Mapping School offline & Production Admin mock bootstrap |
| [003 Identity HTTP](TASK-003-identity-api-router.md) · G1 | Có auth/profile/Student API/OpenAPI; giữ register 403, no-store code, tránh trùng Admin routes | #21 Preview E2E |
| [004 Identity public/data requests](TASK-004-identity-public-interface.md) · G1 | DTO/helpers và manual attestation ACCESS/EXPORT/CORRECTION đã được kiểm thử PostgreSQL; DELETE/retention còn deferred | #13 mapping thật; #15 Web Admin integration |
| [005 Classroom schema](TASK-005-classroom-models-schema.md) · G1 | Migration 0014 nullable FK và synthetic mapping/validation regression đạt PostgreSQL 16.15; mapping thật chưa có | #13 authoritative mapping |
| [006 Enrollment/transfer](TASK-006-classroom-membership-flow.md) · G1 | Có Admin sync, preview/version confirm, MANUAL và chuyển homeroom/MANUAL giữ lịch sử; Learning attempt policy còn pending | #24 Learning attempt policy |
| [007 Lifecycle/phân công](TASK-007-classroom-lifecycle-colecturer.md) · G1 | Có Admin PRIMARY/ASSISTING, thay giáo viên chính, archive/restore; consumer Learning approval còn pending | #24 Learning approval contract |
| [008 Classroom public/internal](TASK-008-classroom-public-interface.md) · G1 | Có action access, lịch sử cá nhân, school scope và X-Service-Key; consumer grant/grade cần tích hợp | #22–#24 consumers |
| [009 Feed](TASK-009-social-feed.md) · G1 | Feed CRUD/soft-delete, author DTO, cursor/sort và membership/resource checks đã có; CI xanh | E2E browser workflow đầy đủ |
| [010 Comments](TASK-010-social-comments.md) · G1 | Body 1..10.000, class/post/comment checks, Feed public và moderation/outbox | 008/009/012; Learning consumer #24 |
| [011 Calendar](TASK-011-social-calendar.md) · G1 | PRIMARY/ASSISTING, starts_at < ends_at; Core event và Learning projection riêng | Learning API/event thật chưa triển khai (#24) |
| [012 Notification/outbox](TASK-012-notification-module.md) · G1 | Worker chỉ giao V2 có recipient snapshot; V1 legacy được giữ và không phát. Migration regression test xác nhận hành vi này. | Credential email queue có trong #16; không giao V1 từ audience hiện tại |
| [013 Administration/audit](TASK-013-administration-audit.md) · G1 | Staff/status/roles, audit, CSV/XLSX preview/confirm/reconciliation; staging invitation/recovery delivery verified; manual resolution attestation đang được đồng bộ. | DELETE/retention và Web Admin integration (#15) |
| [014 Migrations/seed](TASK-014-core-migrations-db.md) · G2 | Working tree có FK nullable 0014, Core 381 passed; Production mock migrate/bootstrap và schema check đạt; #13 đóng theo xác nhận | Publish image source đồng bộ trước deploy tiếp; manifest riêng chỉ cần cho future backfill |
| [015 Security/integration](TASK-015-core-integration-security-tests.md) · G2 | CI đạt 380 PostgreSQL 16 tests/0 skip, gồm security/integration matrix. | QAS-05 client latency cần môi trường đo cô lập (#17); browser E2E #21 |
| [016 Compose/health/CI](TASK-016-core-docker-compose-ci.md) · G2 | Compose smoke, health, migration/head, Web build CI đạt; Staging và Core Production mock ready. | Vercel reauth/redeploy; full Web E2E #21 |
| [017 Document (Tổng hợp)](TASK-017-document-service-design-impl.md) · G3 | `P0/P1 IMPLEMENTED — VERIFIED` ([ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md), [Document DESIGN](../docs/architect/microservices-v1/document/DESIGN.md), [API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md)) | Đồng bộ AI events (`017F`) và P2 (`017I`) |
| ↳ [017A Core prereqs](TASK-017A-core-document-prereqs.md) · G3 | `IMPLEMENTED — VERIFIED` · `manage_class_documents` (`ClassAccessAction`) và `POST /internal/v1/auth/introspect` tại Core | Core G1/G2 |
| ↳ [017B Document foundation](TASK-017B-document-service-foundation.md) · G3 | `IMPLEMENTED — VERIFIED` · `services/document/`, schema `document`, `GET /documents/policy` (20 MB/300 trang), validation, grants, reader/download, notes/bookmarks, internal API | `017A` |
| ↳ [017C Document IDP worker](TASK-017C-document-idp-worker-and-review.md) · G3 | `IMPLEMENTED — VERIFIED` · `worker.py`, `services/rag/idp`, 5 trạng thái (`NEEDS_REVIEW`), chủ sở hữu `/accept`, `/retry`, outbox `document.version.published` | `017B` |
| ↳ [017D Web proxy & Document UI](TASK-017D-web-proxy-and-document-ui.md) · G3 | `IMPLEMENTED — VERIFIED` · Proxy đa upstream (`DOCUMENT_API_URL`, `arrayBuffer()`), `FormData`, 12 hàm port, decoder `SUCCESS→READY`, nút `/accept`, sync 20 MB/300 trang | `017B`, `017C` |
| ↳ [017E Compose demo](TASK-017E-compose-demo-and-deployment.md) · G3 | `IMPLEMENTED — VERIFIED` · Compose startup/health và smoke HTTP với tệp 10 MB, chặn `413` và cô lập lỗi `503` đã kiểm chứng | `017B`, `017C`, `017D` |
| ↳ [017F Đồng bộ sự kiện Doc↔AI](TASK-017F-doc-ai-event-and-report-sync.md) · G3 | `IN PROGRESS` · Hợp đồng event `document.version.published` & internal API `/canonical`, `/active-grants`, `/grant-invalidation` cho AI (`TASK-018`) | `ADR-002` |
| ↳ [017G Document CI & Compose smoke](TASK-017G-document-ci-and-compose-smoke.md) · G3 | `DONE — VERIFIED (P0/P1 Wave 1A)` · CI `core-ci.yml` (`document-tests`, `web-build`, `compose-smoke`); đã kiểm chứng cục bộ PG16 `99 passed` (`94.19%`), Ruff `53` files, Compose + DB/service outage | `017E`, [22_ENVIRONMENTS_DEPLOYMENT.md](../docs/architect/athena-design/22_ENVIRONMENTS_DEPLOYMENT.md) |
| ↳ [017H Compose full E2E & Runbook](TASK-017H-document-compose-e2e-and-demo-runbook.md) · G3 | `DONE — VERIFIED (P0/P1 Wave 1B)` · `verify_compose_e2e.py` đạt **`13/13 PASS`** qua Web Proxy (5 định dạng, `NEEDS_REVIEW -> /accept`, H3, `B6`, `H6/L6/O2`, `QAS-10`, p95 `32.4–40.0 ms`) + `DEMO_RUNBOOK.md` | `017E`, [DEMO_RUNBOOK.md](../docs/architect/microservices-v1/document/DEMO_RUNBOOK.md) |
| ↳ [017I Document P2 Cloud & Governance](TASK-017I-document-p2-cloud-and-governance.md) · G3 | `DEFERRED (P2 Wave 2)` · GCS signed upload, malware scan, storage quota, retention (`OD-02`/`OD-03`), notify khi share *(chờ Core API)*, Cloud Run | Sau bảo vệ; blocked by `OD-02`/`OD-03` & Core |
| [018 AI Tutor](TASK-018-ai-service-rag-tutor.md) · G3 | Thiết kế RAG/2 chế độ/chat privacy; đồng bộ `ADR-002` (`introspect`, `document.version.published`, `active-grants`, `canonical`, bundle `complete: true`), đo QAS-06 | Core G2, 008, `017A–017F`; D6 phối hợp 019 |
| [019 Learning](TASK-019-learning-service.md) · G4 | Thiết kế PRIMARY approval, immutable attempt, transfer cutoff, grade version/deadline events | G3 + 006/008/011/012/018 contracts |
| [020 Learner Intelligence/reporting](TASK-020-learner-intelligence-service.md) · G5 | Thiết kế evidence/version, suppression nhóm nhỏ, gợi ý hỗ trợ, báo cáo PDF/Excel | 013/017–019 + Core access |

Phụ thuộc gồm **hợp đồng cần chốt trước** và **triển khai cần tích hợp sau**, không buộc xong tuần tự mọi task, chốt policy 008 cùng ERD 005–007 để làm mock/contract song song; mock không thay bằng chứng nghiệm thu thật.

## 4. Thứ tự sửa và quyết định còn mở

1. School mapping #13: xác nhận mapping với trường thật hoàn tất và issue đóng. Không lưu manifest hoặc dữ liệu cá nhân trong repo; FK nullable 0014 đã migrate vào Production mock, hiện chưa có legacy user rows. Backfill khi có tài khoản thực vẫn cần manifest được kiểm soát và report/plan/apply.
2. Limiter/idempotency hiện giữ một worker theo quyết định MVP; chỉ mở rộng khi có scale target cụ thể.
3. SMTP invitation/recovery/retry đã được xác nhận trên staging (#16 đóng). ACCESS/EXPORT/CORRECTION dùng quy trình Admin thủ công đã duyệt; DELETE giữ `IN_REVIEW` đến khi chốt retention và dọn dữ liệu xuyên service (#15).
4. Document P0/P1 (`TASK-017A..E`, Wave 1A `TASK-017G`, Wave 1B `TASK-017H`) đã triển khai và kiểm chứng thực tế trên PostgreSQL 16 (`99 passed`, `94.19%`) + Docker Compose live (`verify_compose_e2e.py` `13/13 PASS`); P2 cloud/retention gom ở `TASK-017I`, và `TASK-017F` theo dõi đồng bộ sự kiện Document↔AI. AI (`TASK-018`) và Learning (`TASK-019`) đã triển khai (`compose.full.yaml` & Web live), còn mở gate `QAS-06` (`>= 100` câu GGUF thật), `QAS-03` (`1,000` concurrency) và full `TP-01`; `Learner Intelligence` (`TASK-020`) vẫn ở trạng thái `PROPOSED`.
5. Đã có benchmark Core subset trên máy CKEY cô lập; full TP-01/user-facing SLO và full 4-service browser E2E vẫn là gate nghiệm thu riêng.

## 5. Điều kiện đóng task

- [ ] Rule ID, module owner và contract nhất quán; chốt PENDING DESIGN trước triển khai phụ thuộc.
- [ ] Code/API/schema thật đáp ứng tiêu chí; không import private module hoặc SQL xuyên service.
- [ ] Backend kiểm tra role/status/school/resource hiện hành; lịch sử cá nhân và source grant có test riêng.
- [ ] Migration mới/test/Web cần thiết đi cùng feature; không sửa migration đã áp dụng hoặc lấy demo local thay Web chính thức.
- [ ] Ghi command, môi trường/revision và kết quả thực chạy; skip/mock/lịch sử không thay bằng chứng mới.
- [x] Shared/API/permission changes đã self-review theo contract, quyền hiện hành và CI. Không commit secret, mã một lần hoặc nội dung riêng tư.
