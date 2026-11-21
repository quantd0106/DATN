# TASK-017D: Web Proxy đa Upstream (Binary Body) & Live Document UI

- **Mã Task:** `TASK-017D`
- **Giai đoạn:** `G3 — Document + AI`
- **Trạng thái:** `IMPLEMENTED — VERIFIED` (phụ thuộc [TASK-017B](TASK-017B-document-service-foundation.md) và [TASK-017C](TASK-017C-document-idp-worker-and-review.md))
- **Module sở hữu:** `apps/web/`
- **Giải quyết vấn đề:** `#1` (decoder map `SUCCESS → READY`, `MARKDOWN → MD`), `#2` (hiển thị `NEEDS_REVIEW` + nút "Chấp nhận"), `#5` (Web proxy định tuyến `DOCUMENT_API_URL` & binary body `arrayBuffer()`), `#8` (đồng bộ policy 20 MB / 300 trang từ `GET /documents/policy`)
- **Business Rules liên quan:** `B1–B7`, `H3`, `H6`, `L6`, `O1`, `O2`, `G5`
- **Tài liệu căn cứ:**
  - [ADR-002](../docs/architect/microservices-v1/ADR-002-document-service.md) (Quyết định 1, 2, 5, 8)
  - [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md#4-hợp-đồng-web-proxy-allowlist-appswebsrclibapioperationsts--proxyts)
  - [Boundary Policy](../apps/web/scripts/boundary-policy.mjs)

---

## 1. Mục tiêu

Nâng cấp tầng proxy `/api/v1/*` của Next.js để hỗ trợ định tuyến đa upstream (`CORE_API_URL` và `DOCUMENT_API_URL`) và chuyển tiếp dữ liệu nhị phân `multipart/form-data` không bị hỏng byte; đồng thời hoàn thiện bộ chuyển đổi dữ liệu live Document (`live-document.ts`), ánh xạ trạng thái `SUCCESS → READY`, bổ sung nút **"Chấp nhận"** cho phiên bản `NEEDS_REVIEW` và đồng bộ chính sách `20 MB / 300 trang`.

---

## 2. Chi tiết kỹ thuật & danh sách tệp cần sửa

### 2.1. Next.js API Proxy & HTTP Transport (`apps/web/`)
- **`apps/web/src/lib/api/operations.ts`:**
  - Bổ sung trường `upstream: 'core' | 'document'` (hoặc map `operationUpstream`) cho mỗi operation. Tất cả operation hiện có thuộc `'core'`.
  - Bổ sung đủ 20 operation Document theo bảng tại [Document API Contract](../docs/architect/microservices-v1/document/API_CONTRACT.md#4-hợp-đồng-web-proxy-allowlist-appswebsrclibapioperationsts--proxyts), giữ nguyên cơ chế kiểm tra UUID cho mọi path param (`documentId`, `versionId`, `classId`, `noteId`) và `operationQueryParameters` allowlist.
  - Hỗ trợ `successStatuses` (hoặc giữ `successStatus` + cho phép `[200, 201]` với `grantClassDocument`).
- **`apps/web/src/lib/api/proxy.ts` & `apps/web/app/api/v1/[...path]/route.ts`:**
  - Thay tham số đơn `getOrigin` bằng resolver đa upstream (`{ core: () => process.env.CORE_API_URL, document: () => process.env.DOCUMENT_API_URL }`, vẫn giữ overload hoặc adapter tương thích test cũ nếu cần).
  - Sau khi khớp `operation` trong allowlist, tra đúng upstream (`core` hoặc `document`). **Quan trọng:** các route như `/classes/:classId/documents*`, `/classes/:classId/notes*`, `/classes/:classId/bookmarks/toggle` phải đi tới `DOCUMENT_API_URL`, không gửi sang `CORE_API_URL`.
  - Cô lập lỗi cấu hình: nếu thiếu `DOCUMENT_API_URL`, chỉ các request `upstream === 'document'` trả `503 CONFIGURATION_ERROR`; request Core vẫn hoạt động bình thường.
  - **Sửa hỏng file nhị phân:** thay `await request.text()` bằng **`await request.arrayBuffer()`** (chỉ đọc khi `method` không phải `GET`/`HEAD` và body khác rỗng). Giữ nguyên header `Content-Type` (kèm `boundary` của `multipart/form-data`), không gán cứng `Accept: application/json` khi gọi `classDocumentDownload` (cho phép `*/*` hoặc theo request).
  - Forward response headers cho file download (`content-disposition`, `content-length`, `content-type`, `x-correlation-id`, `retry-after`, `x-token-delivery-status`) và luôn chặn `X-Service-Key` từ trình duyệt.
  - Tăng timeout upstream cho các thao tác upload/download tệp (ví dụ `60_000 ms`, giữ `15_000 ms` cho các request khác).
- **`apps/web/src/lib/api/http.ts`:**
  - Nếu `options.body instanceof FormData`, **không** gán `Content-Type: application/json` (để trình duyệt tự gắn `multipart/form-data; boundary=...`) và **không** gọi `JSON.stringify(options.body)`.
  - Bổ sung hàm/chế độ tải nhị phân (ví dụ `requestBlob` hoặc `rawResponse`) trong `http.ts` để tuân thủ [boundary-policy.mjs](../apps/web/scripts/boundary-policy.mjs) (chỉ `http.ts` và `proxy.ts` được gọi `fetch`).

### 2.2. Domain Types, Upload Validation & Mock Sync
- **`apps/web/src/lib/domain.ts`:**
  - Mở rộng `ProcessingStatus`: `'READY' | 'PROCESSING' | 'PENDING' | 'NEEDS_REVIEW' | 'FAILED'`.
  - Mở rộng `DocumentVersion`: thêm `id?: string`, `isSuperseded?: boolean`, `reviewSummary?: { reviewItemCount: number; flagCodes: string[]; flaggedPages: number[] }`.
  - Mở rộng `DocumentCapabilityPolicy`: thêm `maxPages?: number`.
- **`apps/web/src/lib/document-versions.ts`:**
  - Giữ `serving = ordered.find(v => v.status === 'READY') ?? null` (vì `SUCCESS` đã được map thành `'READY'`). Bảo đảm khi `latest` có trạng thái `'NEEDS_REVIEW'`, `'PENDING'` hoặc `'PROCESSING'`, `serving` vẫn giữ bản `'READY'` cao nhất trước đó (Rule H3).
- **`apps/web/src/lib/document-upload.ts`:**
  - Kiểm tra thêm phần thông báo/giới hạn `maxPages` (300 trang) nếu hiển thị ở UI gợi ý tệp.
- **`apps/web/dev/mocks/fixtures.ts` & `apps/web/dev/mocks/adapter.ts`:**
  - Cập nhật `documentCapabilityPolicy` từ `50 MB` (`52428800`) thành **`20 MB` (`20971520`)** và `maxPages: 300`, sửa thông báo lỗi `50MB` cứng trong `dev/mocks/adapter.ts` thành đọc động từ `documentCapabilityPolicy.maxSizeBytes`.
  - Kiểm tra `apps/web/tests/document-validation.test.ts` (đang test với `60*1024*1024`) vẫn nhất quán.

### 2.3. Live Adapter (`live-document.ts`, `port.ts`, `live.ts`, `integrations.ts`, `route-availability.ts`)
- **`apps/web/src/lib/data/port.ts`:**
  - Cập nhật `uploadDocument` và `uploadVersion` nhận `file: File` thật (hoặc `UploadInput & { file: File }`) thay vì chỉ `{ name, size, type }`.
  - Bổ sung `acceptVersion(documentId: string, versionId: string): Promise<DocumentVersion>` và `downloadClassDocument(classId: string, documentId: string, version?: number): Promise<void>` (hoặc trả blob/filename) vào `DataAdapter['document']`.
- **`apps/web/src/lib/data/live-document.ts` (tạo mới):**
  - Decoder ánh xạ chuẩn:
    - `processing_status === 'SUCCESS'` → `'READY'`
    - `format === 'MARKDOWN'` → `'MD'`
    - ánh xạ `is_superseded`, `id` (version UUID), `review_summary`, `is_source_accessible`, `source_quote` (null khi bị che).
  - Triển khai các hàm `workspace.load` cho các route `t.library`, `t.libraryDocument`, `t.documents`, `s.documents`, `s.reader`, `s.notes` (kết hợp gọi Core `classes`/`classDetail` khi cần `classSummary` và `shareableClasses`).
- **`apps/web/src/lib/data/live.ts`, `live-core.ts`, `integrations.ts`, `route-availability.ts`:**
  - Nối `live-document.ts` vào `live.ts` và ủy quyền load các route Document ở `live-core.ts`.
  - Cập nhật `integrations.ts`: `document: { status: 'contracted', owner: 'Document' }`.
  - Cập nhật `route-availability.ts`: bỏ `'s.notes'`, `'t.library'`, `'s.documents'`, `'t.documents'` khỏi `liveUnavailable` (và cập nhật `apps/web/tests/route-availability.test.ts`).

### 2.4. UI Components & Views (`apps/web/src/features/document/`)
- **`apps/web/src/features/document/components/source-management-ui.tsx` & `.css`:**
  - Thêm nhãn trạng thái `NEEDS_REVIEW` (`"Cần rà soát"`, badge màu hổ phách) vào `PROCESSING_STATUS_LABELS`.
  - Trong `SourceVersionHistory`: thêm prop `canManageSource?: boolean` và `onAcceptVersion?(versionId: string): void`. Khi `v.status === 'NEEDS_REVIEW'` và `canManageSource` là `true` và có `v.id`, hiển thị **nút nhỏ "Chấp nhận"** (kèm `title`/gợi ý số trang bị gắn cờ nếu có, ví dụ "Duyệt phiên bản này để phục vụ lớp"). Không hiện nút này cho người không phải chủ sở hữu.
  - Nếu `v.isSuperseded` hoặc `v.status === 'READY' && !v.isServing`, tiếp tục hiển thị `"Bản lưu"`.
- **`apps/web/src/features/document/views/teacher-library-view.tsx` & `teacher-library-document-view.tsx`:**
  - Truyền `file: selectedFile` thật (không chỉ metadata `{name, size, type}`) vào `adapter.document.uploadDocument` và `adapter.document.uploadVersion`.
  - Thêm option `<option value="NEEDS_REVIEW">Cần rà soát</option>` trong bộ lọc trạng thái thư viện; hiển thị badge `NEEDS_REVIEW` trong bảng tài liệu.
  - Hiển thị gợi ý `Tối đa 20 MB · 300 trang` từ `capabilityPolicy` (`GET /documents/policy`).
  - Nối handler `onAcceptVersion` trong `TeacherLibraryDocumentView` gọi `adapter.document.acceptVersion(doc.id, versionId)` rồi `reload()`.

---

## 3. Tiêu chí nghiệm thu & Kiểm thử bắt buộc

- [x] **Binary Proxy Test (`tests/integration-boundary.test.ts`):** gửi payload `multipart/form-data` chứa byte nhị phân không phải UTF-8 (ví dụ PDF header + bytes `0x80 0xFF 0x00`) qua `proxy` → kiểm chứng từng byte nhận ở upstream khớp 100% với input, `Content-Type` giữ nguyên `boundary`.
- [x] **Multi-upstream Routing Test:** kiểm chứng `/documents/*`, `/classes/:id/documents/*`, `/classes/:id/notes*`, `/notes/*`, `/users/me/notes` đi tới `DOCUMENT_API_URL`, còn `/classes/:id/posts` đi tới `CORE_API_URL`. Thiếu `DOCUMENT_API_URL` chỉ trả `503` cho Document routes, không ảnh hưởng Core routes.
- [x] **Decoder & Version State Test (`tests/live-document.test.ts`, `tests/document-validation.test.ts`):** decoder ánh xạ `SUCCESS → READY`, `MARKDOWN → MD` và giữ trạng thái `NEEDS_REVIEW`. Test trạng thái phiên bản dùng adapter mock xác nhận v2 `NEEDS_REVIEW` giữ v1 `READY` làm `serving` đến khi chủ sở hữu duyệt.
- [x] **Boundary & Live Build Gate:** không vi phạm `boundary-policy.mjs` (chỉ `http.ts` & `proxy.ts` gọi `fetch`), không rò fixture trong production build (`NEXT_PUBLIC_DATA_MODE=live`).
- [x] Live HTTP smoke qua Web proxy với Core + Document containers, gồm upload PDF 10 MB, xử lý, cấp grant, Student đọc/tải và cô lập lỗi khi dừng Document.
- [ ] Manual browser E2E qua proxy với Core + Document containers đang chạy.

### Lệnh kiểm chứng (chạy từ `apps/web`)

**Bằng chứng kiểm định (2026-11-21)** · Windows 11 · Python 3.12.5 · Node 24.15.0

- `npm run check` → `check:boundaries` đạt, `tsc --noEmit` có 0 lỗi, **277/277 tests passed** (exit 0).
- `NEXT_PUBLIC_DATA_MODE=live npm run build` → biên dịch thành công, **32/32** trang, `check:release` đạt (`Release output contains no preview fixture marker.`), exit 0.
- Docker Desktop 29.7.2 (linux) · PostgreSQL 16. Tệp PDF `10,500,004` byte tải qua `POST http://127.0.0.1:3000/api/v1/documents` → `201 PENDING` → `document-worker` xử lý đến `SUCCESS`. `POST /api/v1/classes/{id}/documents/grants` trả `201`. Student gọi `GET .../reader` và `GET .../download` đều nhận `200`, bản tải có `10,500,004` byte.
- Tệp `20,971,521` byte (>20 MB) bị từ chối `413`. Sau `docker compose stop document`, `GET http://127.0.0.1:3000/api/v1/classes` trả `200` và `GET http://127.0.0.1:3000/api/v1/documents/policy` trả `503`. Xem [TASK-017E](TASK-017E-compose-demo-and-deployment.md).

Luồng Web được kiểm chứng bằng unit tests, live build và request HTTP trực tiếp tới `web:3000`. Kiểm thử thao tác thủ công trong trình duyệt còn mở.
