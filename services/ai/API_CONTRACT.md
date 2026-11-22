# Hợp đồng AI · Giai đoạn 1

> **Tài liệu kiến trúc đồng bộ:** xem thêm [docs/architect/microservices-v1/ai/API_CONTRACT.md](../../docs/architect/microservices-v1/ai/API_CONTRACT.md), [DESIGN.md](../../docs/architect/microservices-v1/ai/DESIGN.md) và [FOLDER_STRUCTURE.md](../../docs/architect/microservices-v1/ai/FOLDER_STRUCTURE.md).

## HTTP

| Method / path | Kết quả |
|---|---|
| `GET /health/live` | `200`, `{"status":"live"}`. |
| `GET /health/ready` | `200` với `status: "ready"`, `db: "connected"`, `scheduler` gồm `busy`, `slots`, `waiting`, `wait_p95_seconds`, và `llama` là `"connected"` hoặc `"disabled"`. `503 SERVICE_UNAVAILABLE` khi thiếu migration DB hoặc llama-server đã bật trả lỗi, timeout hay chưa sẵn sàng. Mode `disabled` dùng để khởi động khung service. |

`X-Correlation-ID` là UUID trên mọi phản hồi HTTP. Service cấp UUID mới nếu header vào thiếu hoặc sai định dạng. Lỗi từ handler có dạng `{"error":{"code":"…","message":"…","correlation_id":"…"}}`, với thông báo tiếng Việt, không đính kèm đầu vào lỗi hay nội dung upstream.

## Kiểm tra phụ thuộc

- **Core.** Bearer JWT HS256 hợp lệ về chữ ký và thời hạn vẫn phải qua `GET /api/v1/users/me` với cùng token để Core kiểm tra phiên và trạng thái tài khoản. Quyền lấy từ vai trò hiện hành. `GET /api/v1/classes/{id}` trả metadata lớp. `GET /internal/v1/classes/{id}/access` dùng `X-Service-Key` và đối chiếu user/lớp/trường/action. Tutor dùng `use_ai_tutor`, `read_own_history`, `read_document_source`. Cấu hình và bản nháp quiz cần giáo viên được phân công, lớp ACTIVE, qua `participate_current_class`.
- **Document (`services/document` + `TASK-017F`).** `HttpDocumentClient.active_grants` gọi `GET /internal/v1/classes/{id}/documents/active-grants?school_id=…` (`X-Service-Key`) và hỗ trợ cả hai định dạng wire đã duyệt: schema chuẩn của `services/document` (`ActiveGrantsResponse`: `{class_id, items:[{document_id, school_id, title, format, serving_version_id, serving_version_number, canonical_schema_version, canonical_sha256}]}` — xác thực nghiêm `class_id` và từng `item.school_id`, ánh xạ `serving_version_id → document_version_id/source_id`) lẫn `GrantSnapshot` legacy (`{class_id, school_id, grants:[{document_id, document_version_id, source_id}]}`). `source_id` định danh nguồn bất biến của một phiên bản. Per-call grants là nguồn xác minh quyền. Locate dùng `POST` tới path nội bộ cấu hình qua `DOCUMENT_LOCATE_PATH`, nhận `{class_id, school_id, document_version_id, source_id, source_spans}`. Phản hồi lặp lại scope và định danh nguồn kèm `location` có `document_name`, và tùy nguồn có `page`, `slide` hoặc `section`. `source_spans` là mảng cặp offset `[start,end]` theo văn bản chuẩn hóa. Local dev dùng fixture có scope chính xác.
- **Learning, hợp đồng adapter chờ rà soát bởi owner TASK-019.** D6 dùng GET tới path đã cấu hình với `user_id`, `school_id`, kiểm tra mọi Assessment chính thức đang làm của học sinh trong trường. Phản hồi phải có cùng định danh và boolean `active_official_assessment`. `true` trả `403`, còn thiếu cấu hình, timeout, trạng thái không xác minh được hoặc body sai trả `503`. Export dùng POST tới path đã cấu hình và `Idempotency-Key` bằng UUID của draft đã duyệt. Learning trả `200` hoặc `201` với `class_id`, `school_id`, `idempotency_key`, `learning_item_id`. D1 yêu cầu giáo viên duyệt bản nháp. Learning giữ quyền quản lý assessment chính thức.

