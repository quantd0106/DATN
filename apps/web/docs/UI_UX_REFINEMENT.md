# Lumi UI/UX refinement — 2026-11-15 → 2026-11-22

> **Lưu ý hiện trạng (`2026-11-22`):** Tài liệu này ghi lại đợt chuẩn hóa hệ thống trình bày (`lumi-foundation.css` và các CSS scoped theo feature) ngày 2026-11-15. Từ 2026-11-22, các dịch vụ **Document**, **AI** (`tutor` + Quiz) và **Learning** (Practice, Assessment, Assignment, Grading/Corrections, Appeals, Calendar projection) đã được tích hợp live qua proxy; chỉ còn **Learner Intelligence** và một số thao tác quản trị đang chờ hợp đồng/chính sách giữ trạng thái ẩn/unavailable. Xem [UI_UX_DESIGN.md](UI_UX_DESIGN.md) và [UX_LIVE_FIXES.md](UX_LIVE_FIXES.md) để nắm hiện trạng live mới nhất.

## Phạm vi

Chuẩn hóa tầng trình bày và tương tác cho toàn bộ **71 product routes** trên `apps/web`, giữ nguyên ranh giới nghiệp vụ (`BUSINESS_RULES.md`), adapter interface (`src/lib/data/port.ts`), kiểm tra quyền backend và cơ chế cô lập `live` / `mock`. Các file `app/**/page.tsx` giữ vai trò entry point mỏng chuyển tiếp sang `RouteScreen` và `FeatureEntry`.

## Bảng phân nhóm 71 routes đã chuẩn hóa giao diện

| Nhóm | Route IDs | Trạng thái live hiện tại (`2026-11-22`) |
|---|---|---|
| Authentication | `auth.login`, `auth.activate`, `auth.recover`, `auth.reset`, `auth.invitation` | Wired (`Core`) |
| Classroom | `s.home`, `s.classes`, `s.overview`, `s.history`, `t.home`, `t.classes`, `t.overview` | Wired (`Core` + cross-service enrichment Document/Learning; `s.history` pending API chi tiết) |
| Communication | `s.feed`, `t.feed`, `s.calendar`, `t.calendar`, `account.calendar`, `account.notifications` | Wired (`Core` + `Learning` deadline projection) |
| Documents & notes | `t.library`, `t.libraryDocument`, `t.documents`, `s.documents`, `s.reader`, `s.notes` | Wired (`Document`) |
| Tutor & Quiz AI | `s.tutor`, `t.tutor` (+ Quiz authoring trong `t.practiceEditor` / `t.assessmentEditor`) | Wired (`AI`) |
| Practice & activities | `s.practice`, `s.practiceAttempt`, `s.practiceResult`, `t.activities`, `t.practiceEditor` | Wired (`Learning` + `AI`) |
| Official assessments | `s.assessments`, `s.assessment`, `s.examAttempt`, `s.receipt`, `s.examResult`, `t.assessmentEditor`, `t.assessmentApproval`, `t.examGrading` | Wired (`Learning`) |
| Assignment & grading | `s.assignments`, `s.assignment`, `s.historyResult`, `t.assignmentEditor`, `t.gradingQueue`, `t.assignmentGrading`, `t.correction`, `t.correctionApproval` | Wired (`Learning` + `AI` grade-hint) |
| Account & provisioning | `account.profile`, `account.requests`, `a.newStudent` | Wired (`Core`; download bản sao dữ liệu thủ công chờ chính sách) |
| Account administration | `a.accounts`, `a.account`, `a.legacy`, `a.staffInvite`, `a.importNew`, `a.import` | Wired (`Core`; `a.legacy` giữ trạng thái bảo trì offline) |
| Classroom administration | `a.catalog`, `a.homerooms`, `a.homeroom`, `a.classes`, `a.newClass`, `a.classSettings`, `a.enrollment`, `a.lifecycle` | Wired (`Core`) |
| Evidence, audit & reports | `s.progress`, `t.analytics`, `t.studentAnalytics`, `a.dataRequests`, `a.audit`, `a.reports`, `a.exports` | `a.audit` wired (`Core`); các route Intelligence & `a.dataRequests` adapter giữ unavailable/ẩn trên điều hướng live |

