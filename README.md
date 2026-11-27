# RAG Course Assistant — Athena

Nền tảng trợ giảng và quản lý học tập sử dụng **Microservices trong một Monorepo**, phục vụ **học sinh THCS và THPT (khối 6–12)** trong **lớp học chính khóa**.

**Năm microservices theo kiến trúc:** `Core`, `Document`, `AI`, `Learning`, `Learner Intelligence`.

**Trạng thái triển khai (`2026-11-22`):**
- **Đã triển khai:** `Core` (`services/core`), `Document` (`services/document`), `AI` (`services/ai` cùng engine `services/rag`), `Learning` (`services/learning`) và giao diện Web (`apps/web`, Next.js 15 với 71 product routes), chạy trên PostgreSQL 16 (4 schema cô lập `core`, `document`, `ai`, `learning`) và Docker Compose (`compose.yaml` + `compose.full.yaml`).
- **Chưa triển khai / Đề xuất (`PROPOSED` / `DEFERRED`):** `Learner Intelligence` (`TASK-020` / G5), báo cáo tổng hợp PDF/Excel, và cơ chế thực thi `DELETE` / chính sách lưu giữ dữ liệu xuyên service (`OD-02`, `OD-03`).
- **Gate kiểm định còn mở:** đánh giá định lượng `QAS-06` (`>= 100` câu hỏi với model GGUF thật), bật end-to-end consumer tự động cho `document.version.published` → `/canonical` khi chạy với corpus/GGUF thật, kiểm thử trình duyệt hỗn hợp 4 service, và đo tải đầy đủ `TP-01` / `QAS-05`.

---

## Tổng quan các service

| Service | Thư mục & Stack | Cổng mặc định | DB Schema / Lưu trữ | Worker nền | Trạng thái | Tài liệu |
|---|---|---|---|---|---|---|
| **Core** | `services/core` (FastAPI, SQLAlchemy 2, Alembic 0001–0015) | `8000` | Schema `core` | `notification-worker` (`app.background_worker`) | `IMPLEMENTED` | [DESIGN](docs/architect/microservices-v1/core/DESIGN.md) · [API](docs/architect/microservices-v1/core/API_CONTRACT.md) · [STRUCTURE](docs/architect/microservices-v1/core/FOLDER_STRUCTURE.md) |
| **Document** | `services/document` (FastAPI, SQLAlchemy 2, Alembic 0001, `services/rag/idp`) | `8001` | Schema `document` + `document_data` | `document-worker` (`app.worker`) | `IMPLEMENTED` | [ADR-002](docs/architect/microservices-v1/ADR-002-document-service.md) · [DESIGN](docs/architect/microservices-v1/document/DESIGN.md) · [API](docs/architect/microservices-v1/document/API_CONTRACT.md) · [STRUCTURE](docs/architect/microservices-v1/document/FOLDER_STRUCTURE.md) |
| **AI** | `services/ai` (FastAPI, SQLAlchemy 2, Alembic 0001–0002, `services/rag`) | `8002` *(host)* / `8001` *(container)* | Schema `ai` | `ai-worker` (`app.background_worker`) | `IMPLEMENTED` | [DESIGN](docs/architect/microservices-v1/ai/DESIGN.md) · [API](docs/architect/microservices-v1/ai/API_CONTRACT.md) · [STRUCTURE](docs/architect/microservices-v1/ai/FOLDER_STRUCTURE.md) |
| **Learning** | `services/learning` (FastAPI, SQLAlchemy 2, Alembic 0001–0004) | `8003` | Schema `learning` + `learning_blobs` | `learning-worker` (`app.background_worker`) | `IMPLEMENTED` | [DESIGN](docs/architect/microservices-v1/learning/DESIGN.md) · [API](docs/architect/microservices-v1/learning/API_CONTRACT.md) · [STRUCTURE](docs/architect/microservices-v1/learning/FOLDER_STRUCTURE.md) |
| **Web** | `apps/web` (Next.js 15, React 19, TypeScript) | `3000` | BFF proxy (`/api/v1`, `/api/ai/v1`, `/api/learning/v1`) | — | `IMPLEMENTED` | [FRONTEND](apps/web/docs/FRONTEND.md) · [UI/UX Design](apps/web/docs/UI_UX_DESIGN.md) · [Đặc tả UI/UX](docs/architect/athena-design/13_UIUX.md) |
| **Learner Intelligence** | Đề xuất (`TASK-020` / G5) | — | Chưa triển khai | — | `PROPOSED` | [FRS Intelligence](docs/architect/athena-design/06_FRS_Intelligence.md) · [TASK-020](tasks/TASK-020-learner-intelligence-service.md) |

