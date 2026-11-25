# Learning Service API Contract v1

**Trạng thái:** `IMPLEMENTED` · **Tài liệu chi tiết triển khai:** [DESIGN.md](DESIGN.md) · [FOLDER_STRUCTURE.md](FOLDER_STRUCTURE.md)

---

## 1. Quy ước chung và Health

- **Health (không dùng prefix `/api/v1`):**
  - `GET /health/live` → `200 {"status": "live"}`
  - `GET /health/ready` → `200 {"status": "ready", "db": "connected", "alembic_head": true}` khi kết nối DB thành công và schema `learning` đúng revision head (`0004_grades_appeals_adjustments`), ngược lại trả `503 SERVICE_UNAVAILABLE`.
- **Public API (`/api/v1`):** trình duyệt truy cập thông qua Next.js proxy `/api/learning/v1/*` (`LEARNING_API_URL`), gửi `Authorization: Bearer <access_jwt>`. Hỗ trợ các loại câu hỏi: `single_choice`, `multiple_choice`, `true_false`, `short_answer` (chuẩn hóa `true_false_4` → `true_false`).
- **Internal API (`/internal/v1`):** dùng `X-Service-Key` (`LEARNING_INTERNAL_SERVICE_SECRET` hoặc `LEARNING_ANALYTICS_SERVICE_SECRET` cho route đọc điểm đã xác nhận).
- **Headers & Error Envelope:** mọi phản hồi đều mang `X-Correlation-ID` (UUID) và `Cache-Control: no-store`. Lỗi theo chuẩn `{"error": {"code": "...", "message": "...", "correlation_id": "uuid"}}`, mã lỗi domain gồm `REVISION_CONFLICT`, `GRADE_VERSION_CONFLICT`, `CLASS_ARCHIVED`, `UNREVIEWED_QUESTIONS`, `ATTEMPT_EXPIRED`, `PRIMARY_APPROVAL_REQUIRED`, `SELF_APPROVAL_FORBIDDEN`, `AI_HINT_UNAVAILABLE`, v.v.

---

## 2. Public Endpoints (`/api/v1`)