## Các chuẩn hóa thiết kế & UX đã áp dụng

- **Canonical Design Tokens (`src/styles/lumi-foundation.css`):** định nghĩa tập trung tokens màu (`--primary: #2563eb`, `--ai: #0d9488`, bảng slate trung tính, màu trạng thái semantic), typography (`Inter`), khoảng cách, nút tối thiểu `44px`, vòng focus `3px`, notice, state panel, dialog native (`<dialog>`) và auth layout; import một lần tại `app/layout.tsx` sau `app/globals.css`.
- **Điều hướng & truy cập:** phân biệt thanh bên theo vai trò (`STUDENT`, `LECTURER`, `ADMIN`), đưa liên kết hồ sơ vào chân sidebar trên desktop và giữ trong drawer mobile; liên kết bỏ qua (`skip-link`) trỏ đúng `#main-content` trên mọi layout kể cả màn hình đăng nhập, lỗi phiên và loading.
- **Classroom & Calendar:** dùng CSS container queries cho các cột Home/Overview; chuẩn hóa bộ selector CSS lịch tuần 7 ngày, chú giải và vùng cuộn ngang có nhãn truy cập (`aria-label`); tab lớp tự động wrap và reveal tab đang chọn khi điều hướng hoặc đổi kích thước màn hình.
- **Document & Notes:** thay thế toàn bộ utility class thiếu định nghĩa bằng các class scoped (`source-management-ui.css`, `library.css`, `reader.css`); bổ sung nhãn **Cần rà soát (`NEEDS_REVIEW`)**, nút **Chấp nhận**, trạng thái **Bản lưu**, giữ nguyên `version`/`page` trên liên kết ghi chú/bookmark, bảo vệ thay đổi chưa lưu (`useUnsavedChanges`), xác nhận khi xóa và giữ thông báo lỗi trong dialog đang mở.
- **Feed & Lớp lưu trữ:** nút đăng/sửa/xóa chỉ hiển thị khi ngữ cảnh lớp hiện hành còn quyền và trạng thái `ACTIVE`; lớp `ARCHIVED` chuyển sang chế độ chỉ đọc có nhãn rõ ràng (backend vẫn là lớp kiểm tra thẩm quyền cuối cùng).
- **Practice, Assessment & Assignment:** tách CSS sang module riêng (`practice-authoring.css`, `learning-workspace.css`, `assessment.css`, `assignment.css`), cô lập namespace `.as-*`, tách trạng thái lưu câu trả lời dựa trên ACK thật thay vì khớp chuỗi văn bản; đồng hồ làm bài thi neo theo thời gian server (`serverNow` + `performance.now`), hiển thị rõ trạng thái đồng bộ và cơ chế đối soát `/reconcile`.
- **Quản trị, Import & Yêu cầu dữ liệu:** thêm cuộn ngang cục bộ cho bảng rộng, phân biệt đúng trạng thái yêu cầu dữ liệu (`PENDING`, `IN_REVIEW`, `COMPLETED`, `REJECTED`), đánh dấu `aria-current` cho tab quản trị lớp đang mở và tách biệt trường chỉ đọc với trường vô hiệu hóa.

## Kiểm chứng mốc 2026-11-15 và 2026-11-22

- **Mốc 2026-11-15 (UI/UX refinement):** `npm run check` đạt `262/262` tests, `tsc --noEmit` và boundary checks pass; `NEXT_PUBLIC_DATA_MODE=live npm run build` pass và sạch fixture canary.
- **Mốc 2026-11-22 (sau đồng bộ live Document, AI, Learning):** `npm run check` đạt **314/314 tests** (kể cả `tests/live-cross-service.test.ts`), boundary checks và `tsc --noEmit` pass; `NEXT_PUBLIC_DATA_MODE=live npm run build` biên dịch đủ **32/32** static/dynamic routes và pass `npm run check:release`.
- **Giới hạn nghiệm thu còn mở:** chưa hoàn tất đợt chụp/duyệt trình duyệt tương tác đa viewport (320/390/768/1024/1440px) trên toàn bộ luồng hỗn hợp 4 backend chạy đồng thời qua Docker Compose.
