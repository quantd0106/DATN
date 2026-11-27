# 13. UI/UX Specification (`apps/web`)

**Trạng thái:** `IMPLEMENTED` · **Cập nhật:** 2026-11-22
**Tài liệu chi tiết tại Web workspace:** [apps/web/docs/UI_UX_DESIGN.md](../../../apps/web/docs/UI_UX_DESIGN.md) · [apps/web/docs/FRONTEND.md](../../../apps/web/docs/FRONTEND.md)

---

## 1. Triết lý & Kiến trúc Frontend hiện tại

- **Ngăn xếp:** Next.js 15 (`15.5.12`), React 19, TypeScript, CSS custom tokens (`apps/web/src/styles/lumi-foundation.css`) + **20 tệp CSS scoped** theo domain trong `apps/web/src/features/**`.
- **Đối tượng phục vụ:** Học sinh THCS & THPT (khối 6–12), Giáo viên (`LECTURER`), Quản trị viên trường (`ADMIN`) trong lớp học chính khóa — giao diện sáng, tập trung học tập, không game hóa (không streak, huy hiệu, bảng xếp hạng).
- **Quy mô giao diện:** **71 product routes** được định nghĩa trong `apps/web/src/config/routes.ts` (cùng `app/page.tsx` điều hướng theo phiên đăng nhập, tổng 72 `page.tsx`), kết nối backend thông qua 3 lớp BFF proxy server-only trong `apps/web/src/app/api/`:
  - `/api/v1/*` -> Core (`CORE_API_URL`) & Document (`DOCUMENT_API_URL`)
  - `/api/ai/v1/*` -> AI Service (`AI_API_URL`, 15 thao tác Tutor & Quiz)
  - `/api/learning/v1/*` -> Learning Service (`LEARNING_API_URL`, 57 thao tác Practice/Assessment/Assignment/Grading/Appeals/Bank)
- **Nguyên tắc vận hành UI:**
  - **No fake success:** Chỉ báo thành công sau ACK/receipt từ backend; giữ nguyên ngữ nghĩa `401`/`403`/`404`/`409`/`422`/`503`.
  - **Fail closed & cô lập lỗi:** Chế độ `live` không bao giờ rơi về dữ liệu mock; lỗi dịch vụ con (`503`) trên Widget dashboard được cô lập bằng `optionalServiceRead` để không làm sập shell hoặc màn hình Core, riêng `401` buộc xác thực lại ngay.

---

## 2. Design Tokens (`src/styles/lumi-foundation.css`)

| Nhóm | Token tiêu chuẩn | Giá trị mặc định | Sử dụng chính |
|---|---|---|---|
| **Primary** | `--primary`, `--primary-hover`, `--primary-tint`, `--primary-border` | `#2563eb`, `#1d4ed8`, `#eff6ff`, `#bfdbfe` | Nút hành động chính, link, active tab, `--focus-ring` |
| **AI Accent (Athena)** | `--ai`, `--ai-tint`, `--ai-border` | `#0d9488`, `#f0fdfa`, `#99f6e4` | Khối **Hỏi Athena**, panel trợ lý, gợi ý chấm điểm & giải thích lỗi |
| **Surfaces & Text** | `--ink`, `--ink-secondary`, `--muted`, `--bg`, `--surface`, `--surface-subtle`, `--border` | `#0f172a`, `#334155`, `#5b6b82`, `#f8fafc`, `#ffffff`, `#f1f5f9`, `#e2e8f0` | Nền trang dịu, card trắng rõ cấu trúc |
| **Semantic Status** | `--success`, `--warning`, `--danger` (+ `-tint`, `-border`) | `#166534`, `#92400e`, `#b91c1c` | Trạng thái phiên bản tài liệu, điểm đã công bố, cảnh báo nộp muộn/conflict |
| **Shell Geometry** | `--sidebar`, `--topbar`, `--radius-sm..xl` | `260px` (`224px` tablet), `64px`, `6px..16px` | Sidebar cố định trái, Topbar cố định trên, Drawer mobile `<dialog>` |
| **Touch & Typography** | `font-family`, `min-height` | `Inter, system-ui, sans-serif`, `button`/`input >= 44px` | Tuân thủ chạm thân thiện di động & focus keyboard rõ (`3px`) |

---

## 3. Cấu trúc 7 Layouts & Điều hướng theo Vai trò

