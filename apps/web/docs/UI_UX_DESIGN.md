# Tài liệu Thiết kế UI/UX — Lumi Web (`apps/web`)

**Trạng thái:** `IMPLEMENTED` · **Cập nhật:** 2026-11-22  
**Ngăn xếp giao diện:** Next.js 15 (`15.5.12`), React 19, TypeScript, CSS Modules/Scoped Feature CSS & Canonical Tokens (`src/styles/lumi-foundation.css`).

---

## 1. Triết lý và mục tiêu trải nghiệm

Lumi phục vụ **học sinh THCS & THPT (khối 6–12)**, **giáo viên** (`LECTURER`) và **quản trị viên trường học** (`ADMIN`) trong lớp học chính khóa (`BUSINESS_RULES.md`):
- **Sáng, gọn, tập trung học tập:** không dùng game hóa (huy hiệu/streak/bảng xếp hạng) hoặc trang trí phân tâm.
- **Không thành công giả (`No fake success`):** mọi thao tác ghi chỉ báo thành công sau khi backend trả ACK/receipt hợp lệ (`401`/`403`/`404`/`409`/`422`/`503` được giữ nguyên ngữ nghĩa).
- **An toàn trạng thái & cô lập chế độ:** chế độ `live` không bao giờ fallback sang fixture mẫu (`dev/mocks/*`); lỗi dịch vụ con (Document/AI/Learning) được cô lập cục bộ qua `optionalServiceRead` mà không làm sập shell hoặc màn hình lớp Core.
- **Tiếp cận bàn phím & đa thiết bị:** phản hồi tương thích 320px / 390px / 768px / 1024px / 1440px, vòng focus rõ (`3px`), nút tối thiểu `44px`, bảng/lịch rộng cuộn cục bộ có nhãn `aria-label`.

---

## 2. Hệ thống thiết kế (Design System Tokens & Styles)

Nguồn chuẩn duy nhất cho token và thành phần nền nằm tại [src/styles/lumi-foundation.css](../src/styles/lumi-foundation.css), được import một lần trong `app/layout.tsx` ngay sau `app/globals.css`. Các domain UI sở hữu thêm **20 tệp CSS scoped** trong `src/features/**`.

| Nhóm Token | Biến CSS chính | Giá trị mặc định | Vai trò thị giác |
|---|---|---|---|
| **Thương hiệu & Hành động chính** | `--primary`, `--primary-hover`, `--primary-tint`, `--primary-border` | `#2563eb`, `#1d4ed8`, `#eff6ff`, `#bfdbfe` | Nút chính, liên kết, tab lớp đang chọn, focus ring `--focus-ring` |
| **Điểm nhấn AI (Trợ lý Lumi)** | `--ai`, `--ai-tint`, `--ai-border` | `#0d9488`, `#f0fdfa`, `#99f6e4` | Khối **Hỏi Lumi**, badge gợi ý AI, panel trợ lý học tập & gợi ý phản hồi |
| **Văn bản & Bề mặt** | `--ink`, `--ink-secondary`, `--muted`, `--bg`, `--surface`, `--surface-subtle` | `#0f172a`, `#334155`, `#5b6b82`, `#f8fafc`, `#ffffff`, `#f1f5f9` | Nền trang sáng dịu, thẻ trắng viền `--border` (`#e2e8f0`) |
| **Ngữ nghĩa trạng thái** | `--success`, `--warning`, `--danger` (+ `-tint`, `-border`) | `#166534`, `#92400e`, `#b91c1c` | Trạng thái phiên bản tài liệu (`Sẵn sàng`/`Cần rà soát`/`Thất bại`), điểm chính thức, cảnh báo nộp muộn/xung đột |
| **Bố cục & Hình học** | `--sidebar`, `--topbar`, `--radius-sm..xl`, `--shadow-xs..dialog` | `260px` (`224px` ở tablet), `64px`, `6px..16px` | Khung Sidebar cố định trái + Topbar cố định trên + bo góc thống nhất |
| **Typography & Chạm** | `font-family`, `font-size`, `min-height` | `Inter, system-ui, sans-serif`, `14px` (`h1` `24px`, `h2` `18px`, `h3` `15px`), `button`/`input` tối thiểu `44px` | Đọc rõ trên cả desktop lẫn màn hình cảm ứng di động |

---

