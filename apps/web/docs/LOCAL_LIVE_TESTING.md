# Local live testing

For the subsequent dashboard, roster, identity and navigation corrections, see
[Live UX corrections](UX_LIVE_FIXES.md). The current Core class read responses
include the additive display fields documented in the Core API contract.

Prepared on 2026-11-15. Open http://localhost:3000/login. This instance uses the
live adapter, the same-origin Next.js API proxy, Core on port 8000 and PostgreSQL
on port 55432. Synthetic records are persisted in the real database.

## Local profile

- Compose project: `lumi_w3_local`; volume: `lumi_w3_local_pgdata`.
- Database: `dev_lumi_local`; school: `Lumi Local Test School`.
- Root `.env` and `apps/web/.env.local` hold local configuration and are ignored.
- Credentials: `demo-ui/local-live/credentials.json` (ignored; keep private).
- Admin: `admin@seed.example.com`, password key `admin_password`.
- Teachers: `local.teacher01@example.com` through `local.teacher08@example.com`,
  password key `teacher_password`.
- Students: `local.hs6.01` through `local.hs12.20` (20 per grade), password key
  `student_password`.

The original six-account development seed is retained. Its generated teacher and
student passwords are discarded by the existing seed; use the accounts above.

## Data and evidence

Imported 140 activated students, eight activated teachers, four courses, seven
homerooms, 28 AUTO subject classes, 168 posts, 336 comments and 168 calendar events.
Including the existing baseline seed, PostgreSQL contains 154 users, 42 subject
classes and 14 homerooms. Each new class has 20 students and PRIMARY/ASSISTING
assignments. Dates cover past, current and upcoming events.

Core migration completed. The identity tool uses normal invitation redemption
and student activation service operations inside a transaction, scoped to
`dev_lumi_local`, and authenticates the seed administrator. It sends no email.
Subsequent classroom/social/calendar imports use the real authenticated HTTP API;
notification delivery uses the existing background worker.

Verified live login and profile retrieval for ADMIN, LECTURER and STUDENT through
the web proxy; an HttpOnly refresh cookie was received. The sample grade-six
student sees four classes and an inbox with 28 unread notifications. The identity
seed was rerun successfully without creating duplicates. Ruff lint/format passed
for the new Identity tool. Browser interaction and the complete backend test
suite were not run for this data import.

## Restart and reuse

Start Docker Desktop, then run from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File demo-ui/local-live/start.ps1
```

The starter uses the existing live production build; run `npm.cmd run build` in
`apps/web` after changing application code. Data survives container restarts.

Local reusable tools are in the ignored `demo-ui/local-live` folder:
`provision.py` initializes the baseline and synthetic identities, and
`import-content.py` imports content with a local operation checkpoint.
Keep `import-state.json` with its database; do not reuse it for another database
or delete it and rerun against the populated database. HTTP checkpointing cannot
guarantee exactly-once delivery if a response is lost after a successful commit.

The reusable Identity entry point is
`python -m app.modules.identity.local_test_data`, run from `services/core` with
the dedicated database configured. Supply a JSON object on stdin with
`admin_password`, `student_password`, and `teacher_password`; never put passwords
in command-line arguments or commit them.

## Full 4-service Docker Compose stack (`2026-11-22`)

For multi-service live testing across **Core, Document, AI, Learning, and Web**
(still keeping **Learner Intelligence** unavailable/deferred), start the combined
Compose stack from the repository root:

```bash
docker compose -f compose.yaml -f compose.full.yaml up --build -d --wait
```

Default host ports in the combined Compose stack:
- `db` (PostgreSQL 16): `127.0.0.1:5432`
- `core`: `127.0.0.1:8000`
- `document`: `127.0.0.1:8001`
- `ai`: `127.0.0.1:8002` (maps to container port `8001`)
- `learning`: `127.0.0.1:8003`
- `web`: `127.0.0.1:3000`

In local live runs without `AI_API_URL` or `LEARNING_API_URL` configured (e.g., Core-only
`demo-ui/local-live/start.ps1`), the live adapter preserves strict `503` service
isolation for unstarted backends without falling back to preview fixtures.