## API Tutor

Các đường dẫn sau bắt đầu bằng `/api/v1` và dùng Bearer từ Core.

| Method / path | Request và kết quả |
|---|---|
| `POST /classes/{class_id}/tutor/conversations` | `{title,default_mode?}` → `201 {id}`. Mode mặc định là `QUICK_QA`, mode còn lại là `STEP_BY_STEP`. Cần quyền hỏi AI và kết quả kiểm tra D6. |
| `GET /classes/{class_id}/tutor/conversations` | `200` danh sách hội thoại của chính học sinh theo lớp, mới nhất trước. `limit` mặc định 50, tối đa 100. |
| `GET /tutor/conversations/{conversation_id}/messages` | `200` tối đa `limit` tin nhắn gần nhất theo thứ tự thời gian. Che claim và nguồn đã mất quyền. StepSession chỉ lưu phía server. |
| `POST /tutor/conversations/{conversation_id}/messages` | `{operation?,mode?,text?,retry_of_message_id?}` → `200 text/event-stream`. `ASK` mặc định, giới hạn 2000 ký tự ở QUICK_QA và 600 ở STEP_BY_STEP. `CHECK` nhận tối đa 500 ký tự. `HINT` và `REVEAL` dùng phiên STEP_BY_STEP hiện tại, body để trống trường `text`. `REVEAL` cần TutorConfig cho phép. Hội thoại nhiều lượt tự động gọi `qa.condense` và `qa.route` với lịch sử đã che quote từ nguồn bị thu hồi; gửi `retry_of_message_id` (chỉ hợp lệ với `operation: "ASK"`) trỏ tới tin nhắn `USER` trước đó trong cùng hội thoại để tạo lại phản hồi không trùng lặp tin nhắn hỏi. Mode mặc định lấy từ hội thoại. |
| `POST /tutor/conversations/{conversation_id}/messages/{message_id}/stop` | `200 {stopped}`. Giá trị `true` nghĩa là vừa chuyển tin nhắn đang chạy sang STOPPED. |
| `GET /classes/{class_id}/tutor/config` | `200` cho giáo viên được phân công. Trả trường của TutorConfig và `version`; bản mặc định có `version: null`. |
| `PUT /classes/{class_id}/tutor/config` | `{allow_general_knowledge,hint_reveal_after,allow_solution_reveal,guidance_level,max_answer_words,expected_version}` → `200` cấu hình đã lưu. Gửi `expected_version: null` khi tạo lần đầu. Phiên bản cũ trả `409`. |

SSE dùng `event: start` với `{"message_id":"…" }` và `event: message` với DTO tin nhắn ở trạng thái COMPLETED, STOPPED hoặc FAILED (với `rendered.step.state` gồm `PLANNED`, `HINT`, `IN_PROGRESS`, `SOLVED`, `REVEALED`). Lỗi quyền, D6, dữ liệu hoặc phụ thuộc khi tiếp nhận trả ErrorEnvelope trước HTTP 200. Sau sự kiện start, kết quả được kiểm tra phiên, quyền và grant lại trước khi lưu và phát. Stop ghi STOPPED có điều kiện và báo hủy cho producer; lệnh llama.cpp đang chạy có thể tiếp tục đến khi trả về hoặc hết timeout. Tin nhắn `STREAMING` mồ côi quá `AI_STALE_OPERATION_SECONDS` được phục hồi về `FAILED` khi khởi động dịch vụ hoặc trong chu kỳ worker nền.

## API Quiz

Các đường dẫn bắt đầu bằng `/api/v1/classes/{class_id}/quiz` và chỉ dành cho giáo viên được phân công trong lớp ACTIVE. Grade lấy từ Core. Nội dung nguồn lấy từ corpus đã công bố và grant hiện hành. Bản nháp chỉ dùng được sau khi giáo viên duyệt theo D1.

