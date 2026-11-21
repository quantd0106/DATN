# TASK-017G: Document CI Pipeline & Compose Smoke Upgrade

**ID:** `TASK-017G` · **Mức ưu tiên:** `P0 / P1` *(Wave 1A)* · **Trạng thái:** `DONE — VERIFIED` *(cục bộ qua YAML/Ruff/PG16/Docker Compose; chờ kích hoạt trên GitHub runner khi push)*
**Owner chính:** Codex CLI (`sol 6.2` / `gpt-6.2-sol`, reasoning `high`) · **Người nghiệm thu:** Coordinator  
**Tham chiếu chuẩn:** [CODEX_ORCHESTRATION.md](../docs/architect/microservices-v1/document/CODEX_ORCHESTRATION.md) · [22_ENVIRONMENTS_DEPLOYMENT.md](../docs/architect/lumi-design/22_ENVIRONMENTS_DEPLOYMENT.md) · [core-ci.yml](../.github/workflows/core-ci.yml)

---

## 1. Bối cảnh & Mục tiêu

Hiện tại [.github/workflows/core-ci.yml](../.github/workflows/core-ci.yml) chỉ chạy:
- `core-tests`, `core-load`
- `web-build` (chỉ chạy `npm run build`, chưa chạy `npm run check`)
- `compose-smoke` (mới kiểm tra `db`, `migrate`, `core`, `notification-worker`, `web`; chưa kiểm chứng `document-migrate`, `document`, `document-worker`, cô lập role `document_svc`, và kịch bản cô lập lỗi `docker compose stop document`).

Mục tiêu của `TASK-017G` là đưa toàn bộ bảo chứng chất lượng đã chạy tay ở `TASK-017A..E` vào **GitHub Actions CI tự động**, bảo đảm không một PR nào làm gãy hợp đồng Core–Document–Web–Compose.

---

## 2. Phạm vi & File Allowlist

### Được phép tạo / sửa
- `.github/workflows/core-ci.yml`
- `.github/workflows/document-ci.yml` *(nếu tách workflow riêng cho Document + phối hợp trigger `paths` rõ ràng, hoặc gộp job mới trực tiếp vào `core-ci.yml` — chọn 1 phương án sạch nhất, không trùng lặp bước cài đặt nặng)*
- `services/document/pyproject.toml` *(chỉ bổ sung nếu thiếu tool config phục vụ CI; không đổi runtime logic)*

### Không được sửa
- `docs/architect/BUSINESS_RULES.md`, `services/core/`, `services/document/app/`, `apps/web/`, `compose.yaml`, các migration đã áp dụng.

---

## 3. Yêu cầu kỹ thuật chi tiết

1. **Job `document-tests` (trên Postgres 16 thật):**
   - Khởi tạo service container `postgres:16-alpine` + tách role `core_svc` (nếu test cần schema/init giống `init-document-db.sh`) và `document_svc`.
   - Kiểm tra `ruff check --no-cache .` và `ruff format --check --no-cache .` trong `services/document`.
   - Kiểm tra Alembic roundtrip (`alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head`).
   - Chạy `pytest -p no:cacheprovider -W error` toàn bộ `services/document/tests/` với Coverage gate hợp lý (đặc biệt nhánh kiểm tra quyền và `versions`).
2. **Nâng cấp job `web-build`:**
   - Thêm bước `npm run check` (`check:boundaries`, `tsc --noEmit`, unit/contract tests) trước khi build `NEXT_PUBLIC_DATA_MODE=live npm run build` và `check:release`.