| Layout | Số route | Phạm vi áp dụng |
|---|---:|---|
| `AuthLayout` | 5 | `/login`, `/activate`, `/recover`, `/reset-password`, `/staff/invitation` |
| `AppLayout` | 30 | Trang chủ theo vai trò, danh sách lớp, Thư viện giảng viên, Ghi chú cá nhân, Lịch tổng hợp, Thông báo, Hồ sơ, Yêu cầu dữ liệu, khu vực Quản trị |
| `StudentClassLayout` | 13 | Workspace lớp học sinh với `ClassHeader` và các tab: `Tổng quan`, `Bảng tin`, `Tài liệu`, `Hỏi Athena`, `Luyện tập`, `Kiểm tra`, `Bài tập`, `Lịch học` *(tab `Tiến độ` ẩn trên live)* |
| `TeacherClassLayout` | 12 | Workspace lớp giáo viên với `ClassHeader` (nhãn `Giáo viên chính`/`Giáo viên phối hợp`/`Đã lưu trữ`) và các tab: `Tổng quan`, `Bảng tin`, `Tài liệu`, `Hỏi Athena`, `Hoạt động học tập`, `Lịch học` *(tab `Thống kê` ẩn trên live)* |
| `ReaderLayout` | 1 | `s.reader` — Trình đọc tài liệu Canonical nhiều trang/slide kèm bảng ghi chú & dấu trang |
| `FocusedTaskLayout` | 8 | Soạn/làm luyện tập, làm bài kiểm tra chính thức (`s.examAttempt`), biên nhận (`s.receipt`), chấm bài tập/kiểm tra (`t.assignmentGrading`, `t.examGrading`), phúc khảo & duyệt sửa điểm (`t.correction`, `t.correctionApproval`) |
| `HistoryLayout` | 2 | `s.history`, `s.historyResult` — Lịch sử cá nhân cho lớp đã rời/chuyển hoặc lưu trữ, tách khỏi điều hướng lớp mới |

---

## 4. Đặc tả các Màn hình Trọng yếu & Luồng Cross-Service

### 4.1. Identity, Social & School Administration (`Core`)
- **Auth & Onboarding:** Học sinh dùng `username` + mã kích hoạt/khôi phục một lần từ Admin (`/activate`, `/reset-password`); Nhân sự dùng `email` + token fragment tự điền và xóa khỏi URL history (`/staff/invitation`).
- **Admin Workspace (`/admin/*`):** Tìm kiếm tài khoản debounce 200ms, sort tự nhiên, cập nhật role/status kèm `expected_revision` + `X-Admin-Reason`, import học sinh CSV/XLSX (`read-excel-file`) có preview/confirm/reconcile timeout, quản lý danh mục khối 6–12, lớp hành chính & lớp môn học (`AUTO`/`MANUAL` preview tác động), audit log.
- **Lớp học & Lịch:** Lớp `ARCHIVED` tự chuyển UI sang read-only (hiện banner trạng thái). Lịch `/calendar` và `/classes/:id/calendar` hiển thị hợp nhất sự kiện `CORE` và deadline `LEARNING` (chỉ sự kiện `CORE` mới cho sửa/xóa).

### 4.2. Tài liệu & Ghi chú (`Document`)
- **`UI-D01` Thư viện & Phiên bản (`/teacher/library`, `/teacher/library/:documentId`):** Tải lên 5 định dạng (`PDF/PPTX/DOCX/TXT/MARKDOWN`) đối chiếu giới hạn động từ `GET /documents/policy` (`20 MB`, `300` trang). Hiển thị vòng đời xử lý (`Đang xử lý`, `Sẵn sàng`, `Cần rà soát`, `Thất bại`), nút **Chấp nhận** phiên bản `NEEDS_REVIEW`, nút **Thử lại** khi `FAILED`, cảnh báo xóa đa lớp liệt kê đúng các lớp đang có grant (`B6`).
- **`UI-D02` Quản lý chia sẻ lớp (`/teacher/classes/:classId/documents`):** Liệt kê tài liệu chia sẻ trong lớp, badge **"Của bạn"** đối với chủ nguồn, thêm/gỡ grant hiệu lực ngay.
- **`UI-D03` Trình đọc Canonical & Ghi chú (`/student/classes/:classId/documents/:documentId`, `/student/notes`):** Đọc từng trang/slide từ Canonical JSON, gắn ghi chú cá nhân và dấu trang theo vị trí/phiên bản, tải tệp gốc. Khi mất grant hoặc tài liệu xóa mềm (`H6/L6`), UI che nội dung nguồn và `source_quote`, nhưng vẫn cho học sinh xem/sửa/xóa ghi chú cá nhân (kể cả trên lớp `ARCHIVED`, `O2`).

