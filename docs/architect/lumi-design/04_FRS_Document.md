# FRS-DOCUMENT — THƯ VIỆN TÀI LIỆU, VERSION, GRANT, NOTES

**Mã:** LUMI-FRS-DOC-001 · **Trạng thái:** **P0/P1 IMPLEMENTED — VERIFIED** (backend, worker, Web UI và Compose demo theo [ADR-002](../microservices-v1/ADR-002-document-service.md), [Document DESIGN](../microservices-v1/document/DESIGN.md), [Document API Contract](../microservices-v1/document/API_CONTRACT.md) và [TASK-017](../../../tasks/TASK-017-document-service-design-impl.md); CI/E2E hoàn tất thêm ở `TASK-017G`/`H`; P2 mở rộng ở `TASK-017I`).

## 1. Mục tiêu và nguồn quy tắc

Service Document sở hữu tệp nguồn, phiên bản, chia sẻ theo lớp và ghi chú/bookmark gắn nguồn. Source of truth: B1–B7, G5, H3, H6, L6, O1, O2. Không đọc SQL Core; cần xác minh danh tính người dùng (`POST /internal/v1/auth/introspect`) và current action/class scope qua interface dịch vụ được phê duyệt. Không mô tả hay thiết kế pipeline lập chỉ mục AI.

## 2. FRS-D01 — Thư viện cá nhân

- **Actor:** Lecturer có tài khoản ACTIVE.
- **Điểm vào:** `/teacher/library` (UI + Web binary proxy live tới `DOCUMENT_API_URL` đã triển khai theo `TASK-017D`).
- **List:** tài liệu do bản thân sở hữu, tên, loại, version thành công hiện hành, trạng thái xử lý, lớp được chia sẻ; chỉ metadata được phép.
- **Create:** metadata tối thiểu, lựa chọn tệp. Server xác minh owner từ credential, không nhận owner_user_id từ form để phân quyền.
- **Edit/Delete:** chỉ owner nguồn; trước xóa nếu có nhiều class grants đang dùng phải hiển thị danh sách ảnh hưởng và xác nhận riêng (`confirm_multi_class=true`).
- **Errors:** 401/403/404, 409 (xác nhận đa lớp), 413 (`FILE_TOO_LARGE` > 20 MB), validation upload (422), storage unavailable; không xóa từ UI trước khi backend xác nhận.
- **AC:** DOC-T01, DOC-T02.

## 3. FRS-D02 — Upload và xử lý phiên bản

- **Format được chốt:** PDF, PPTX, DOCX, TXT, Markdown.
- **Chính sách & điều kiện trước upload (P1 theo ADR-002):** owner source; loại file hợp lệ qua kiểm tra magic-byte và cấu trúc ZIP/PDF; giới hạn **20 MB (`20_971_520` bytes)** và **tối đa 300 trang** (PDF/slide PPTX), công bố thống nhất qua `GET /api/v1/documents/policy`; từ chối PDF mã hóa và chống ZIP-bomb (vẫn không lấy mức mẫu 10 MB của TP-01 làm giới hạn trần).
- **Luồng và 5 trạng thái máy chủ:** nhận blob → lưu bền vững → ghi version `PENDING` → worker IDP chạy `PROCESSING` → `SUCCESS` (tự động đạt ngưỡng tin cậy và đã lưu Canonical) hoặc `NEEDS_REVIEW` (ví dụ fast OCR hoặc công thức/bảng DOCX cần rà soát) hoặc `FAILED` → chỉ `SUCCESS` đủ điều kiện phục vụ lớp.
- **Lối thoát `NEEDS_REVIEW`:** chỉ **chủ sở hữu tài liệu nguồn** được phép thực hiện thao tác chấp nhận (`POST .../accept`) để chuyển `NEEDS_REVIEW → SUCCESS` (trên Web UI có nút nhỏ **"Chấp nhận"** trong lịch sử phiên bản).
- **Ranh giới Canonical và lập chỉ mục:** `SUCCESS` nghĩa là tài liệu Canonical đã được trích xuất và lưu bền vững, đồng thời Document phát sự kiện transactional outbox `document.version.published` (việc lập chỉ mục thuộc phạm vi dịch vụ ngoài Document).
- **Trạng thái hiển thị và fallback (Rule H3):** Không lưu trạng thái `SUPERSEDED` trong DB; phiên bản `SUCCESS` có số hiệu lớn nhất là bản đang phục vụ (`is_serving`), các bản `SUCCESS` cũ hơn mang cờ dẫn xuất `is_superseded` ("Bản lưu"). Trên giao diện Web live, `SUCCESS` được giải mã thành `READY`. Worker crash, timeout, upload v2 lỗi hoặc v2 đang ở `NEEDS_REVIEW` đều giữ nguyên v1 `SUCCESS` đang phục vụ; báo lỗi có thể khắc phục, không lộ stack trace.
- **AC:** DOC-T03–DOC-T05, QAS-10.

## 4. FRS-D03 — Chia sẻ tài liệu vào lớp

