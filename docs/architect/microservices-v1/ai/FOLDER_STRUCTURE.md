# Quy ước tổ chức mã nguồn AI Service

**Trạng thái:** `IMPLEMENTED` · **Cập nhật:** 2026-11-22

**Phạm vi:** `services/ai/` trong monorepo; chạy cùng build context `./services` để sử dụng thư viện `services/rag/`. AI Service có tiến trình API, worker nền, Dockerfile, migration Alembic và schema PostgreSQL `ai` độc lập.

---

## 1. Cây thư mục thực tế (`services/ai/`)

```text
services/ai/
├── app/
│   ├── __init__.py
│   ├── __main__.py                    # Entry point uvicorn (python -m app), cổng container 8001
│   ├── main.py                        # Khởi tạo FastAPI, lifespan (DB recovery, RagRuntime, HTTP clients), CORS, routers
│   ├── background_worker.py           # Worker nền: phục hồi stale state, chạy quiz_jobs QUEUED, retry EXPORT_PENDING
│   ├── config.py                      # Settings (AI_DATABASE_URL, CORE_*, DOCUMENT_*, LEARNING_*, LLAMA_*, AI_*)
│   ├── model_registry.py              # Composition root đăng ký ORM models thuộc schema 'ai' cho Alembic
│   ├── common/
│   │   ├── api.py                     # Dependency injection (Actor, Credentials, ApiServices, Services)
│   │   ├── auth.py                    # Giải mã JWT + verify qua Core; verify X-Service-Key (internal & analytics)
│   │   ├── class_access.py            # Kiểm tra quyền lớp cho Teacher (participate_current_class)
│   │   ├── db.py                      # Engine, SessionLocal, Base(schema='ai'), transaction(session)
│   │   ├── errors.py                  # ErrorEnvelope tiếng Việt, CorrelationMiddleware, UnexpectedErrorMiddleware
│   │   └── logging.py                 # Logger sự kiện an toàn, không in nội dung hội thoại hay token
│   ├── integrations/
│   │   ├── http.py                    # Helper gọi HTTP nội bộ + header X-Service-Key / X-Correlation-ID
│   │   ├── core_client.py             # Client gọi Core (/users/me, /classes/{id}, /internal/v1/classes/{id}/access)
│   │   ├── document_client.py         # Client Document (active-grants, canonical_artifact, locate) + LocalDevDocumentClient
│   │   └── learning_client.py         # Client Learning (/internal/v1/assessment-lock, /internal/v1/quiz-items/import)
│   ├── runtime/
│   │   ├── models.py                  # Bảng generation_provenance (chỉ hash/metrics, không lưu raw text)
│   │   ├── executor.py                # Executor bất đồng bộ cho tác vụ CPU/DB blocking
│   │   ├── provenance.py              # Lưu vết sinh nội dung có kiểm soát
│   │   └── rag_runtime.py             # Khởi tạo GroundedTaskPipeline, CancelRegistry, bộ đếm & llama health probe
│   └── modules/
│       ├── health/                    # GET /health/live, GET /health/ready (DB + scheduler + llama)
│       ├── indexing/                  # Quản lý chỉ mục phiên bản tài liệu & retriever cache
│       │   ├── models.py              # IndexedDocumentVersion
│       │   ├── corpora.py             # Nạp corpus READY / đọc nguồn active có lọc grant
│       │   ├── service.py             # publish_generation, record_failed_build, invalidate_grants
│       │   └── router.py              # Internal indexing API
│       ├── tutor/                     # AI Tutor (QUICK_QA, STEP_BY_STEP), TutorConfig & internal Learning hints
│       │   ├── models.py              # TutorConfigRow, Conversation, Message
│       │   ├── service.py             # Quyền lịch sử, che nguồn thu hồi (mask_revoked), condensed history, stop
│       │   ├── generation.py          # Chạy pipeline sinh câu trả lời và locate trích dẫn
│       │   ├── canonical_fallback.py  # Trích xuất/truy hồi trên bản Canonical khi chưa có index cục bộ
│       │   ├── streaming.py           # SSE streaming lifecycle & persistence
│       │   ├── router.py              # Public Tutor API (/api/v1)
│       │   └── learning_internal.py   # Internal Learning API (/explain-mistake, /assignment/grade-hint)
│       ├── quiz/                      # Sinh đề xuất ma trận, job sinh câu hỏi, review & export sang Learning
│       │   ├── models.py              # QuizJob, QuizItemDraft
│       │   ├── service.py             # propose_quiz, enqueue_job, run_queued_job, review/regenerate/export
│       │   └── router.py              # Public Quiz API (/api/v1/classes/{class_id}/quiz/*)
│       └── analytics_export/          # Thống kê chủ đề hỏi đáp ẩn danh nhóm nhỏ
│           ├── service.py             # summarize_topics (ẩn ô < 5 học sinh + complementary suppression)
│           └── router.py              # POST /internal/v1/classes/{class_id}/tutor/topic-summary
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 0001_initialize_ai_schema.py
│       └── 0002_quiz_draft_slot_meta.py
├── tests/                             # Unit, policy, Postgres migration & cross-service client tests
├── alembic.ini
├── compose.yaml
├── Dockerfile                         # Context ./services, copy cả rag/ và ai/app, non-root UID 10002
├── Dockerfile.dockerignore
├── pyproject.toml
├── API_CONTRACT.md
└── README.md
```

---

## 2. Quy tắc biên module và phụ thuộc

1. **Cô lập cơ sở dữ liệu:** Mọi bảng thuộc schema `ai`. Không đọc ghi trực tiếp vào bảng của Core, Document hoặc Learning; mọi kiểm tra phiên, quyền lớp, tài liệu được chia sẻ và khóa Assessment (`D6`) đều đi qua HTTP adapter trong `app/integrations/`.
2. **Tách biệt engine `services/rag/` và service HTTP `services/ai/`:** `services/rag/` chứa logic xử lý ngôn ngữ, chunking, retrieval, citation verification và prompt contracts thuần Python; `services/ai/` quản lý xác thực người dùng, kiểm tra quyền liên service, trạng thái hội thoại, job queue và giao diện HTTP/SSE.
3. **Không rò rỉ dữ liệu riêng tư:** Không ghi nội dung tin nhắn, prompt hay đoạn trích dẫn vào `generation_provenance`, audit hay log; luôn thực thi `mask_revoked` trước khi trả lịch sử cho người dùng hoặc đưa vào ngữ cảnh condense lượt kế tiếp.