### 4.3. Trợ lý AI Tutor & Sinh câu hỏi (`AI` + `RAG`)
- **`UI-AI01` Cấu hình Tutor lớp (`/teacher/classes/:classId/tutor`):** Bật/tắt kiến thức tổng quát, cho phép/chặn tiết lộ lời giải, mức dẫn dắt (`GUIDED` / `STRICT`), số gợi ý trước khi hé lộ, độ dài tối đa, đồng bộ khóa lạc quan `expectedVersion` (`409 Conflict`).
- **`UI-AI02` Khung Hỏi Athena (`/student/classes/:classId/tutor`):** Streaming SSE theo hai chế độ (`QUICK_QA` và `STEP_BY_STEP` với `HINT` / `CHECK` / `REVEAL`), nút **Dừng phản hồi** (`/stop`), danh sách trích dẫn deep-link sang trang tài liệu tại trình đọc, cảnh báo và chặn hỏi mới khi đang có ca thi chính thức mở (`D6`).
- **Sinh & duyệt câu hỏi AI (`t.practiceEditor`, `t.assessmentEditor`):** Tích hợp trực tiếp tạo batch câu hỏi từ tài liệu lớp, xem trạng thái `PENDING` -> duyệt/từ chối/sinh lại từng câu, xuất sang ngân hàng Learning (`POST /internal/v1/quiz-items/import`).

### 4.4. Luyện tập, Kiểm tra, Bài tập & Phúc khảo (`Learning`)
- **`UI-L01` Luyện tập & Giải thích lỗi sai (`s.practiceAttempt`, `s.practiceResult`):** Hỗ trợ trắc nghiệm đơn, đa lựa chọn, điền chỗ trống, tự luận ngắn; kiểm tra từng câu và nút **Giải thích lỗi sai** gọi AI, hiển thị rõ luyện tập không tính điểm chính thức (`D8`).
- **`UI-L02` Kiểm tra chính thức (`s.assessment`, `s.examAttempt`, `s.receipt`):** Đồng hồ neo theo `serverNow`, auto-save từng câu tuần tự theo `revision` (`AnswerSaver`), phát hiện xung đột bản lưu từ tab/thiết bị khác, giữ nguyên `operationId` khi timeout để đối soát `/reconcile`, màn biên nhận lưu mã xác nhận.
- **`UI-L03` Bài tập tự luận & Nộp tệp (`s.assignment`, `t.assignmentEditor`):** Giao bài theo toàn lớp hoặc nhóm chọn lọc, gia hạn cá nhân; học sinh chuẩn bị tệp -> xác nhận nộp bài tách bước kèm `/reconcile` (`15 MB` trên giao diện bài tập; giới hạn backend lên tới `25 MiB` tại Learning service).
- **`UI-L04` Chấm điểm & Phúc khảo (`t.gradingQueue`, `t.assignmentGrading`, `t.examGrading`, `t.correction`, `t.correctionApproval`, `s.historyResult`):** Hàng chờ phân công chấm, nhận gợi ý phản hồi từ AI ở chế độ tư vấn (`D5`), luồng phúc khảo `M4`/`I7` và phê duyệt sửa điểm bởi `PRIMARY` (`I6`).

---

## 5. Khả năng hiển thị trên Chế độ Live vs Mock & Chuẩn Tiếp cận

- **Ẩn điều hướng trên `live` (`src/lib/route-availability.ts`):** 4 mục thuộc phân hệ chưa sẵn sàng bị ẩn khỏi Sidebar/ClassTabs ở chế độ `live` (`s.progress`, `t.analytics`, `a.reports`, `a.dataRequests`), và 2 route sâu (`t.studentAnalytics`, `a.exports`) + `a.legacy` hiển thị màn hình trạng thái trung thực thay vì dùng dữ liệu giả.
- **Bảo vệ dữ liệu nhập & Accessibility:**
  - `useUnsavedChanges` + `draftStore` gắn key theo `(actorId, resourceId, revision)` ngăn rò dữ liệu nháp khi đổi tài khoản trên cùng trình duyệt.
  - Mọi trang (gồm Auth, Loading, 401/403) có `.skip-link` tới `#main-content`.
  - Mọi modal (xóa đa lớp, bắt đầu/nộp thi, drawer mobile) dùng `<dialog>` có focus trap (`Tab` / `Shift+Tab`), khóa `Escape` khi đang xử lý (`busy`) và trả focus khi đóng.
  - Responsive đã tinh chỉnh cho `320px`, `390px`, `768px`, `1024px`, `1440px` với `.scroll-x` cục bộ cho bảng/lịch rộng.