## 3. Kiến trúc bố cục (7 Layouts) & Khung vỏ (`PageShell`)

Mọi trang đều đi qua [RouteScreen](../src/components/route-screen.tsx) và [PageShell](../src/components/shell.tsx), sử dụng liên kết bỏ qua (`.skip-link`) trỏ tới `#main-content` trên mọi màn hình (kể cả trang đăng nhập, loading, 401/403).

| Layout | Số route | Thành phần khung & mục đích |
|---|---:|---|
| `AuthLayout` | 5 | Bố cục 2 cột desktop (cột giới thiệu sản phẩm + card form `/login`, `/activate`, `/recover`, `/reset-password`, `/staff/invitation`); thu gọn 1 cột trên `<= 860px`. |
| `AppLayout` | 30 | `app-shell`: Sidebar `260px` (ẩn vào Drawer `<Dialog>` dưới `<= 760px`), Topbar `64px` (breadcrumb, chuông `/notifications`, hồ sơ `/account`), vùng nội dung chính. |
| `StudentClassLayout` | 13 | Kế thừa `AppLayout` + thanh `ClassHeader` (mã lớp, trạng thái `Đang học`/`Đã kết thúc ghi danh`/`Đã lưu trữ`, metadata giáo viên) và tối đa 9 tab lớp học sinh (tự ẩn tab unavailable khi `live`). |
| `TeacherClassLayout` | 12 | Kế thừa `AppLayout` + `ClassHeader` (nhãn `Giáo viên chính` / `Giáo viên phối hợp` / `Đã lưu trữ`) và tối đa 7 tab lớp giáo viên. |
| `ReaderLayout` | 1 | `reader-shell` (`s.reader`): tối ưu không gian đọc tài liệu Canonical nhiều trang/slide song song với ghi chú cá nhân và đánh dấu trang. |
| `FocusedTaskLayout` | 8 | `focused-shell`: ẩn Sidebar/tab lớp, giữ `task-header` gọn nhẹ và liên kết quay lại — dùng cho soạn luyện tập, lượt luyện tập, làm kiểm tra chính thức có đồng hồ (`s.examAttempt`), biên nhận (`s.receipt`), chấm bài (`t.assignmentGrading`, `t.examGrading`) và quy trình sửa điểm (`t.correction`, `t.correctionApproval`). |
| `HistoryLayout` | 2 | Khung lịch sử cá nhân (`s.history`, `s.historyResult`): hiển thị thông báo *"Lịch sử cá nhân · Quyền truy cập nguồn được kiểm tra riêng"* và thu gọn breadcrumb để không lộ điều hướng lớp mới. |

> **Tối ưu điều hướng & tab lớp:** Trên mobile/tablet, `useCurrentClassTabVisibility` đảm bảo tab hiện hành không bị khuất khỏi tầm nhìn; `.class-tabs` hỗ trợ `flex-wrap: wrap` để mọi chuyên mục đều chạm tới được.

---

## 4. Điều hướng theo vai trò & Cơ chế khả dụng Live

Cấu hình điều hướng nằm tại [src/config/navigation.ts](../src/config/navigation.ts) và bộ lọc khả dụng live nằm tại [src/lib/route-availability.ts](../src/lib/route-availability.ts) + [src/lib/data/integrations.ts](../src/lib/data/integrations.ts):

### 4.1. Thanh bên chính (`sidebars`)
- **Học sinh (`STUDENT`):** `Trang chủ` (`s.home`), `Lớp học của tôi` (`s.classes`), `Lịch học` (`account.calendar`), `Thông báo` (`account.notifications`), `Ghi chú & Đã lưu` (`s.notes`), `Hồ sơ và bảo mật` (`account.profile` ở footer sidebar).
- **Giáo viên (`LECTURER`):** `Tổng quan giảng dạy` (`t.home`), `Lớp phụ trách` (`t.classes`), `Thư viện tài liệu` (`t.library`), `Lịch dạy & Sự kiện` (`account.calendar`), `Thông báo` (`account.notifications`), `Hồ sơ và bảo mật` (`account.profile`).
- **Quản trị viên (`ADMIN`):** `Tài khoản` (`a.accounts`), `Nhập danh sách` (`a.importNew`), `Danh mục học vụ` (`a.catalog`), `Lớp hành chính` (`a.homerooms`), `Lớp môn học` (`a.classes`), *(ẩn live)* `Yêu cầu dữ liệu` (`a.dataRequests`), `Nhật ký hệ thống` (`a.audit`), *(ẩn live)* `Báo cáo` (`a.reports`), `Hồ sơ và bảo mật` (`account.profile`).

