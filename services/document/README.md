# Document Service

Run these commands from `services/document` with Python 3.12 or newer.

```powershell
python -m pip install -e '.[dev]'
python -m ruff check app tests migrations
python -m ruff format --check app tests migrations
python -m pytest tests/ -W error
```

The default tests use disposable SQLite databases with schema translation. To run the
same suite on PostgreSQL 16, set `TEST_DATABASE_URL` to an existing disposable database
with permission to create schemas. The fixture requires the database to have no
`document` schema, applies the full Alembic migration, and removes the test schema
after the run.

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://tester@localhost:5432/document_test'
python -m pytest tests/ -W error
```

To start the API, set `DATABASE_URL`, `CORE_API_URL`, `INTERNAL_SERVICE_SECRET`, and
`DOCUMENT_STORAGE_PATH` for the environment. Core must implement TASK-017A introspection
and class access. `CORS_ORIGINS` accepts a JSON array. The default upload limits are
20 MiB and 300 PDF pages or PPTX slides.

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app
```

Run the worker from the same environment and with the same database and storage
settings as the API. The worker uses the Monorepo's `services/rag/idp` package when
`idp` is absent from the Python import path. Set `IDP_PACKAGE_PATH` to the directory
containing `idp` to override that fallback. The runtime dependencies include OpenCV
and a bundled Pandoc for DOCX parsing.

```powershell
python -m app.worker
python -m app.worker --once
```

The worker processes `PENDING` versions, stores Canonical artifacts, and relays
outbox events. The owner can accept a `NEEDS_REVIEW` version or retry a `FAILED`
version. Set `DOCUMENT_WORKER_LEASE_SECONDS`, `DOCUMENT_WORKER_MAX_ATTEMPTS`, or
`DOCUMENT_WORKER_POLL_SECONDS` to change the defaults of 300 seconds, 3 attempts,
and 1 second. The worker also accepts `--poll-seconds`.

The library's status filter applies to the latest uploaded version. A class continues
to use the latest successful version until a newer version succeeds. When access is
lost, reader and personal-history responses use a generic document title and retain
the user's own note text. Validation errors put the upload failure reason in
`error.details.reason`, and delete conflicts include the affected classes in
`error.details`.

### Verify a live Compose stack

Start a disposable stack and run Core `seed-dev` as described in the
[defense demo runbook](../../docs/architect/microservices-v1/document/DEMO_RUNBOOK.md).
From `services/document`, set `CORE_SEED_ADMIN_PASSWORD`, `INTERNAL_SERVICE_SECRET`
and `DOCUMENT_DB_PASSWORD` to the stack's values, then run:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_compose_e2e.py `
  --web-url http://127.0.0.1:13000 --origin http://localhost:13000 `
  --compose-project lumi-doc-demo --db-name dev_rag_course
```

Use the actual Compose project name and match `--origin` to Web's `WEB_ORIGIN`
and Core's CORS configuration. The suite creates synthetic users, classes and
documents. With no lecturer credentials supplied, it uses the seeded staff
password-reset flow on the disposable database. Set `E2E_LECTURER_PASSWORD` and
`E2E_ASSISTING_PASSWORD` to use existing passwords. Pass `--skip-docker-faults`
to omit the worker kill and Document stop checks. The DB role check still runs
when Docker is available.
