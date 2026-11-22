# Quy ước tổ chức mã nguồn Document Service

**Trạng thái:** `P0/P1 IMPLEMENTED — VERIFIED` (triển khai theo [TASK-017B](../../../../tasks/TASK-017B-document-service-foundation.md), [TASK-017C](../../../../tasks/TASK-017C-document-idp-worker-and-review.md) và [TASK-017E](../../../../tasks/TASK-017E-compose-demo-and-deployment.md)).

> [!NOTE]
> **Cập nhật cấu trúc thực tế (`services/document/`):** Ngoài khung baseline ban đầu bên dưới, mã nguồn hiện tại đã triển khai đầy đủ `app/bootstrap.py`, `app/model_registry.py`, `app/common/types.py`, `app/modules/notes/public.py`, `app/modules/versions/processing.py`, `docker/init-document-db.sh`, `Dockerfile`, `Dockerfile.dockerignore`, `README.md`, `tests/test_packaging_contract.py`, script kiểm thử `scripts/verify_compose_e2e.py` và hướng dẫn vận hành tại [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md).
**Phạm vi:** `services/document/` trong **một monorepo**; Document là một FastAPI microservice độc lập cùng worker nền chung image/schema.

---

## 1. Cây thư mục mục tiêu

```text
services/document/                     # Microservice Document độc lập
├── app/                               # Mã Python chạy trong Document API & Worker
│   ├── __init__.py
│   ├── main.py                        # Khởi tạo FastAPI, CORS, error handler, correlation ID
│   ├── bootstrap.py                   # Đăng ký toàn bộ router & dependency overrides
│   ├── worker.py                      # Worker nền: claim PENDING IDP jobs & outbox events
│   ├── model_registry.py              # Metadata composition root dùng cho Alembic
│   ├── common/                        # Hạ tầng dùng chung nội bộ service Document
│   │   ├── config.py                  # Settings: DATABASE_URL, CORE_API_URL, INTERNAL_SERVICE_SECRET, MAX_SIZE_BYTES, MAX_PAGES
│   │   ├── db.py                      # Engine, session, transaction(session), Base schema='document'
│   │   ├── errors.py                  # ErrorEnvelope chuẩn giống Core, localization thông điệp
│   │   ├── pagination.py              # Cursor UUID pagination (limit 1..100)
│   │   ├── auth.py                    # Dependency xác thực qua Core /internal/v1/auth/introspect (fail closed)
│   │   ├── core_client.py             # HTTP client gọi Core /internal/v1/auth/introspect và /classes/{id}/access
│   │   ├── storage.py                 # StorageBackend abstraction (P1 LocalVolumeStorage; P2 GCS placeholder)
│   │   ├── audit.py                   # AuditEvent + allowlist metadata cho Document
│   │   └── outbox.py                  # OutboxEvent + record_outbox_event trong cùng transaction
│   └── modules/                       # Tách theo miền nghiệp vụ nội bộ
│       ├── health/                    # /health/live và /health/ready (kiểm tra DB + StorageBackend)
│       ├── library/                   # Document metadata, thư viện cá nhân, GET /documents/policy, upload validation
│       │   ├── models.py              # Document
│       │   ├── repository.py
│       │   ├── schemas.py
│       │   ├── service.py
│       │   ├── validation.py          # Magic-byte, ZIP-bomb, encrypted PDF, page count <= 300, size <= 20 MB
│       │   ├── public.py              # Interface nội bộ cho grants/notes/versions
│       │   └── router.py
│       ├── versions/                  # DocumentVersion, state machine, accept/retry, Canonical reader projection
│       │   ├── models.py              # DocumentVersion
│       │   ├── repository.py
│       │   ├── schemas.py
│       │   ├── service.py             # serving_version / is_superseded dẫn xuất; accept NEEDS_REVIEW->SUCCESS
│       │   ├── idp_runner.py          # Wrapper gọi services/rag/idp.ingest + lưu Canonical artifact
│       │   ├── public.py
│       │   └── router.py
│       ├── grants/                    # ClassDocumentGrant, kiểm tra Core manage_class_documents & read_document_source
│       │   ├── models.py              # ClassDocumentGrant
│       │   ├── repository.py
│       │   ├── service.py
│       │   ├── schemas.py
│       │   ├── public.py              # Kiểm tra active grant hiện hành cho notes/reader/download/internal API
│       │   └── router.py              # Public grants + /internal/v1/.../active-grants & .../canonical
│       └── notes/                     # SourceBoundNote và DocumentBookmark (H6, L6, O2)
│           ├── models.py
│           ├── repository.py
│           ├── service.py             # Che source_quote khi mất quyền hiện hành
│           ├── schemas.py
│           └── router.py
├── migrations/                        # Alembic migrations cho schema 'document' (độc lập với Core)
│   ├── env.py
│   └── versions/
├── tests/                             # Unit + integration + contract tests
│   ├── conftest.py
│   ├── test_policy_and_upload.py
│   ├── test_versions_idp_review.py
│   ├── test_grants_and_access.py
│   ├── test_notes_bookmarks.py
│   └── test_internal_contract.py
├── alembic.ini
├── Dockerfile                         # Multi-stage Python 3.12 non-root, kèm poppler-utils/tesseract/pandoc cho IDP
└── pyproject.toml                     # Dependencies, ruff, pytest
```

---

## 2. Quy tắc biên module và phụ thuộc

1. **Tách biệt hoàn toàn với Core DB:** Document chỉ kết nối schema `document` bằng credential riêng; mọi truy vấn `users` hoặc `subject_classes` đều thực hiện qua REST nội bộ Core (`core_client.py`).
2. **Giao tiếp nội bộ qua `public.py`:** `grants` không import `library/repository.py` hay `versions/repository.py`; `notes` không tự query `class_document_grants` mà gọi `grants.public`.
3. **Không đưa chunking/embedding/BM25 vào `services/document/`:** `versions/idp_runner.py` chỉ gọi tầng `idp` để tạo Canonical artifact và trích xuất outline/page projection cho Document Reader.
4. **Hạ tầng fail closed:** mọi lỗi từ `core_client.py` (timeout, connection error, HTTP 5xx) đều làm dừng thao tác nhạy cảm và trả `503 SERVICE_UNAVAILABLE`.
