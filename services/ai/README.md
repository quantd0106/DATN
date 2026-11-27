# Athena AI · Giai đoạn 1

Service FastAPI độc lập cho Tutor, bản nháp câu hỏi AI, index dẫn xuất và tổng hợp chủ đề. Xem OpenAPI ở `/docs` trên cổng container `8001` (ánh xạ ra host cổng `8002` khi chạy chung `compose.yaml` + `compose.full.yaml` để không trùng cổng `8001` của Document) và bộ tài liệu: [API_CONTRACT.md](API_CONTRACT.md), [Architecture Design](../../docs/architect/microservices-v1/ai/DESIGN.md), [Architecture API Contract](../../docs/architect/microservices-v1/ai/API_CONTRACT.md), [Folder Structure](../../docs/architect/microservices-v1/ai/FOLDER_STRUCTURE.md). Core, Document và Learning được kết nối qua HTTP adapter.

## Chạy trong workspace

`services/rag` cung cấp scheduler, pipeline, các task và trình đọc corpus. Giữ engine này cạnh `services/ai` trong cùng checkout hoặc build context. Docker chỉ lấy mã Python cần thiết của engine, không lấy môi trường ảo, dữ liệu mẫu hay model.

Cấu hình `AI_DATABASE_URL` trỏ tới PostgreSQL dùng tài khoản riêng của AI. Tài khoản chạy migration cần sở hữu schema `ai` đã được cấp trước hoặc có quyền tạo schema. Tài khoản runtime cần quyền sử dụng schema và các bảng AI, bao gồm quyền đọc `ai.alembic_version`. Có thể cấp riêng URL cho hai tiến trình khi vận hành.

```powershell
cd services/ai
python -m pip install -e ".[dev,rag]"
$env:PYTHONPATH = (Resolve-Path ..).Path
python -m alembic upgrade head
python -m app
```

`python -m app` chạy một Uvicorn worker, tắt access log và lọc log theo danh sách sự kiện cố định. Khi khởi động với PostgreSQL, dịch vụ phục hồi các tin nhắn `STREAMING` và job `RUNNING` bị treo quá `AI_STALE_OPERATION_SECONDS`. Worker nền `python -m app.background_worker` (hoặc `--once`) chạy chu kỳ thu hồi trạng thái treo, xử lý job quiz `QUEUED` và thử xuất lại draft `EXPORT_PENDING` sang Learning. Mỗi tiến trình chỉ có một scheduler và pipeline dùng chung.

Schema dùng hai migration `0001_ai` và `0002_ai_quiz_slot_meta`.

## Chạy container

Từ thư mục gốc, sao chép `services/ai/.env.example` thành `services/ai/.env` rồi điền URL DB mà container truy cập được. Ví dụ trên Docker Desktop, DB chạy trên máy host có thể dùng tên host `host.docker.internal`. Khởi động bằng lệnh sau.

```powershell
docker compose -f services/ai/compose.yaml up --build
```

Compose chạy migration trước tiến trình HTTP (`ai`) và worker nền (`ai-worker`). Các container chạy với UID `10002`, root filesystem chỉ đọc và tmpfs tại `/tmp`. Với `LLAMA_MODE=local`, llama.cpp phải chạy trong cùng network namespace với AI. Gắn model và executable ở chế độ chỉ đọc cho bước xác minh artifact. Job xây index cần một volume riêng, có quyền ghi và nằm ngoài root filesystem.

## Cấu hình

| Nhóm | Biến môi trường |
|---|---|
| PostgreSQL | `AI_DATABASE_URL` dạng `postgresql+psycopg://...`, `AI_DATABASE_POOL_SIZE`, `AI_DATABASE_MAX_OVERFLOW`, `AI_DATABASE_POOL_TIMEOUT_SECONDS`, `AI_DATABASE_CONNECT_TIMEOUT_SECONDS` |
| HTTP | `AI_CORS_ORIGINS` là mảng JSON các origin cụ thể. `AI_DEPENDENCY_TIMEOUT_SECONDS` đặt timeout client. |
| Core | `CORE_API_URL`, `CORE_JWT_SECRET` cùng khóa HS256 với Core, `CORE_SERVICE_KEY` tương ứng `INTERNAL_SERVICE_SECRET` tại Core. |
| AI nội bộ | `AI_INTERNAL_SERVICE_SECRET` xác thực header `X-Service-Key` trên các route indexing. Analytics dùng `AI_ANALYTICS_SERVICE_SECRET` riêng. |
| Document | `DOCUMENT_MODE` nhận `disabled`, `http`, `local_dev`. `DOCUMENT_API_URL`, `DOCUMENT_SERVICE_KEY`, `DOCUMENT_LOCATE_PATH`. Chỉ `AI_ENVIRONMENT=local_dev` mới nhận fixture Document có scope trường/lớp cụ thể. |
| Learning | `LEARNING_MODE` nhận `disabled` hoặc `http`. `LEARNING_API_URL`, `LEARNING_SERVICE_KEY`, `LEARNING_ACTIVE_ASSESSMENT_PATH`, `LEARNING_QUIZ_EXPORT_PATH`. |
| llama.cpp / LLM | `LLAMA_MODE` nhận `disabled`, `local` hoặc `openai_compatible`. Chế độ `local` dùng `LLAMA_URL` (loopback HTTP có cổng), `LLAMA_MODEL_PATH`, `LLAMA_SERVER_PATH`, `LLAMA_MODEL_REPO`, `LLAMA_MODEL_REVISION`, `LLAMA_MODEL_VARIANT`, `LLAMA_MODEL_SHA256`, `LLAMA_CONTEXT_TOKENS`, `LLAMA_OUTPUT_CEILING`, `LLAMA_TIMEOUT_SECONDS`. Chế độ `openai_compatible` dùng `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL`. |
| Corpus và embedding | `AI_INDEX_ROOT` trỏ đến thư mục có các generation hoàn chỉnh. `AI_EMBEDDING_MODEL_PATH`, `AI_EMBEDDING_TOKENIZER_PATH`, `AI_EMBEDDING_REPO`, `AI_EMBEDDING_REVISION`, `AI_EMBEDDING_VARIANT`, `AI_EMBEDDING_THREADS`, `AI_EMBEDDING_MAX_TOKENS` phải khớp profile encoder của corpus. |
| Tác vụ nền | `AI_RAG_WORKERS` giới hạn thread thực thi. `AI_RAG_ADMISSION_SECONDS` là thời gian đợi thread. `AI_WORKER_POLL_SECONDS` đặt chu kỳ nghỉ worker. `AI_STALE_OPERATION_SECONDS` đặt ngưỡng phục hồi `STREAMING`/`RUNNING`. llama.cpp dùng một slot P0/P1/P2 chung. |

## Kiểm thử và CI

Chạy `python -m pytest` từ `services/ai` (`39 passed, 8 skipped` khi chưa đặt PostgreSQL URL, Ruff clean trên 58 tệp). Đặt thêm `AI_TEST_DATABASE_URL` để chạy kiểm thử PostgreSQL. URL đó phải trỏ vào **DB test riêng, chưa có schema `ai`**. Kiểm thử tạo và dọn schema của fixture, chạy Alembic upgrade/check/downgrade và kiểm tra schema lân cận. CI tổng của repository nằm tại [core-ci.yml](../../.github/workflows/core-ci.yml).