| Method / path | Request và kết quả |
|---|---|
| `POST /proposals` | `{scope,total,purpose?,subject?,language?,options?}` → `200` ma trận đề xuất P1 gồm `section,type,level,count`. |
| `GET /jobs` | `limit` (1..50, mặc định 20) → `200` danh sách các job của lớp, mới nhất trước. |
| `POST /jobs` | Cùng payload với proposal, thay `total` bằng `matrix` → `202 {id,status:"QUEUED"}`. Job cũng được hàng đợi nền `ai-worker` nhận xử lý lại sau khởi động hoặc lỗi tạm thời. |
| `GET /jobs/{job_id}` | `200` với trạng thái, tiến độ và mã lỗi công khai nếu có. |
| `GET /jobs/{job_id}/items` | `200` danh sách draft. `item` được đặt thành `null` khi nguồn không còn quyền. |
| `POST /items/{draft_id}/review` | `{decision,reason?,edited_item?}` → `200 {status}`. Quyết định gồm `accept`, `edit`, `reject`, `regenerate`. |
| `POST /items/{draft_id}/regenerate` | Sinh lại ngay lập tức đúng một slot draft đang ở trạng thái `REGENERATE` (sau khi review `regenerate`; trạng thái khác trả `409`) theo ưu tiên P1, xóa `reviewer_id`/`review_reason` và cập nhật metadata slot. |
| `POST /items/{draft_id}/export` | `200` receipt Learning cho draft đã duyệt. Nếu Learning lỗi, trả `503`, draft giữ EXPORT_PENDING để API hoặc `ai-worker` retry với cùng khóa `Idempotency-Key`. |

`scope` là mảng `{document_version_id,section_paths?}`; bỏ `section_paths` để dùng cả phiên bản. `purpose` nhận PRACTICE hoặc ASSESSMENT_DRAFT. Các loại item gồm `single_choice`, `multiple_choice`, `true_false_4`, `short_answer`. Mức nhận `biet`, `hieu`, `van_dung`. Tổng số câu mỗi đề tối đa 100. Worker nền `python -m app.background_worker` thu hồi job `RUNNING` treo, chạy lại job `QUEUED` và retry `EXPORT_PENDING`.

## API nội bộ của AI

| Method / path | Request và kết quả |
|---|---|
| `PUT /internal/v1/document-versions/{id}/index` | Key `AI_INTERNAL_SERVICE_SECRET`. `{generation_id,expected_generation_id?,source_id,source_sha256}` → `204` nếu corpus hoàn chỉnh tại `AI_INDEX_ROOT/<generation_id>`. Corpus lỗi hoặc generation cạnh tranh trả `409`. |
| `POST /internal/v1/document-versions/{id}/index/failure` | Cùng key, body `{source_sha256}` → `204`. Bản READY trước đó vẫn được phục vụ. |
| `POST /internal/v1/document-versions/{id}/grant-invalidation` | Cùng key → `204`. Loại retriever đã cache; việc xác thực grant luôn đi qua Document. |
| `POST /internal/v1/classes/{class_id}/tutor/topic-summary` | Key riêng `AI_ANALYTICS_SERVICE_SECRET`. `{school_id,rows:[{school_id,class_id,student_id,topic_id}]}` → `200` mảng `{topic_id,students}` sau khi ẩn nhóm nhỏ. Caller nội bộ cung cấp snapshot chuẩn hóa của một lớp; API không nhận nội dung chat, không giữ snapshot. |
| `POST /internal/v1/explain-mistake` | Key `AI_INTERNAL_SERVICE_SECRET`. `{school_id,class_id,grade_level,question,student_choice,correct_answer,misconception,approved_explanation,pinned_evidence}` → `200 {explanation,evidence,fallback}`. |
| `POST /internal/v1/assignment/grade-hint` | Cùng key. `{school_id,class_id,assignment_title,instructions,submission_note}` → `200 {feedback,limitations}` chạy qua `GroundedTaskPipeline` (ưu tiên `P1_TEACHER`) trả hướng dẫn tư vấn cho giáo viên; tự động từ chối `503` nếu llama bị tắt, hàng đợi bận hoặc phản hồi chứa điểm số (Rule D5). Giáo viên quyết định điểm chính thức. |