### 4.2. Tab trong lớp học (`classTabs`)
- **Học sinh (`StudentClassLayout`):** `Tổng quan`, `Bảng tin`, `Tài liệu`, `Hỏi Lumi`, `Luyện tập`, `Kiểm tra`, `Bài tập`, `Lịch học`, *(ẩn live)* `Tiến độ` (`s.progress`).
- **Giáo viên (`TeacherClassLayout`):** `Tổng quan`, `Bảng tin`, `Tài liệu`, `Hỏi Lumi`, `Hoạt động học tập`, `Lịch học`, *(ẩn live)* `Thống kê` (`t.analytics`).

> Các route thuộc `Learner Intelligence` (`s.progress`, `t.analytics`, `t.studentAnalytics`, `a.reports`, `a.exports`) và `a.dataRequests` / `a.legacy` chỉ hoạt động đầy đủ trong chế độ `mock` preview; trong chế độ `live`, thanh điều hướng ẩn các mục chưa sẵn sàng và khi truy cập trực tiếp sẽ hiển thị màn hình trạng thái trung thực (không giả lập dữ liệu).

---

## 5. Bản đồ 71 Product Routes theo phân hệ & Backend sở hữu

*(Ngoài 71 route bên dưới, `app/page.tsx` thực hiện chuyển hướng theo phiên đăng nhập hiện hành.)*

### 5.1. Xác thực & Tài khoản chung (Core Identity / Notification / Social Calendar)
| Route ID | Đường dẫn | Vai trò | Layout | Mục đích |
|---|---|---|---|---|
| `auth.login` | `/login` | `public` | `AuthLayout` | Đăng nhập (Học sinh dùng username, Giáo viên/Admin dùng email) |
| `auth.activate` | `/activate` | `public` | `AuthLayout` | Kích hoạt tài khoản học sinh bằng mã một lần & đặt mật khẩu |
| `auth.invitation` | `/staff/invitation` | `public` | `AuthLayout` | Nhận lời mời nhân sự (đọc token từ URL fragment và xóa khỏi history) |
| `auth.recover` | `/recover` | `public` | `AuthLayout` | Quên mật khẩu / hướng dẫn khôi phục |
| `auth.reset` | `/reset-password` | `public` | `AuthLayout` | Đặt lại mật khẩu một lần |
| `account.profile` | `/account` | Tất cả | `AppLayout` | Hồ sơ cá nhân, đổi tên hiển thị, đăng xuất tất cả thiết bị |
| `account.requests` | `/account/data-requests` | Tất cả | `AppLayout` | Gửi & theo dõi yêu cầu dữ liệu cá nhân (`ACCESS`/`EXPORT`/`CORRECTION`/`DELETE`) |
| `account.notifications` | `/notifications` | Tất cả | `AppLayout` | Hộp thông báo cá nhân, đếm chưa đọc, đánh dấu đã đọc / đọc tất cả |
| `account.calendar` | `/calendar` | `STUDENT`, `LECTURER` | `AppLayout` | Lịch tổng hợp các lớp còn quyền (CORE events + Learning deadline projections) |

