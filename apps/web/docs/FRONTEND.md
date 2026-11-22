# Frontend Architecture — Lumi Web

## Current implementation

The frontend implements the 71 product routes in `src/config/routes.ts`. There are 72 App Router `page.tsx` files because `app/page.tsx` is the root session redirect.

The shared shell (`src/components/shell.tsx`, `src/components/route-screen.tsx`) owns role navigation, breadcrumbs, the current class header/tabs, reader/focused layouts, session/access boundaries, mobile navigation and dialog focus. Feature entries (`src/features/*`) own Identity, accounts/imports, Classroom administration, classroom home/history, Social/calendar, notifications, Document/notes, Tutor, Quiz authoring, Practice, official Assessment, assignments/grading and Intelligence. See [UI_UX_DESIGN.md](UI_UX_DESIGN.md) for the complete Vietnamese UI/UX design system, layout tokens, and 71-route specification.

In **live** mode, Core, Document, AI (`tutor` + Quiz generator) and Learning (Practice, Assessment, Assignment, Grading/Correction, Appeals, Calendar deadline projection) are wired to their respective backend services through same-origin BFF proxies. Only **Learner Intelligence** (`s.progress`, `t.analytics`, `a.reports`, `a.exports`) and unresolved Core admin capabilities (`a.dataRequests` review adapter, `a.legacy` username conversion UI) remain hidden or explicitly unavailable in `live` mode (`src/lib/route-availability.ts`, `src/lib/data/integrations.ts`).

Development preview (`npm run dev:mock`) implements in-memory preview interactions across all 71 routes. Full document reload resets that adapter; same-document navigation and explicit sign-out/sign-in retain the preview's shared business store. Local drafts are scoped to actor/resource/version and are volatile. No browser preview certifies durable service persistence, real AI/GGUF quality, email delivery or school production readiness.

Access tokens stay in memory. Refresh cookies remain `HttpOnly` under `/api/v1/auth`, with one refresh in flight and no blanket mutation replay. Auth return links preserve only allowed source/page/conversation/filter context, discard secrets and unknown queries, and retain role checks. Server permission decisions remain authoritative.

Staff invitation and password-reset emails link to `/staff/invitation` or `/reset-password` with a one-time token in the URL fragment. The client reads the fragment and removes it from browser history before submission. The existing one-time-code field remains available as a fallback. Student activation and recovery stay Admin-issued codes and are not emailed.

## Shared presentation contract

`src/styles/lumi-foundation.css` is the canonical source for shared design tokens, typography, buttons, form controls, notices, shell, native-dialog surfaces and authentication layouts. Root layout (`app/layout.tsx`) imports it after `app/globals.css`; feature styles own their scoped layouts. Lumi blue (`#2563eb`) and teal (`#0d9488`) remain the main and Tutor accents. Class navigation retains its compact summary and underline selection; official attempts retain `FocusedTaskLayout`.

Controls use visible keyboard focus and native disabled semantics. Radio/checkbox controls stay separate from text-input styling. Account-type and calendar-view selectors are groups of pressed buttons (`aria-pressed`). The skip link targets `main-content` on authentication, session/access boundaries, loading and error pages as well as authenticated shells. Wide tables and calendars scroll locally; page overflow must not be hidden to mask missing responsive layout.

Feed mutation controls require the matching current active class membership or teaching assignment in the loaded class summary; missing context and archived classes are read-only in the UI. Core still authorizes every request. Saved-note/bookmark links retain the captured version and page, and note editing preserves those locators. Dirty note edits use the existing unsaved-change guard; note deletion is confirmed and operation errors stay inside the active dialog.

Route and UI refinements are tracked in [UI_UX_REFINEMENT.md](UI_UX_REFINEMENT.md) and [UX_LIVE_FIXES.md](UX_LIVE_FIXES.md).

## Performance and state safeguards

- **Resource-scoped loading & cancellation (`src/lib/route-load-plan.ts`, `src/components/route-screen.tsx`):** `resourceParamsKey` separates resource identity (`classId`, `version`, `conversationId`, `compose`) from UI position/filter parameters so changing a Reader page or Tutor filter does not re-fetch the entire workspace; stale fetches are cancelled via `AbortController`.
- **Feature-owned reads (`featureOwnsRead`):** 41 domain-owned routes load directly inside their feature entry rather than issuing duplicate parent workspace reads; `ClassSummaryProvider` shares the authenticated class summary with the shell header.
- **Debounced search & URL synchronization:** Account search updates local input immediately and syncs URL parameters after 200 ms using history replacement while preserving IME composition and Back/Forward behavior.
- **Serialized Practice & Assessment saves (`src/features/learning/lib/save-controller.ts`, `src/features/assessment/answer-save.ts`):** Saves serialize revisions, coalesce pending short-answer edits (200 ms debounce), flush immediately on explicit save/submit, use monotonic server-anchored exam clocks, and retain a single `operationId` + `Idempotency-Key` with `/reconcile` lookup on unknown outcomes.
- **Cross-service fault isolation & session fence (`src/lib/data/learning-wire.ts`):** `optionalServiceRead` isolates non-`401` outages from Document/AI/Learning without falling back to preview fixtures, while strictly propagating `401` and checking `sessionGeneration()` across async awaits.

## One data source, no fallback

