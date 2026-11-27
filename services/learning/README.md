# Athena Learning Service (L0–L4)

Microservice độc lập quản lý **Practice Quiz**, **Official Assessment**, **Assignment**, **Grading & Grade Revisions**, **Appeals**, **Scoring Adjustments (M4)**, **D6 Assessment Lock** cho AI Service và **Deadline / Notification Sync** sang Core.

Xem bộ tài liệu kiến trúc tại [DESIGN.md](../../docs/architect/microservices-v1/learning/DESIGN.md), [API_CONTRACT.md](../../docs/architect/microservices-v1/learning/API_CONTRACT.md) và [FOLDER_STRUCTURE.md](../../docs/architect/microservices-v1/learning/FOLDER_STRUCTURE.md).

## Cấu hình và Schema

- **Schema PostgreSQL:** `learning` với 4 migrations (`0001_initialize_learning_schema` → `0004_grades_appeals_adjustments`), quản lý 18 bảng qua `app.model_registry`.
- **Cổng mặc định:** `8003` (`/health/live`, `/health/ready`, `/docs`).
- **Biến môi trường chính:** `LEARNING_DATABASE_URL`, `CORE_API_URL`, `CORE_JWT_SECRET`, `CORE_SERVICE_KEY`, `LEARNING_INTERNAL_SERVICE_SECRET`, `LEARNING_ANALYTICS_SERVICE_SECRET`, `AI_MODE` (`disabled`/`http`), `AI_API_URL`, `AI_SERVICE_KEY`, `DOCUMENT_MODE` (`disabled`/`http`/`local_dev`), `DOCUMENT_API_URL`, `DOCUMENT_SERVICE_KEY`, `BLOB_STORAGE_DIR` (mặc định `/var/lib/learning-blobs` trong Compose), `ASSIGNMENT_MAX_UPLOAD_BYTES` (25 MiB), `DEFAULT_APPEAL_WINDOW_DAYS` (7), `TRANSFER_PENDING_ATTEMPT_POLICY`, `SWEEPER_INTERVAL_SECONDS` (10).

## Chạy trong workspace

```powershell
cd services/learning
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m app
```

Chạy worker nền (auto-submit Assessment hết giờ + outbox deadline/notification dispatcher + dọn tệp upload hết hạn):

```powershell
python -m app.background_worker
```

## Chạy container

Chạy riêng Learning service:

```powershell
docker compose -f services/learning/compose.yaml up --build
```

Hoặc chạy cùng toàn bộ stack tại thư mục gốc repository:

```powershell
docker compose -f compose.yaml -f compose.full.yaml up --build -d --wait
```

## Kiểm thử

```powershell
python -m pytest -q
```

Đặt `LEARNING_TEST_DATABASE_URL` trỏ tới PostgreSQL 16 test DB riêng để chạy thêm bộ test Alembic upgrade/check/downgrade & isolation trên schema `learning`.
