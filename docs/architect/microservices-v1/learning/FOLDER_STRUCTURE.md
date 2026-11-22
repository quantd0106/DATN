# Quy ước tổ chức mã nguồn Learning Service

**Trạng thái:** `IMPLEMENTED` · **Cập nhật:** 2026-11-22

**Phạm vi:** `services/learning/` trong monorepo; Learning là một FastAPI microservice độc lập cùng worker nền chung image và schema PostgreSQL `learning`.

---

## 1. Cây thư mục thực tế (`services/learning/`)

```text
services/learning/
├── app/
│   ├── __init__.py
│   ├── __main__.py                    # Entry point uvicorn (python -m app), cổng 8003
│   ├── main.py                        # Khởi tạo FastAPI, lifespan (DB ping, HTTP clients, LocalFsBlobStore), CORS, routers
│   ├── background_worker.py           # Worker nền: sweep hết hạn Assessment, phát outbox Core, dọn upload hết hạn
│   ├── config.py                      # Settings (LEARNING_DATABASE_URL, CORE_*, AI_*, DOCUMENT_*, BLOB_STORAGE_DIR, ...)
│   ├── model_registry.py              # Composition root đăng ký toàn bộ 18 bảng ORM schema 'learning' cho Alembic
│   ├── common/
│   │   ├── access.py                  # Kiểm tra quyền lớp (active teacher/student, PRIMARY, grade_submitted_work, own_history, X-Admin-Reason)
│   │   ├── audit.py                   # Bảng audit_events & record_audit_event (không lưu đáp án thô hay chat)
│   │   ├── auth.py                    # Giải mã JWT + verify qua Core; verify X-Service-Key (internal & analytics)
│   │   ├── db.py                      # Engine, Base(schema='learning'), write_transaction (hỗ trợ SQLite & Postgres)
│   │   ├── errors.py                  # ErrorEnvelope, AttemptClosedError, PUBLIC_MESSAGES tiếng Việt
│   │   ├── idempotency.py             # Bảng idempotency_records & kiểm tra payload SHA-256
│   │   ├── logging.py                 # Logger sự kiện an toàn
│   │   ├── outbox.py                  # Bảng outbox_events & helper ghi sự kiện deadline/thông báo
│   │   ├── pagination.py              # Cursor UUID pagination
│   │   └── routes.py                  # Dependency Actor/DbSession, helper check_operation_header & get_grants
│   ├── integrations/
│   │   ├── http.py                    # HTTP client nội bộ an toàn (X-Service-Key, correlation ID)
│   │   ├── core_client.py             # Client gọi Core (session, access, learning-deadlines, learning-events)
│   │   ├── document_client.py         # Client gọi Document (/active-grants) + LocalDevDocumentClient
│   │   └── ai_client.py               # Client gọi AI (/explain-mistake, /assignment/grade-hint)
│   ├── storage/
│   │   ├── __init__.py
│   │   └── blob_store.py              # Abstraction BlobStore & LocalFsBlobStore (/var/lib/learning-blobs)
│   └── modules/
│       ├── health/                    # GET /health/live, GET /health/ready (kiểm tra Alembic head)
│       ├── bank/                      # Ngân hàng câu hỏi, hoạt động, phiên bản bất biến & bộ chấm tự động
│       │   ├── models.py              # QuestionItem, Activity, ActivityVersion, ActivityAudience, DueOverride
│       │   ├── activities.py          # Helper tạo/quản lý phiên bản & rà soát nguồn tài liệu
│       │   ├── grading_engine.py      # Bộ chấm tự động single/multiple/true_false/short_answer
│       │   └── service.py             # Import câu hỏi AI (idempotent) & xuất truy vấn danh mục hoạt động
│       ├── practice/                  # Luyện tập L1 (tách biệt khỏi điểm chính thức D8)
│       ├── assessment/                # Đánh giá chính thức L2 (timer, immutable version, submit/reconcile, auto-submit)
│       ├── assignment/                # Bài tập nộp tệp L3 (upload, submit, due overrides, download, grade-hint)
│       ├── grading/                   # Chấm điểm, đề xuất/duyệt sửa điểm, điều chỉnh đề lỗi M4
│       ├── appeals/                   # Phúc khảo L4 & xem lịch sử kết quả sau chuyển lớp
│       └── internal/                  # API nội bộ (/assessment-lock, /quiz-items/import, /grades/confirmed)
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_initialize_learning_schema.py
│       ├── 0002_assessment_attempts.py
│       ├── 0003_assignments_uploads.py
│       └── 0004_grades_appeals_adjustments.py
├── tests/                             # Suite unit, concurrency QAS-03, wire integration & Postgres Alembic tests
├── alembic.ini
├── compose.yaml
├── Dockerfile                         # Non-root UID 10003
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 2. Quy tắc biên module và phụ thuộc

1. **Cô lập cơ sở dữ liệu:** Mọi bảng thuộc schema `learning`. Không import model hay truy vấn SQL trực tiếp sang schema `core`, `document` hoặc `ai`.
2. **Tách biệt Luyện tập và Điểm chính thức (`D8`):** `practice` không bao giờ ghi dữ liệu vào bảng `grades` hoặc `grade_revisions`.
3. **Giao tiếp liên service qua port:** Mọi lời gọi sang Core, Document và AI đều đi qua các port/adapter trong `app/integrations/`; mọi sự kiện bất đồng bộ (`calendar.deadline.upsert`, `ASSESSMENT_RESULT_RELEASED`, `GRADE_PROPOSAL_PENDING`, `APPEAL_RESOLVED`) ghi vào `learning.outbox_events` trong cùng transaction nghiệp vụ để `background_worker` chuyển phát an toàn.
4. **Tính bất biến của phiên bản đề (`I4`, `O3`):** Sau khi `activity_versions.status == "PUBLISHED"`, không cho phép sửa nội dung câu hỏi trên phiên bản đó; mọi thay đổi quy tắc chấm câu hỏi lỗi phải đi qua `grading/ScoringAdjustment` (`M4`).