`src/lib/data/port.ts` is the frontend port. Only `src/lib/data/index.ts` imports `@data-adapter`. `next.config.mjs` binds that alias to `src/lib/data/live.ts` for live builds, or `dev/mocks/adapter.ts` for explicit development preview. TypeScript checks both against the same port through an ambient declaration; `tsconfig.json` must not map this alias to a concrete adapter, because that overrides runtime selection. Preview compiles into `.next-preview`; live uses `.next` to keep artifacts separate. Product pages never import fixtures and never call `fetch` directly.

`NEXT_PUBLIC_DATA_MODE` accepts only `live` or `mock`; missing means `live`. `mock` is rejected for production build and start. There is no per-feature override, query/localStorage toggle, catch-to-fixture branch or hybrid mode. Fixture data lives exclusively in `dev/mocks`; mocks are excluded from the live dependency graph. `npm run check:boundaries` checks imports/network/storage. `npm run check:release` scans `.next/static` and `.next/server` for the fixture canary; run `npm run build` for the complete release gate.

## Backend boundary

The browser uses same-origin Next.js BFF proxies (`src/lib/api/proxy.ts`):
- `/api/v1/*`: routes explicitly allowlisted Core operations (`CORE_API_URL`) and Document operations (`DOCUMENT_API_URL`, preserving multipart uploads and streaming file downloads without buffering sensitive credentials to the browser).
- `/api/ai/v1/*`: routes 15 allowlisted AI operations (`AI_API_URL`), piping SSE streams (190 s stream timeout) and rejecting redirects/CSRF origins.
- `/api/learning/v1/*`: routes 57 allowlisted Learning operations (`LEARNING_API_URL`), preserving `Idempotency-Key`, `X-Admin-Reason`, multipart uploads, and binary submission downloads.

Proxy handlers never forward service keys, fail closed when upstream origins are misconfigured or cross-origin mutations lack matching `WEB_ORIGIN`, and preserve upstream `401`/`403`/`404`/`409`/`503` semantics. `src/lib/data/integrations.ts` records operation-level contracts across Core, Document, AI (`tutor`), Learning, and Learner Intelligence.

Live adapters validate response DTOs strictly and never fall back to preview fixtures (`dev/mocks`). In `src/lib/data/live.ts`, workspace loaders compose cross-service live data with strict fault isolation (`optionalServiceRead`):
- `s.tutor` (`live-ai.ts`) deep-links valid `document_id` citations to `s.reader` (`?page=...`, falling back to `s.documents` when only `source_id` is available) and enriches `classHasSources` + `quickNotes` from Document (`live-document.ts`) while propagating `401` and tolerating Document `503`.
- `s.history` loads read-only AI conversations together with personal notes for that class.
- `s.home`, `t.home`, `s.overview`, and `t.overview` (`live-dashboard.ts`) enrich Core classroom workspaces with live Document `recentMaterials` and Learning `upcomingActivities`, `upcomingTasks`, `recentStudies`, `tutorSuggestions`, and `teacherMetrics` (`pendingGrading` / `openActivities`), leaving unavailable widgets/metrics empty or unknown (`undefined`) rather than fabricating counts when Document or Learning returns `503` (while always propagating `401`).

## Run and verify

From `apps/web`, run `npm run dev:mock` (`node scripts/dev-mock.mjs`) for explicit development preview. Use `npm run dev` for live mode with the documented server environment (see [LOCAL_LIVE_TESTING.md](LOCAL_LIVE_TESTING.md)). Do not deploy the preview server or build with mock mode.

Current verification (`2026-11-22`): `npm run check` passes boundary checks, TypeScript (`tsc --noEmit`), and **314/314 unit/contract tests** (including `tests/live-cross-service.test.ts` for Tutor deep-links/notes/sources and fault-isolated dashboard enrichment). `NEXT_PUBLIC_DATA_MODE=live npm run build` compiles cleanly (**32/32** static + dynamic routes) and passes `npm run check:release` (no preview fixture markers).

These unit and live-build checks do not replace full 4-service mixed browser E2E testing or quantitative GGUF/RAG evaluation (`QAS-06`).

## Remaining integration work

Core, Document, AI (`tutor` + Quiz generator) and Learning (Practice, Assessment, Assignment, Grading/Correction, Appeals, Calendar deadline projection) are wired in `live` mode.

Only **Learner Intelligence** (`s.progress`, `t.analytics`, `a.reports`, `a.exports`) remains hidden in `src/lib/route-availability.ts` and unavailable in `live` mode. Admin personal-data copy download/review/fulfillment (`DELETE`/retention) remains deferred pending policy decisions (`OD-02`, `OD-03`). Staff invite/recovery delivery requires an operator-configured SMTP provider, and full 4-service mixed browser E2E acceptance remains an operational gate after running all Compose backends together.

## Local development compiler

`npm run dev:mock` uses Next.js 15 Turbopack to reduce initial development compilation. Both `turbopack.resolveAlias` and `webpack.resolve.alias` select the same mode-specific `@data-adapter`. Output stays in `.next-preview` for mock and `.next` for live.

Stop the current server before changing compilers or restarting; never run two servers writing the same output directory. `npm run dev:mock:webpack` keeps the previous Webpack runner available for diagnosing bundler-specific issues.