### 2.1. Hoạt động chung, Ngân hàng câu hỏi & Luyện tập (Practice — L1, 18 endpoints)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `GET` | `/classes/{class_id}/activities` | Giáo viên phân công (`read_class_content`); `?kind=` → `200 {items, next_cursor: null}` (kèm `pendingGradingCount`, `canDeleteDraft`) |
| `DELETE` | `/classes/{class_id}/activities/{activity_id}` | Giáo viên lớp `ACTIVE`; chỉ xóa bản nháp chưa công bố và chưa có lượt làm/bài nộp (`DRAFT`) → `204` (`409 ACTIVITY_ALREADY_PUBLISHED` nếu đã công bố/chờ duyệt/có dữ liệu) |
| `POST` | `/classes/{class_id}/activities/{activity_id}/copy` | Giáo viên đọc lớp nguồn (`read_class_content`) và dạy lớp đích `ACTIVE` (`participate_current_class`), cùng trường, khác lớp; `{target_class_id, title?}` → `201 {id, activity_id, class_id, source_activity_id, source_class_id, kind, title, status: "DRAFT", version_number, revision, edit_href, stripped_source_count}` (tạo bản nháp độc lập v1, giữ nguyên `reviewed=true` / `isReviewed=true`, lọc nguồn tài liệu theo grant lớp đích, không sao chép bài nộp/lượt làm/điểm/phúc khảo/gia hạn/audience) |
| `GET` | `/classes/{class_id}/question-bank` | Giáo viên phân công (`read_class_content`); `?origin=&type=&topic=` → `200 {items, next_cursor: null}` |
| `POST` | `/classes/{class_id}/practices` | Giáo viên lớp `ACTIVE` (`participate_current_class`); `{title, topic?}` → `201` |
| `GET` | `/classes/{class_id}/practices/{practice_id}/editor` | Giáo viên phân công → `200` (kèm danh sách nguồn tài liệu khả dụng của lớp) |
| `PUT` | `/classes/{class_id}/practices/{practice_id}/draft` | Giáo viên lớp `ACTIVE`; `{expected_revision, title?, topic?, questions}` → `200` (`409` nếu stale revision) |
| `POST` | `/classes/{class_id}/practices/{practice_id}/items/attach` | Giáo viên lớp `ACTIVE`; `{expected_revision, learning_item_ids}` → `200` |
| `POST` | `/classes/{class_id}/practices/{practice_id}/questions/{question_id}/review` | Giáo viên lớp `ACTIVE`; `{expected_revision, approved}` → `200` |
| `POST` | `/classes/{class_id}/practices/{practice_id}/publish` | Giáo viên lớp `ACTIVE`; `{expected_revision}` → `200` (bắt buộc duyệt đủ 100% câu hỏi `D1`, ghi outbox `PRACTICE_PUBLISHED` sang Core) |
| `POST` | `/classes/{class_id}/practices/{practice_id}/versions` | Giáo viên lớp `ACTIVE` → `201` phiên bản nháp mới |
| `GET` | `/classes/{class_id}/practices` | Học sinh (`read_own_history`) → `200` danh sách bài luyện tập |
| `POST` | `/classes/{class_id}/practices/{practice_id}/attempts` | Học sinh lớp `ACTIVE`; `{intent: "start"\|"resume"\|"retry"}` → `200 \| 201` |
| `GET` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}` | Học sinh (`read_own_history`) → `200` |
| `PUT` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/draft` | Học sinh lớp `ACTIVE`; `{answers, current_question_index?}` → `200` |
| `POST` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/check` | Học sinh lớp `ACTIVE`; `{question_id, answer}` → `200` (chấm tức thì & phản hồi câu hỏi) |
| `POST` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/explain-mistake` | Học sinh lớp `ACTIVE`; `{question_id}` → `200` (gọi AI giải thích lỗi sai theo evidence ghim; fallback sang lời giải đã duyệt nếu AI gián đoạn) |
| `POST` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/finish` | Học sinh lớp `ACTIVE` → `200` (hoàn thành lượt luyện tập, không ghi vào `grades`) |
| `GET` | `/classes/{class_id}/practices/{practice_id}/attempts/{attempt_id}/result` | Học sinh (`read_own_history`) → `200` |

### 2.2. Đánh giá chính thức (Official Assessment — L2, 15 endpoints)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `POST` | `/classes/{class_id}/assessments` | Giáo viên lớp `ACTIVE` → `201` (nháp mới) |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}/draft` | Giáo viên phân công → `200` (kèm `can_approve` theo vai trò `PRIMARY`, `available_sources`, `attempt_count`, `can_create_version`, `scoring_adjustments`) |
| `PUT` | `/classes/{class_id}/assessments/{assessment_id}/draft` | Giáo viên lớp `ACTIVE`; `{expected_revision, settings, questions}` → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/questions/{question_id}/review` | Giáo viên lớp `ACTIVE`; `{expected_revision}` → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/propose` | Giáo viên lớp `ACTIVE`; `{expected_revision}` → `200` (chuyển `PENDING_APPROVAL`) |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/decision` | **Chỉ `PRIMARY`** lớp `ACTIVE`; `{expected_revision, decision: "APPROVE"\|"REJECT", reason?}` → `200` (công bố đề bất biến & phát outbox deadline kèm `activity_kind="ASSESSMENT"`) |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/versions` | Giáo viên lớp `ACTIVE` → `201` phiên bản nháp mới (`O3`; chỉ khi chưa có lượt làm bài nào, ngược lại `409 ASSESSMENT_HAS_ATTEMPTS` và dùng `M4`) |
| `GET` | `/classes/{class_id}/assessments` | Học sinh lớp `ACTIVE` → `200` |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}` | Học sinh lớp `ACTIVE` → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/attempts` | Học sinh lớp `ACTIVE` → `201` (ghim `activity_version_id`, tạo đồng hồ `ends_at`) |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}` | Học sinh lớp `ACTIVE` (hoặc đang hoàn tất sau chuyển lớp theo chính sách `TRANSFER_PENDING_ATTEMPT_POLICY`) → `200` |
| `PUT` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/answers` | Như trên; `{expected_revision, answers}` → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/submit` | Như trên + bắt buộc header `Idempotency-Key` khớp `operation_id`; `{expected_revision, operation_id}` → `200` (biên nhận nộp bài) |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/reconcile` | Như trên; `?operation_id=` → `200` (đối soát trạng thái khi mất phản hồi `QAS-03`) |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/result` | Học sinh (`read_own_history`) → `200` (kèm `appeal_window_days` và che nguồn đã thu hồi) |

### 2.3. Bài tập nộp tệp (Assignment — L3, 14 endpoints)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `POST` | `/classes/{class_id}/assignments` | Giáo viên lớp `ACTIVE` → `201` |
| `PUT` | `/classes/{class_id}/assignments/{assignment_id}/draft` | Giáo viên lớp `ACTIVE`; `{expected_revision, title, topic?, instructions?, due_at, policy, source?, publish?}` → `200` (xác thực & chuẩn hóa `source` theo Document grant đang hiệu lực `403 SOURCE_ACCESS_DENIED`, đẩy outbox deadline kèm `activity_kind="ASSIGNMENT"` khi công bố/đổi hạn) |
| `PUT` | `/classes/{class_id}/assignments/{assignment_id}/recipients` | Giáo viên lớp `ACTIVE`; `{expected_revision, student_ids}` → `204` |
| `GET` | `/classes/{class_id}/assignments/{assignment_id}/recipients` | Giáo viên phân công → `200` |
| `GET` | `/classes/{class_id}/assignments/{assignment_id}/submissions/{submission_id}/grading` | Giáo viên chấm bài (`grade_submitted_work`) → `200` |
| `POST` | `/classes/{class_id}/assignments/{assignment_id}/due-overrides` | Giáo viên lớp `ACTIVE`; `{student_id?, new_due_at, reason}` → `201` |
| `GET` | `/classes/{class_id}/assignments` | Học sinh/Giáo viên có quyền đọc lớp → `200` (lọc theo quyền nguồn & đối tượng giao) |
| `GET` | `/classes/{class_id}/assignments/{assignment_id}` | Học sinh/Giáo viên có quyền đọc lớp → `200` (giáo viên nhận thêm `available_sources` khả dụng) |
| `POST` | `/classes/{class_id}/assignments/{assignment_id}/uploads` | Học sinh lớp `ACTIVE`; `multipart/form-data`: `file`, `expected_revision` → `201` |
| `POST` | `/classes/{class_id}/assignments/{assignment_id}/submissions` | Học sinh lớp `ACTIVE` + header `Idempotency-Key`; `{expected_revision, upload_id, operation_id, note?}` → `201` |
| `GET` | `/classes/{class_id}/assignments/{assignment_id}/submissions/reconcile` | Học sinh (`read_own_history`); `?operation_id=` → `200` |
| `GET` | `/submissions/{submission_id}/files/{file_id}` | Chủ bài nộp (`read_own_history`) hoặc giáo viên chấm (`grade_submitted_work`) → `200` (MIME chuẩn hóa theo tệp đã kiểm tra, fallback `application/octet-stream`) |
| `POST` | `/submissions/{submission_id}/grade` | Giáo viên chấm (`grade_submitted_work`); `{expected_grade_version, score, feedback?}` → `200` (ghi outbox `ASSIGNMENT_GRADED` sang Core) |
| `POST` | `/submissions/{submission_id}/grade-hint` | Giáo viên chấm (`grade_submitted_work`) → `200 {feedback, limitations}` (tư vấn AI `D5`, `503 AI_HINT_UNAVAILABLE` nếu AI gián đoạn) |

### 2.4. Chấm điểm, Sửa điểm, Điều chỉnh đề lỗi & Phúc khảo (L4, 11 endpoints)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `GET` | `/classes/{class_id}/grading` | Giáo viên phân công (`read_class_content`) → `200` (hàng chờ chấm, đánh dấu `membership_ended` qua Core) |
| `GET` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/grading` | Giáo viên phân công (`read_class_content`) → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/attempts/{attempt_id}/grade` | Giáo viên chấm (`grade_submitted_work`); `{expected_grade_version, scores, feedback?}` → `200` |
| `POST` | `/classes/{class_id}/assessments/{assessment_id}/release-results` | **Chỉ `PRIMARY`** lớp `ACTIVE`; `{reason?, release_now?: bool}` → `200` (công bố điểm & ghi outbox `ASSESSMENT_RESULT_RELEASED`) |
| `POST` | `/grades/{grade_id}/revisions` | Giáo viên (`grade_submitted_work`) hoặc `ADMIN` (bắt buộc `X-Admin-Reason`); `{expected_grade_version, scores?, total_score?, feedback?, reason, appeal_id?}` → `201` |
| `POST` | `/grade-revisions/{revision_id}/decision` | **Chỉ `PRIMARY`** (hoặc `ADMIN` kèm `X-Admin-Reason`, cấm tự duyệt đề xuất của mình); `{expected_grade_version, decision: "APPROVE"\|"REJECT", reason}` → `200` |
| `POST` | `/classes/{class_id}/activity-versions/{version_id}/scoring-adjustments` | Giáo viên lớp `ACTIVE`; `{question_id, action: "VOID_QUESTION"\|"GRANT_FULL_CREDIT"\|"ACCEPT_ADDITIONAL_KEY", payload?, reason}` → `201` (`M4`) |
| `POST` | `/scoring-adjustments/{adjustment_id}/decision` | **Chỉ `PRIMARY`** lớp `ACTIVE`; `{decision: "APPROVE"\|"REJECT", reason}` → `200` |
| `POST` | `/classes/{class_id}/appeals` | Học sinh (`read_own_history`); `{target_type, target_id, question_id?, reason}` → `201` (chỉ trong cửa sổ phúc khảo) |
| `POST` | `/classes/{class_id}/appeals/{appeal_id}/resolve` | Giáo viên (`grade_submitted_work`); `{resolution_note, linked_revision_id?}` → `200` (ghi outbox `APPEAL_RESOLVED`) |
| `GET` | `/history/results/{result_id}` | Học sinh (`read_own_history`) → `200` (`limited_history: true`, truy cập kết quả Assessment/Assignment cũ của chính mình) |

---

## 3. Internal Endpoints (`/internal/v1`)

| Method | Endpoint | Xác thực & Nghiệp vụ |
|---|---|---|
| `GET` | `/assessment-lock` | `LEARNING_INTERNAL_SERVICE_SECRET`; `?user_id=&school_id=` → `200 {user_id, school_id, active_official_assessment: bool}` (`D6`) |
| `POST` | `/quiz-items/import` | `LEARNING_INTERNAL_SERVICE_SECRET` + header `Idempotency-Key` khớp `body.idempotency_key`; `{class_id, school_id, idempotency_key, item, level, generated_by: "AI", approved_by, edited_after_generation, provenance, sources}` → `201 \| 200 QuizImportReceipt` |
| `GET` | `/classes/{class_id}/grades/confirmed` | `LEARNING_ANALYTICS_SERVICE_SECRET`; `?school_id=&since_version=&limit=&cursor=` → `200` (danh sách điểm chính thức đã xác nhận theo phân trang cho phân tích học tập) |

---

## 4. Hợp đồng Web Proxy Allowlist (`apps/web/src/lib/api/learning-operations.ts`)

Proxy `/api/learning/v1/[...path]/route.ts` mở allowlist cho **58 thao tác public** (18 Practice, 16 Assessment gồm màn grading, 14 Assignment, 10 Grading/Corrections/Appeals/History):
- Kiểm tra UUID chặt chẽ cho mọi ID đường dẫn (`questionId` là chuỗi an toàn 1..100 ký tự, cấm ký tự điều khiển và segment `.` / `..`).
- Chỉ forward các query parameter được khai báo (`kind`, `origin`, `type`, `topic`, `operation_id`).
- Chuyển tiếp header `Idempotency-Key`, `X-Admin-Reason`, `multipart/form-data` (upload bài tập), stream nhị phân tải tệp (`Content-Disposition`), và bắt buộc `Origin === WEB_ORIGIN` đối với mọi yêu cầu `POST`/`PUT`/`DELETE`.