### 5.2. Lớp học, Bảng tin & Lịch lớp (Core Classroom + Social + Dashboard Enrichment)
| Route ID | Đường dẫn | Vai trò | Layout | Mục đích & Tích hợp chéo |
|---|---|---|---|---|
| `s.home` | `/student` | `STUDENT` | `AppLayout` | Trang chủ học sinh: lớp học, bài sắp tới, luyện tập gần đây, gợi ý Hỏi Lumi |
| `s.classes` | `/student/classes` | `STUDENT` | `AppLayout` | Danh sách lớp môn học hiện tại & lớp lịch sử |
| `s.overview` | `/student/classes/:classId/overview` | `STUDENT` | `StudentClassLayout` | Tổng quan lớp: bài viết mới nhất, tài liệu mới nhất (`Document`), hoạt động sắp tới (`Learning`) |
| `s.feed` | `/student/classes/:classId/feed` | `STUDENT` | `StudentClassLayout` | Bảng tin & bình luận (tự động chuyển read-only khi lớp `ARCHIVED`) |
| `s.calendar` | `/student/classes/:classId/calendar` | `STUDENT` | `StudentClassLayout` | Lịch lớp (`CORE` + `LEARNING` deadline) |
| `s.history` | `/student/history/classes/:classId` | `STUDENT` | `HistoryLayout` | Lịch sử lớp cũ: hội thoại AI cũ (che nguồn đã thu hồi) & ghi chú cá nhân |
| `t.home` | `/teacher` | `LECTURER` | `AppLayout` | Tổng quan giảng dạy: số học sinh phân biệt, bài chờ chấm (`Learning`), lớp phụ trách, bài viết gần đây |
| `t.classes` | `/teacher/classes` | `LECTURER` | `AppLayout` | Danh sách lớp chính / phối hợp / đã lưu trữ |
| `t.overview` | `/teacher/classes/:classId/overview` | `LECTURER` | `TeacherClassLayout` | Tổng quan lớp giáo viên: danh sách học sinh, bài viết mới nhất, hoạt động & chỉ số chờ chấm |
| `t.feed` | `/teacher/classes/:classId/feed` | `LECTURER` | `TeacherClassLayout` | Bảng tin & kiểm duyệt bình luận lớp |
| `t.calendar` | `/teacher/classes/:classId/calendar` | `LECTURER` | `TeacherClassLayout` | Quản lý sự kiện `CORE` và xem deadline `LEARNING` |

### 5.3. Tài liệu & Ghi chú (Document Service)
| Route ID | Đường dẫn | Vai trò | Layout | Điểm nhấn UI/UX |
|---|---|---|---|---|
| `t.library` | `/teacher/library` | `LECTURER` | `AppLayout` | Thư viện nguồn cá nhân: upload 5 định dạng (`PDF/PPTX/DOCX/TXT/MD`), hiển thị chính sách `20 MB` / `300` trang từ `/documents/policy` |
| `t.libraryDocument` | `/teacher/library/:documentId` | `LECTURER` | `AppLayout` | Chi tiết nguồn & lịch sử phiên bản: trạng thái `Sẵn sàng`/`Đang xử lý`/`Cần rà soát`/`Thất bại`/`Bản lưu`, nút **Chấp nhận** (`NEEDS_REVIEW`), **Thử lại** (`FAILED`), chia sẻ lớp & cảnh báo xóa đa lớp (`B6`) |
| `t.documents` | `/teacher/classes/:classId/documents` | `LECTURER` | `TeacherClassLayout` | Tài liệu được chia sẻ trong lớp, đánh dấu **"Của bạn"** với chủ nguồn, thêm/gỡ grant |
| `s.documents` | `/student/classes/:classId/documents` | `STUDENT` | `StudentClassLayout` | Danh sách tài liệu lớp kèm tìm kiếm & lọc định dạng |
| `s.reader` | `/student/classes/:classId/documents/:documentId` | `STUDENT` | `ReaderLayout` | Trình đọc Canonical theo trang/slide, ghi chú gắn vị trí/phiên bản, dấu trang, tải tệp gốc; tự che nội dung nguồn và `source_quote` khi grant bị thu hồi (`H6/L6`) |
| `s.notes` | `/student/notes` | `STUDENT` | `AppLayout` | Tổng hợp ghi chú & dấu trang cá nhân across classes; sửa/xóa ghi chú ngay cả khi nguồn mất quyền hoặc lớp `ARCHIVED` (`O2`) |

### 5.4. Trợ lý AI Tutor & Sinh câu hỏi (AI Service)
| Route ID | Đường dẫn | Vai trò | Layout | Điểm nhấn UI/UX |
|---|---|---|---|---|
| `s.tutor` | `/student/classes/:classId/tutor` | `STUDENT` | `StudentClassLayout` | Khung hội thoại SSE (`QUICK_QA` / `STEP_BY_STEP` với `HINT` / `CHECK` / `REVEAL`), nút **Dừng phản hồi** (`/stop`), trích dẫn deep-link thẳng sang `s.reader?page=...`, panel ghi chú nhanh & chặn hỏi mới khi đang thi chính thức (`D6`) |
| `t.tutor` | `/teacher/classes/:classId/tutor` | `LECTURER` | `TeacherClassLayout` | Cấu hình sư phạm Tutor theo lớp (`allowGeneralKnowledge`, `allowSolutionReveal`, `guidanceLevel`, `hintRevealAfter`, `maxAnswerWords`) kèm khóa lạc quan `expectedVersion` (`409`) |

