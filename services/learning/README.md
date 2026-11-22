# Lumi Learning Service (L0–L4)

Microservice độc lập quản lý **Practice Quiz**, **Official Assessment**, **Assignment**, **Grading & Grade Revisions**, **Appeals**, **Scoring Adjustments (M4)**, **D6 Assessment Lock** cho AI Service và **Deadline Sync** sang Core Calendar.

Xem đặc tả kiến trúc tại [DESIGN.md](../../docs/architect/microservices-v1/learning/DESIGN.md) và [API_CONTRACT.md](../../docs/architect/microservices-v1/learning/API_CONTRACT.md).

## Chạy trong workspace

```powershell
cd services/learning
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m app
```

Chạy worker nền (auto-submit sweeper + outbox deadline/notification dispatcher):

```powershell
python -m app.background_worker
```

## Chạy container

```powershell
docker compose -f services/learning/compose.yaml up --build
```

## Kiểm thử

```powershell
python -m pytest -q
```

Đặt `LEARNING_TEST_DATABASE_URL` trỏ tới PostgreSQL 16 test DB riêng để chạy thêm bộ test Alembic upgrade/check/downgrade & isolation trên schema `learning`.
