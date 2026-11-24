# AI Service API Contract v1

**Trạng thái:** `IMPLEMENTED` · **Tài liệu chi tiết triển khai:** [DESIGN.md](DESIGN.md) · [FOLDER_STRUCTURE.md](FOLDER_STRUCTURE.md) · [services/ai/API_CONTRACT.md](../../../../services/ai/API_CONTRACT.md)

---

## 1. Quy ước chung và Health

- **Health (không dùng prefix `/api/v1`):**
  - `GET /health/live` → `200 {"status": "live"}`
  - `GET /health/ready` → `200 {"status": "ready", "db": "connected", "scheduler": {"busy", "slots", "waiting", "wait_p95_seconds"}, "llama": "connected" | "disabled"}`; trả `503 SERVICE_UNAVAILABLE` khi schema `ai` chưa migrate hoặc khi `LLAMA_MODE` đang bật nhưng máy chủ LLM không phản hồi.
- **Public API (`/api/v1`):** trình duyệt truy cập thông qua Next.js proxy `/api/ai/v1/*` (`AI_API_URL`), gửi `Authorization: Bearer <access_jwt>`.
- **Internal API (`/internal/v1`):** yêu cầu `X-Service-Key` (`AI_INTERNAL_SERVICE_SECRET` hoặc `AI_ANALYTICS_SERVICE_SECRET` cho thống kê chủ đề), bị chặn ở Web proxy.
- **Headers & Error Envelope:** mọi phản hồi đều mang `X-Correlation-ID` (UUID) và `Cache-Control: no-store`. Lỗi theo chuẩn `{"error": {"code": "...", "message": "...", "correlation_id": "uuid"}}`, thông điệp tiếng Việt không chứa dữ liệu thô đầu vào hay thông tin bí mật.

---

## 2. Public Endpoints — AI Tutor (`/api/v1`)

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `GET` | `/classes/{class_id}/tutor/conversations` | Bearer `STUDENT` + Core `read_own_history`; `?limit=50` (`1..100`) → `200 list[ConversationSummary]` (mới nhất trước) |
| `POST` | `/classes/{class_id}/tutor/conversations` | Bearer `STUDENT` + Core `use_ai_tutor` (lớp `ACTIVE`) + Learning `D6` + Document `active-grants`; `{title, default_mode?}` (`QUICK_QA \| STEP_BY_STEP`) → `201 {id}` |
| `GET` | `/tutor/conversations/{conversation_id}/messages` | Chủ hội thoại (`conversation.user_id == actor.id`) + Core `read_own_history`; `?limit=50` (`1..100`) → `200 list[HistoryMessage]` (che claim/trích dẫn thuộc nguồn đã thu hồi) |
| `POST` | `/tutor/conversations/{conversation_id}/messages` | Chủ hội thoại + Core `use_ai_tutor` + Learning `D6`; `{operation?, mode?, text?, retry_of_message_id?}` → `200 text/event-stream` |
| `POST` | `/tutor/conversations/{conversation_id}/messages/{message_id}/stop` | Chủ hội thoại + Core `read_own_history` → `200 {"stopped": bool}` |
| `GET` | `/classes/{class_id}/tutor/config` | Bearer `LECTURER` + Core `participate_current_class` (lớp `ACTIVE`) → `200 ConfigRead` (`version: int \| null`) |
| `PUT` | `/classes/{class_id}/tutor/config` | Bearer `LECTURER` + Core `participate_current_class`; `{allow_general_knowledge, hint_reveal_after, allow_solution_reveal, guidance_level, max_answer_words, expected_version}` → `200 ConfigRead` (`409` nếu `expected_version` cũ) |

**Chi tiết luồng SSE (`POST /tutor/conversations/{conversation_id}/messages`):**
- `operation`: `ASK` (mặc định; `text` `1..2000` ký tự ở `QUICK_QA` và `1..600` ở `STEP_BY_STEP`), `CHECK` (`text` `1..500`, chỉ `STEP_BY_STEP`), `HINT` & `REVEAL` (`text` rỗng, chỉ `STEP_BY_STEP`, `REVEAL` phụ thuộc `allow_solution_reveal`).
- `retry_of_message_id`: chỉ hợp lệ với `operation = "ASK"`, trỏ tới tin nhắn `USER` cùng hội thoại để tạo lại câu trả lời không nhân đôi tin nhắn hỏi.
- Toàn bộ kiểm tra quyền, `D6` và hợp lệ dữ liệu chạy trước HTTP `200`. Luồng SSE phát `event: start` (`{"message_id": "..."}`) rồi `event: message` (DTO ở trạng thái `COMPLETED`, `STOPPED` hoặc `FAILED`), đồng thời xác minh lại phiên và grant trước khi lưu và phát kết quả.
- Mỗi phần tử citation chưa bị che trong `rendered.claims[].citations[]` mang `source_id`, `quote`, `source_spans`, `location` (nếu xác định được), cùng trường tùy chọn `document_id` (UUID) và `version_number` (int) để giao diện mở đúng tài liệu và vị trí trích dẫn.