*(Giao diện sinh đề xuất ma trận, duyệt, sinh lại từng câu và xuất câu hỏi AI sang ngân hàng Learning được tích hợp trực tiếp trong màn hình soạn thảo hoạt động `t.practiceEditor` và `t.assessmentEditor`.)*

### 5.5. Học tập, Chấm điểm & Phúc khảo (Learning Service)
| Route ID | Đường dẫn | Vai trò | Layout | Điểm nhấn UI/UX |
|---|---|---|---|---|
| `s.practice` | `/student/classes/:classId/practice` | `STUDENT` | `StudentClassLayout` | Danh sách bài luyện tập (bắt đầu / tiếp tục / xem kết quả) |
| `s.practiceAttempt` | `/student/classes/:classId/practice/:practiceId/attempts/:attemptId` | `STUDENT` | `FocusedTaskLayout` | Làm bài luyện tập 4 dạng câu hỏi, kiểm tra đáp án tức thì & **Giải thích lỗi sai** (`AI explain-mistake`) |
| `s.practiceResult` | `/student/classes/:classId/practice/:practiceId/attempts/:attemptId/result` | `STUDENT` | `StudentClassLayout` | Kết quả luyện tập (ghi rõ không tính vào điểm chính thức `D8`) |
| `s.assessments` | `/student/classes/:classId/assessments` | `STUDENT` | `StudentClassLayout` | Danh sách bài kiểm tra chính thức (`UPCOMING` / `OPEN` / `CLOSED`) |
| `s.assessment` | `/student/classes/:classId/assessments/:assessmentId` | `STUDENT` | `StudentClassLayout` | Thông tin phiên bản đề, thời lượng, số lượt, lưu ý tắt Hỏi Lumi (`D6`) và xác nhận bắt đầu |
| `s.examAttempt` | `/student/classes/:classId/assessments/:assessmentId/attempts/:attemptId` | `STUDENT` | `FocusedTaskLayout` | Làm bài kiểm tra chính thức: đồng hồ neo theo `serverNow`, lưu tuần tự theo revision (`AnswerSaver`), đối soát xung đột server/local, idempotent `submit` + `/reconcile` |
| `s.receipt` | `/student/classes/:classId/assessments/:assessmentId/attempts/:attemptId/receipt` | `STUDENT` | `FocusedTaskLayout` | Biên nhận nộp bài chính thức (mã biên nhận, phiên bản đề, số câu đã ACK) |
| `s.examResult` | `/student/classes/:classId/assessments/:assessmentId/attempts/:attemptId/result` | `STUDENT` | `StudentClassLayout` | Kết quả đã công bố, phản hồi và gửi yêu cầu xem xét điểm |
| `s.assignments` | `/student/classes/:classId/assignments` | `STUDENT` | `StudentClassLayout` | Danh sách bài tập tự luận, bộ lọc chuyên đề/trạng thái |
| `s.assignment` | `/student/classes/:classId/assignments/:assignmentId` | `STUDENT` | `StudentClassLayout` | Đề bài, chính sách nộp trễ/lại, tách 2 bước **Chuẩn bị tệp** → **Xác nhận nộp** kèm `/reconcile`, lịch sử bản nộp & phúc khảo |
| `s.historyResult` | `/student/history/results/:resultId` | `STUDENT` | `HistoryLayout` | Xem kết quả & gửi phúc khảo khi đã kết thúc ghi danh (`M1`/`O4`) |
| `t.activities` | `/teacher/classes/:classId/activities` | `LECTURER` | `TeacherClassLayout` | Quản lý toàn bộ hoạt động lớp (`Practice`, `Assessment`, `Assignment`) & tạo mới |
| `t.practiceEditor` | `/teacher/classes/:classId/practice/:practiceId/edit` | `LECTURER` | `FocusedTaskLayout` | Soạn/gắn từ ngân hàng/sinh AI/duyệt câu hỏi (`D1`) và công bố bài luyện tập |
| `t.assessmentEditor` | `/teacher/classes/:classId/assessments/:assessmentId/edit` | `LECTURER` | `TeacherClassLayout` | Cấu hình đề thi chính thức, duyệt 100% câu hỏi và gửi phê duyệt |
| `t.assessmentApproval` | `/teacher/classes/:classId/assessments/:assessmentId/review` | `LECTURER` | `TeacherClassLayout` | Phê duyệt công bố Assessment (`PRIMARY` approve/reject) & tạo phiên bản mới (`O3`) |
| `t.assignmentEditor` | `/teacher/classes/:classId/assignments/:assignmentId/edit` | `LECTURER` | `TeacherClassLayout` | Soạn đề bài, gắn tài liệu nguồn lớp, cấu hình hạn/nộp muộn/đối tượng giao & gia hạn cá nhân |
| `t.gradingQueue` | `/teacher/classes/:classId/grading` | `LECTURER` | `TeacherClassLayout` | Hàng chờ chấm bài tập & kiểm tra theo phân công, công bố điểm & danh sách phúc khảo/đề xuất sửa điểm |
| `t.assignmentGrading` | `/teacher/classes/:classId/assignments/:assignmentId/submissions/:submissionId/grade` | `LECTURER` | `FocusedTaskLayout` | Xem tệp bài nộp (sandbox iframe/download), nhận **Gợi ý phản hồi từ Lumi** (chỉ tư vấn, giáo viên quyết định điểm `D5`) & xác nhận điểm |
| `t.examGrading` | `/teacher/classes/:classId/assessments/:assessmentId/attempts/:attemptId/grade` | `LECTURER` | `FocusedTaskLayout` | Chấm câu trả lời ngắn / chủ quan trong bài kiểm tra |
| `t.correction` | `/teacher/classes/:classId/appeals/:appealId` | `LECTURER` | `FocusedTaskLayout` | Xem xét yêu cầu phúc khảo & gửi đề xuất điều chỉnh điểm hoặc giải quyết (`M4`/`I7`) |
| `t.correctionApproval` | `/teacher/classes/:classId/grade-proposals/:proposalId/review` | `LECTURER` | `FocusedTaskLayout` | Giáo viên chính (`PRIMARY`) phê duyệt/từ chối đề xuất sửa điểm (`I6`) |