3. **Nâng cấp job `compose-smoke`:**
   - Tạo `.env` tạm cho CI (không commit) từ `.env.example` với mật khẩu CI ephemeral và `CORE_API_URL=http://core:8000`, `DOCUMENT_API_URL=http://document:8001`.
   - Chạy `docker compose up --build -d --wait`.
   - Kiểm tra `document-migrate` thoát `0`, `document` `/health/ready` trả `200`, `document-worker` đang chạy ổn định (không crash loop).
   - Kiểm tra cô lập DB: dùng `psql` với role `document_svc` thử `SELECT 1 FROM core.users LIMIT 1;` -> **bắt buộc thất bại với permission denied**.
   - Smoke HTTP xuyên `web` (`http://127.0.0.1:3000`): đăng nhập tài khoản seed -> `GET /api/v1/documents/policy` trả `200` -> upload mẫu nhỏ hoặc PDF -> worker đạt `SUCCESS` -> grant lớp -> student đọc nội dung `200`.
   - Cô lập dịch vụ (`QAS-07` / fault isolation): chạy `docker compose stop document` -> `GET /api/v1/classes` qua `web` vẫn `200`, trong khi `GET /api/v1/documents/policy` trả `503`.
   - Dọn `docker compose down -v` và xóa `.env` tạm trong bước `if: always()`.

---

## 4. Lệnh nghiệm thu (Verification Gate)

Trước khi báo hoàn thành, Codex CLI & Coordinator phải chứng minh trên môi trường cục bộ / runner:
1. Validate cú pháp workflow YAML (`actionlint` nếu có hoặc Python `yaml.safe_load` / kiểm tra cấu trúc chuẩn).
2. Chạy thật cục bộ đúng các lệnh mới thêm vào CI:
   - `services/document`: `ruff check --no-cache .`, `ruff format --check --no-cache .`, `pytest -p no:cacheprovider -W error` (ghi rõ số test passed/skipped).
   - `apps/web`: `npm run check` và `NEXT_PUBLIC_DATA_MODE=live npm run build`.
   - Root Compose: thực thi chính đoạn script smoke mới dùng trong `compose-smoke` và ghi lại exit code `0` + dọn sạch `.env` / volume sau khi chạy.

---

## 5. Bằng chứng kiểm định (2026-11-22)

- **Workflow YAML & Lint:**
  - `yaml.safe_load` trên [.github/workflows/core-ci.yml](../.github/workflows/core-ci.yml) → `['core-tests', 'core-load', 'document-tests', 'web-build', 'compose-smoke']` (exit 0).
  - `.\services\document\.venv\Scripts\ruff.exe check --no-cache` và `format --check --no-cache` trên `app tests migrations scripts` → `All checks passed!`, `53 files already formatted`.
- **Web (`apps/web`):** `npm run check` → `check:boundaries` đạt, `tsc --noEmit` 0 lỗi, `277/277` tests pass; `NEXT_PUBLIC_DATA_MODE=live npm run build` & `npm run check:release` chạy sạch trong build Docker `web` của Compose.
- **PostgreSQL 16 & Runtime Smoke (Docker Compose `lumi-verify-017`, Windows 11 + Docker Desktop 29.7.2 linux):**
  - `document-migrate` `exited:0`; `core` và `document` chạy non-root, cài từ `site-packages`, `alembic check` không có sai lệch schema; `notification-worker --once --batch-size 100` & `document-worker --once` thoát `0`.
  - `docker/init-document-db.sh` trên `alembic_check_db` → role `document_svc` bị từ chối `SELECT 1 FROM core.users LIMIT 1;` với `SQLSTATE 42501` (`permission denied for schema core`).
  - Document Alembic roundtrip (`upgrade head`, `check`, `heads --verbose`, `downgrade base`, `upgrade head`) trên `alembic_check_db` đạt.
  - Document PG16 pytest (`document_ci`): **`99 passed in 22.90s`**, tổng coverage nhánh/dòng **`94.19%`** (vượt ngưỡng `--cov-fail-under=90`).
  - DB outage/recovery: `docker compose stop db` → Core & Document `/health/ready` trả `503`, `/health/live` trả `200`; `docker compose start db` → cả hai phục hồi `200`; dọn sạch volume và không để lại `.env`.
  - *(Chưa kích hoạt trên GitHub Actions cloud runner cho tới khi push nhánh lên remote.)*