Các route dành cho Learning nhận `school_id` và `class_id` dạng UUID, xác thực qua
`X-Service-Key` và từ chối trường ngoài hợp đồng, kể cả `student_id`. Learning loại bỏ thông tin
định danh học sinh khỏi nội dung gửi đi, kiểm tra quyền
người dùng và chỉ gọi giải thích sau khi học sinh đã nộp câu trả lời Practice. `grade_level`
nhận số nguyên từ 6 đến 12 hoặc `null`. Mỗi phần tử của `pinned_evidence` tham chiếu `source_id` và
`source_spans` dạng `[{start,end,source_id?}]`, có thể kèm `document_id` và
`document_version_id`. Offset tính theo ký tự trong văn bản chuẩn hóa, với `end` là cận loại
trừ. AI kiểm tra grant của lớp và dùng đúng các đoạn đã ghim trong corpus đã công bố.
Nếu llama bị tắt, hàng đợi bận, tạo nội dung lỗi hoặc nguồn ghim không dùng được, AI trả
`{explanation: approved_explanation hoặc null, evidence: [], fallback: true}`. Route gợi ý chấm
bài chỉ trả hướng dẫn đối chiếu bài nộp và giới hạn của ghi chú.

Các route của AI có OpenAPI tại `/docs`. **Hiện trạng tích hợp (`2026-11-22`):** `services/core`, `services/document`, `services/learning` và `apps/web` đều đã hiện hữu trong monorepo. Adapter Learning (`D6`, `export`, `explain-mistake`, `grade-hint`) đã kết nối HTTP qua `LEARNING_MODE=http`; `HttpDocumentClient` đã hỗ trợ schema `ActiveGrantsResponse` thực tế của `services/document` (trong `compose.full.yaml`, mặc định `DOCUMENT_MODE=disabled` giữ cô lập cho đến khi consumer `document.version.published` → `canonical` indexing và `DOCUMENT_LOCATE_PATH` được bật runtime). Đánh giá định lượng `QAS-06` (`>= 100` câu hỏi với model GGUF thật) đang chờ bước đo thực tế. Khi thiếu Learning, thao tác cần D6 hoặc xuất quiz trả `503`. Khi thiếu Document, Tutor và Quiz trả `503` cho thao tác tạo nội dung theo nguồn. Chủ hội thoại vẫn đọc được lịch sử với nguồn được che.

## Dữ liệu thuộc AI

| Bảng | Ràng buộc |
|---|---|
| `ai.tutor_configs` | Một bản ghi mỗi lớp, giữ trường và người cập nhật. Kiểm tra phiên bản khi ghi, trả xung đột nếu bản ghi đã đổi. |
| `ai.conversations`, `ai.messages` | Hội thoại thuộc người dùng, lớp và trường. Retry chỉ trỏ tới message trong cùng hội thoại. StepSession lưu phía server. DTO lịch sử kiểm tra nguồn hiện hành và che claim/quote/location khi nguồn mất quyền. Admin hoặc giáo viên không được đọc chat riêng tư của người khác. |
| `ai.generation_provenance` | Hash SHA-256, định danh task, seed, số lần thử, trạng thái, mã và số liệu thời gian/token. Giá trị đi qua bộ lọc mã cố định; không giữ nguyên văn prompt, response hoặc quote. |
| `ai.indexed_document_versions` | Giai đoạn 1 dùng một nguồn văn bản chuẩn hóa cho một document version. Mở thử corpus bằng retriever và so SHA-256 của nguồn trước khi đổi con trỏ DB. So sánh với generation trước để ngăn build cũ ghi đè. Lỗi build giữ bản READY trước đó. Cache chỉ giữ đối tượng retriever, quyền tài liệu lấy trực tiếp từ Document. |
| `ai.quiz_jobs`, `ai.quiz_item_drafts` | Hàng đợi lưu trạng thái, tiến độ, cache fact và draft. P1 dùng cho đề xuất đồng bộ, P2 cho entry point xử lý job. Review dùng trạng thái của engine. Export giữ EXPORT_PENDING để retry với cùng idempotency key rồi chuyển EXPORTED sau receipt. |

Toàn bộ khóa ngoại nằm trong schema `ai`. Alembic dùng `ai.alembic_version` và chỉ so sánh schema `ai`. Thống kê chủ đề chỉ nhận định danh chủ đề đã chuẩn hóa và số học sinh phân biệt trong một lớp/trường, ẩn ô dưới 5 và các ô có thể làm lộ nhóm giao nhỏ. Chưa công bố tổng lớp hoặc bộ lọc tùy ý.