### 5.6. Quản trị trường học (Core Administration & Classroom Admin)
| Route ID | Đường dẫn | Vai trò | Layout | Mục đích |
|---|---|---|---|---|
| `a.accounts` | `/admin/accounts` | `ADMIN` | `AppLayout` | Tìm kiếm (debounce 200ms), lọc tài khoản toàn trường (sort tự nhiên & phân trang) |
| `a.account` | `/admin/accounts/:userId` | `ADMIN` | `AppLayout` | Cập nhật vai trò/trạng thái nhân sự/học sinh (`expected_revision`, `X-Admin-Reason`), cấp lại mã khôi phục học sinh |
| `a.legacy` | `/admin/accounts/legacy` | `ADMIN` | `AppLayout` | Giao diện chuẩn hóa tài khoản Student cũ (live giữ thông báo xử lý qua công cụ bảo trì offline) |
| `a.newStudent` | `/admin/accounts/students/new` | `ADMIN` | `AppLayout` | Cấp tài khoản học sinh & hiển thị mã kích hoạt một lần (xác nhận trước khi đóng) |
| `a.staffInvite` | `/admin/accounts/staff/invitations` | `ADMIN` | `AppLayout` | Mời giáo viên/quản trị viên qua email, theo dõi trạng thái gửi & cấp lại lời mời |
| `a.importNew` | `/admin/imports/new` | `ADMIN` | `AppLayout` | Tải tệp CSV/XLSX (`read-excel-file`), ánh xạ cột & preview import học sinh |
| `a.import` | `/admin/imports/:importId` | `ADMIN` | `AppLayout` | Xem chi tiết preview, xác nhận batch atomic & đối soát timeout (`preview_id`), hiển thị mã lần đầu |
| `a.catalog` | `/admin/catalog` | `ADMIN` | `AppLayout` | Quản lý môn học (`Courses`), năm học, học kỳ & danh sách khối 6–12 |
| `a.homerooms` | `/admin/homerooms` | `ADMIN` | `AppLayout` | Danh sách & tạo lớp hành chính (khối 6–12) |
| `a.homeroom` | `/admin/homerooms/:homeroomId` | `ADMIN` | `AppLayout` | Quản lý sĩ số roster lớp hành chính & chuyển lớp hành chính cùng khối (`expected_roster_version`) |
| `a.classes` | `/admin/classes` | `ADMIN` | `AppLayout` | Danh sách lớp môn học |
| `a.newClass` | `/admin/classes/new` | `ADMIN` | `AppLayout` | Tạo lớp môn học & phân công giáo viên `PRIMARY` / `ASSISTING` trong cùng transaction |
| `a.classSettings` | `/admin/classes/:classId/settings` | `ADMIN` | `AppLayout` | Cập nhật phân công giáo viên & xem lịch sử cấu hình lớp |
| `a.enrollment` | `/admin/classes/:classId/enrollment` | `ADMIN` | `AppLayout` | Chế độ ghi danh `AUTO`/`MANUAL`, preview tác động trước khi sync/chuyển mode & chuyển lớp `MANUAL` |
| `a.lifecycle` | `/admin/classes/:classId/lifecycle` | `ADMIN` | `AppLayout` | Lưu trữ (`ARCHIVE`) / khôi phục (`RESTORE`) lớp môn học |
| `a.audit` | `/admin/audit` | `ADMIN` | `AppLayout` | Nhật ký hệ thống theo trường, lọc actor/action/thời gian |