---

## 3. Public Endpoints — AI Quiz Generator (`/api/v1/classes/{class_id}/quiz`)

Mọi endpoint dưới đây chỉ cho phép `LECTURER` đang được phân công tại lớp `ACTIVE` (`participate_current_class`).

| Method | Endpoint | Quyền / Input → Kết quả |
|---|---|---|
| `POST` | `/proposals` | `{purpose?, subject?, language?, scope, options?, total}` (`1..100`) → `200 list[MatrixCell]` (`section, type, level, count`) |
| `POST` | `/jobs` | `{purpose?, subject?, language?, scope, options?, matrix}` → `202 {"id": UUID, "status": "QUEUED"}` |
| `GET` | `/jobs` | `?limit=20` (`1..50`) → `200 list[JobView]` |
| `GET` | `/jobs/{job_id}` | `200 JobView` (`status, completed_items, total_items, error_code, created_at, updated_at`) |
| `GET` | `/jobs/{job_id}/items` | `200 list[DraftView]` (`item` trả `null` nếu nguồn không còn quyền truy cập) |
| `POST` | `/items/{draft_id}/review` | `{decision: "accept"\|"edit"\|"reject"\|"regenerate", reason?, edited_item?}` → `200 {"status": DraftStatus}` |
| `POST` | `/items/{draft_id}/regenerate` | Sinh lại ngay 1 slot đang ở `REGENERATE` (`409` nếu trạng thái khác) → `200 DraftView` |
| `POST` | `/items/{draft_id}/export` | Xuất draft đã duyệt (`ACCEPTED` / `EDITED_ACCEPTED` / `EXPORT_PENDING`) sang Learning qua `Idempotency-Key` → `200 QuizExportReceipt` (`503` giữ `EXPORT_PENDING` để worker retry) |

- Loại câu hỏi hỗ trợ: `single_choice`, `multiple_choice`, `true_false_4`, `short_answer`.
- Mức độ nhận thức: `biet`, `hieu`, `van_dung`. Mục đích: `PRACTICE` hoặc `ASSESSMENT_DRAFT`.

---

## 4. Internal Endpoints (`/internal/v1`)

| Method | Endpoint | Xác thực & Nghiệp vụ |
|---|---|---|
| `PUT` | `/document-versions/{document_version_id}/index` | `AI_INTERNAL_SERVICE_SECRET`; `{generation_id, expected_generation_id?, source_id, source_sha256}` → `204` (`409` nếu corpus chưa hoàn chỉnh hoặc xung đột generation) |
| `POST` | `/document-versions/{document_version_id}/index/failure` | `AI_INTERNAL_SERVICE_SECRET`; `{source_sha256}` → `204` (ghi nhận lỗi build nhưng không xóa bản `READY` trước đó) |
| `POST` | `/document-versions/{document_version_id}/grant-invalidation` | `AI_INTERNAL_SERVICE_SECRET` → `204` (xóa cache retriever của phiên bản tài liệu) |
| `POST` | `/classes/{class_id}/tutor/topic-summary` | `AI_ANALYTICS_SERVICE_SECRET`; `{school_id, rows: [{school_id, class_id, student_id, topic_id}]}` → `200 tuple[TopicCount]` (ẩn nhóm < 5 học sinh + complementary suppression) |
| `POST` | `/explain-mistake` | `AI_INTERNAL_SERVICE_SECRET`; `{school_id, class_id, grade_level, question, student_choice, correct_answer, misconception, approved_explanation, pinned_evidence}` → `200 {explanation, evidence, fallback}` |
| `POST` | `/assignment/grade-hint` | `AI_INTERNAL_SERVICE_SECRET`; `{school_id, class_id, assignment_title, instructions, submission_note}` → `200 {feedback, limitations}` (Rule `D5`: tự động từ chối `503` nếu phản hồi chứa điểm số hoặc LLM không khả dụng) |

---

## 5. Hợp đồng Web Proxy Allowlist (`apps/web/src/lib/api/ai-operations.ts`)

Proxy `/api/ai/v1/[...path]/route.ts` chỉ mở đúng **15 thao tác public** (`7` thao tác Tutor + `8` thao tác Quiz):
- Kiểm tra chặt định dạng UUID cho mọi path param (`classId`, `conversationId`, `messageId`, `jobId`, `draftId`), từ chối path traversal và chỉ forward query parameter `limit`.
- Với thao tác mutation (`POST`, `PUT`), bắt buộc `Origin === WEB_ORIGIN` (chống CSRF).
- Hỗ trợ streaming SSE nguyên trạng (`sendTutorMessage`) với timeout `190_000 ms`, không chuyển tiếp service keys ra trình duyệt.