- **Actor:** PRIMARY/ASSISTING hiện hành của lớp, với tài liệu nguồn thuộc quyền hợp lệ.
- **Điều kiện:** xác minh Core action `manage_class_documents` (lớp `ACTIVE`, cùng `school_id`, actor còn `TeachingAssignment` hiện hành); document ownership hoặc quyền chia sẻ theo policy; không thể sửa/xóa/chấp nhận phiên bản nguồn của người khác. Không dùng `manage_class_calendar` thay cho `manage_class_documents`.
- **Tạo grant:** idempotent/unique theo class/document (chỉ khi tài liệu có bản `SUCCESS`); audit ai chia sẻ và thời điểm. Có thể share một nguồn nhiều lớp.
- **Revoke:** tác động ngay tại lần kiểm tra quyền sau; không dùng permission snapshot hoặc cache stale để mở tài liệu.
- **Lỗi:** class archived (409); actor mất assignment (403); cross-school (404); grant đã thu hồi; storage failure.
- **AC:** DOC-T06–DOC-T08.

## 5. FRS-D04 — Xem/tải tài liệu

- **Student:** chỉ xem/tải khi membership/action hợp lệ **và** Document grant hiện hành.
- **Teacher:** không mặc định mở source của giáo viên khác vì cùng lớp; điều kiện share phải được xác minh đúng action.
- **Lần kiểm tra:** trước cung cấp URL/tệp/preview, xác minh cả class access lẫn grant; URL tải có thời hạn và chính sách thu hồi cần được quyết định trước production.
- **Không lấy metadata môn/khối làm bằng chứng quyền.**
- **AC:** DOC-T09, DOC-T10.

## 6. FRS-D05 — Notes và bookmarks cá nhân

- **Actor:** owner ghi chú/bookmark.
- **Chức năng:** thêm, xem, sửa, xóa ghi chú tự viết; bookmark vị trí trang/slide/mục nếu xác định được; tìm trong tài liệu cá nhân theo quyền.
- **Khi mất quyền nguồn:** giữ chữ tự viết; che nguyên văn quote và chặn mở nguồn/bản preview; không tái cấp grant qua bookmark.
- **Lớp archived:** owner vẫn có thể sửa notes/bookmark cá nhân nếu nguồn còn quyền (O2), không tạo hoạt động chung mới.
- **AC:** DOC-T11–DOC-T13.

## 7. Ma trận dữ liệu và quyền

| Tài nguyên | Đọc | Ghi/sửa | Xóa/thu hồi |
|---|---|---|---|
| Source document | Owner, authorized reader theo grant | Source owner | Source owner với confirm multi-class |
| DocumentVersion | Reader được phép với successful version | Source owner upload; worker process | Policy lifecycle cần chốt |
| ClassGrant | Thành viên/teacher được phép xem trạng thái | Assigned Lecturer theo rule | Assigned Lecturer theo rule |
| SourceBoundNote | Chủ sở hữu note | Chủ sở hữu note | Chủ sở hữu note |
| Bookmark | Chủ sở hữu bookmark | Chủ sở hữu bookmark | Chủ sở hữu bookmark |

## 8. Các quyết định P1 đã chốt (ADR-002) và phần còn mở cho P2

- **Đã triển khai & kiểm chứng P0/P1 ([ADR-002](../microservices-v1/ADR-002-document-service.md), [TASK-017](../../../tasks/TASK-017-document-service-design-impl.md)):** CI/Compose full E2E hoàn thiện tiếp tại [TASK-017G](../../../tasks/TASK-017G-document-ci-and-compose-smoke.md) và [TASK-017H](../../../tasks/TASK-017H-document-compose-e2e-and-demo-runbook.md); phần **P2** theo dõi tại [TASK-017I](../../../tasks/TASK-017I-document-p2-cloud-and-governance.md):
  - Trạng thái máy chủ (`PENDING`, `PROCESSING`, `SUCCESS`, `NEEDS_REVIEW`, `FAILED`), `SUPERSEDED` là dẫn xuất lúc đọc (không lưu DB để giữ H3), Web ánh xạ `SUCCESS → READY` và `MARKDOWN → MD`.
  - Chủ sở hữu chấp nhận thủ công (`POST .../accept`) cho `NEEDS_REVIEW → SUCCESS` (kèm nút nhỏ trên Web).
  - Xác thực người dùng liên service qua Core `POST /internal/v1/auth/introspect` (`X-Service-Key`, fail closed, không chia sẻ `SECRET_KEY`); kiểm tra chia sẻ/thu hồi qua Core action `manage_class_documents`.
  - Hạn mức upload P1: **20 MB (`20_971_520` bytes)**, **tối đa 300 trang**, trả về qua `GET /api/v1/documents/policy`; upload multipart trực tiếp qua Next.js proxy (đọc body nhị phân bằng `arrayBuffer()`) trong môi trường **Docker Compose** để demo bảo vệ.
  - Ranh giới Canonical: `SUCCESS` đồng nghĩa Canonical artifact đã trích xuất và lưu bền vững, phát sự kiện `document.version.published` (lập chỉ mục nằm ngoài Document).
- **Còn mở cho P2 / vận hành production thật:**
  - Upload trực tiếp qua signed URL lên GCS (để vượt trần kích thước body của Vercel Functions khi chạy cloud).
  - Encryption at rest key management, quét mã độc chuyên sâu (malware scanning), và chính sách lưu giữ pháp lý (retention) trước xóa vĩnh viễn tệp/phiên bản.

Chi tiết cấu trúc dữ liệu và hợp đồng API nằm tại [Document DESIGN](../microservices-v1/document/DESIGN.md) và [Document API Contract](../microservices-v1/document/API_CONTRACT.md).