### 5.7. Phân hệ chờ triển khai backend (Learner Intelligence — Ẩn trên điều hướng Live)
| Route ID | Đường dẫn | Vai trò | Trạng thái Live |
|---|---|---|---|
| `s.progress` | `/student/classes/:classId/progress` | `STUDENT` | Ẩn tab lớp trên live; UI hoàn chỉnh trong mock preview (`PROPOSED` `TASK-020`) |
| `t.analytics` | `/teacher/classes/:classId/analytics` | `LECTURER` | Ẩn tab lớp trên live; UI hoàn chỉnh trong mock preview |
| `t.studentAnalytics` | `/teacher/classes/:classId/analytics/students/:studentId` | `LECTURER` | Unavailable trên live |
| `a.dataRequests` | `/admin/data-requests` | `ADMIN` | Ẩn sidebar trên live; chờ hợp đồng review/copy fulfillment (`OD-02`, `OD-03`) |
| `a.reports` | `/admin/reports` | `ADMIN` | Ẩn sidebar trên live; chờ Learner Intelligence export PDF/XLSX |
| `a.exports` | `/admin/reports/exports` | `ADMIN` | Unavailable trên live |

---

## 6. Mẫu tương tác & An toàn dữ liệu trên giao diện

1. **Bảo vệ thay đổi chưa lưu (`useUnsavedChanges` & `draftStore`):** Các biểu mẫu quan trọng (hồ sơ, mời nhân sự, ghi chú, cấu hình Tutor, soạn đề/bài tập, làm bài thi, chấm điểm, phúc khảo) đều khóa điều hướng khi dirty; bản nháp tạm trong phiên được gắn khóa `(actorId, resourceId, revision)` để không rò sang người dùng khác khi đổi tài khoản trên cùng trình duyệt.
2. **Xử lý trạng thái chưa rõ (`Unknown Outcome Reconciliation`):** Với Assessment submit, Assignment submission và Bulk Student Import confirm, khi gặp timeout hoặc mất mạng sau dispatch, UI **giữ nguyên `operationId` / `preview_id`**, khóa thao tác gửi mù và cung cấp nút đối soát (`/reconcile` hoặc status lookup).
3. **Đồng bộ phiên & cô lập lỗi chéo (`401` vs `503`):** `optionalServiceRead` chặn các lỗi phụ thuộc cục bộ (ví dụ Document hoặc Learning trả `503`) để màn hình Home/Overview/Tutor vẫn mở bình thường và hiển thị `Chưa có dữ liệu` thay vì bịa số `0` hoặc `14`; riêng mã `401` luôn được ném lên tầng phiên để buộc xác thực lại.
4. **Hộp thoại chuẩn native (`<dialog>`):** Toàn bộ modal xác nhận (xóa đa lớp `B6`, bắt đầu/nộp bài thi, xác nhận xóa ghi chú, drawer điều hướng mobile) dùng component `Dialog` với focus trap (`Tab` / `Shift+Tab`), phím `Escape` (tạm khóa khi `busy`) và trả focus về nút mở ban đầu khi đóng.