---

## Chạy nhanh

Chuẩn bị `.env` từ `.env.example` tại root (cấu hình `SECRET_KEY`, `INTERNAL_SERVICE_SECRET` tối thiểu 32 ký tự và các biến secret nội bộ), sau đó chạy:

- **Full-stack 4 backend services + 4 workers + Web + PostgreSQL 16:**
  ```powershell
  docker compose -f compose.yaml -f compose.full.yaml up --build -d --wait
  ```
- **Core + Document + Web + PostgreSQL 16 (nhẹ hơn):**
  ```powershell
  docker compose -f compose.yaml up --build -d --wait
  ```
- **Chỉ chạy Frontend ở chế độ Mock Preview (không cần Docker/DB):**
  ```powershell
  cd apps/web
  npm run dev:mock
  ```

---

## Đọc tài liệu

1. [Business Rules](docs/architect/BUSINESS_RULES.md) — nguồn chuẩn nghiệp vụ (ưu tiên cao nhất).
2. [ADR-001](docs/architect/microservices-v1/ADR-001.md), [ADR-002](docs/architect/microservices-v1/ADR-002-document-service.md), [Architecture](docs/architect/microservices-v1/ARCHITECTURE.md) — quyết định kiến trúc và ranh giới microservices.
3. [Hồ sơ yêu cầu & thiết kế Athena](docs/architect/athena-design/00_INDEX.md) — BRD, SRS, FRS, Use Cases, HLD/LLD, ERD/DFD, UI/UX (`13_UIUX.md`), Bảo mật, Vận hành và Kiểm thử.
4. [DeepDoc và RAG Engine](docs/deepdoc/README.md) — pipeline `services/rag/`, IDP, chunking, retrieval, citations và generation.
5. [Frontend Architecture](apps/web/docs/FRONTEND.md), [Local Live Testing](apps/web/docs/LOCAL_LIVE_TESTING.md), [UI/UX Refinement](apps/web/docs/UI_UX_REFINEMENT.md) và [Live UX Fixes](apps/web/docs/UX_LIVE_FIXES.md).
6. [Roadmap](docs/architect/microservices-v1/IMPLEMENTATION_ROADMAP.md), [Quality Attributes](docs/architect/microservices-v1/QUALITY_ATTRIBUTES.md), [Danh mục task](tasks/README.md).

---

## Phạm vi sản phẩm Athena

Athena phục vụ **học sinh THCS và THPT (khối 6–12)** trong **lớp học chính khóa**. Tiểu học, đại học và lớp học thêm không thuộc phạm vi MVP. Giao diện hướng đến học sinh phổ thông với phong cách sáng, gọn, không game hóa hoặc trang trí quá mức.

**Vai trò kỹ thuật & hiển thị:** giữ ba vai trò `ADMIN`, `LECTURER` (hiển thị **Giáo viên**) và `STUDENT` (hiển thị **Học sinh**). Nhà trường nhận báo cáo thông qua Admin, không có tài khoản riêng; không mặc định có vai trò Phụ huynh hay Admin theo trường. Admin tạo lớp môn học liên kết lớp hành chính, phân công giáo viên `PRIMARY`/`ASSISTING`, quản lý ghi danh `AUTO`/`MANUAL` và cấp username cùng mã kích hoạt một lần cho học sinh. Dữ liệu học tập cá nhân và lịch sử hội thoại được bảo vệ khi chuyển lớp, trong khi quyền truy cập nguồn tài liệu luôn kiểm tra tại thời điểm sử dụng (fail closed). Không suy ra tính năng đã đạt KPI/PG-full chỉ từ unit tests hoặc chế độ preview mock.
