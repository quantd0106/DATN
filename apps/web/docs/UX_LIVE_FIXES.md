# Live UX corrections — 2026-11-15

This follows the user-provided browser audit. Existing uncommitted UI work was
preserved. Changes cover existing classroom, feed, calendar and account flows.

## Correctness and availability

- Teacher home now includes the teacher's own posts in recent class posts,
  sorted by creation time. Previously the adapter explicitly discarded them.
- Student/teacher class overviews read the latest post from the same Core posts
  endpoint as the feed. A failed read stays an error rather than becoming an
  empty announcement. The label says "Bài viết mới nhất", without inventing a
  separate announcement type or author role.
- Teacher home counts distinct current student IDs across active assigned
  classes, rather than adding per-class counts and potentially double-counting.
- Live sidebar/class tabs and classroom quick links hide unavailable Document,
  Tutor, Learning, analytics, reports and Admin data-request screens. Direct
  routes retain their existing unavailable states with product-facing copy.
  Unavailable learning/material widgets are omitted on classroom overview/home.
- Preview progress defaults to the earliest confirmed result in the permitted
  student/class scope. An explicit date filter continues to take precedence.
  A regression covers October scores still appearing by default in November.

## Identity and class membership

- Existing Core class read responses expose current primary teacher display
  name, active student count and the requesting Student's current membership.
- Existing class member reads expose display names, with stable name/ID order.
  Teacher overview renders that roster. Homeroom roster IDs have a stable order.
- Classroom obtains names through Identity's public batch summary interface,
  after existing resource authorization; cross-school/missing names stay null.
  No student email, password or token is added to a response. No migration or
  new endpoint is needed. See the Core API contract display enrichment section.
- Local synthetic Admin/teacher accounts receive explicit test display names
  through the dedicated Identity seed. Existing nonempty names are preserved.
  Feed authors, class cards and headers now receive those saved names.
- Live account lists sort by username/email with numeric comparison and an ID
  tie breaker after loading all API pages, before UI filtering/pagination.

## Layout

- Calendar switches to two columns based on its actual content container width;
  the desktop grid fits all seven days. Narrow calendars retain a labeled local
  horizontal scroll region instead of clipping the final day.
- Class tabs wrap to additional rows. Account identifiers keep whole words and
  wide tables scroll within their existing table regions.
- Metrics adapt to the number of available cards; teacher/student overview
  sidebars do not reserve an empty column when their services are unavailable.

## Verification

- `npm.cmd run check`: boundary checks, TypeScript and 266/266 tests passed.
- Final `NEXT_PUBLIC_DATA_MODE=live npm.cmd run build` passed, including release
  fixture scanning. The rebuilt app was restarted at http://localhost:3000.
- Core Classroom tests: 11/11 passed against the separate PostgreSQL database
  `test_ux_20261115`, including roster ordering/counts and revoked, unassigned or
  cross-school access denial. Ruff lint/format passed for changed Python files.
- The updated local Core API was exercised against the seeded live database:
  teacher classes have names/counts; a roster has 20 names and stable ordering;
  post authors have saved display names; Student classes report ACTIVE current
  membership. The identity seed rerun preserved record counts.
- The actual live Web adapter was then exercised through localhost:3000:
  teacher home has seven recent posts and 140 distinct students; overview's
  latest post matches feed; roster has 20 names; the sample Student has four
  current classes with teacher names. The HTTP verifier supplies the browser's
  Origin header; the initial request without Origin was correctly rejected by
  the proxy with 403, then the corrected verifier passed.
- Native Chrome inspection reached the existing Admin screen. Further visual
  verification stopped when the tool detected concurrent user input. Desktop
  and mobile responsive fixes are therefore not certified by browser screenshots.

Document/Tutor/Learning services were added later on 2026-11-22 (see section below). Hiding their live
navigation at the 2026-11-15 checkpoint did not implement them. Backend/public response changes
require synchronized Core Classroom and Web updates.

---

# Cross-service live UX synchronization — 2026-11-22

Following the full integration of Core, Document, AI, and Learning in live mode
(`pendingCapabilities = ['Learner Intelligence']`), UI panels that were previously
hidden became active on live dashboards and workspaces. The following fixes eliminate
hardcoded empty states and synchronize cross-service navigation without inventing
new APIs or hiding authentication expiry:

## Live UI/UX & cross-service wiring

- **Student Tutor workspace (`s.tutor` in `live-ai.ts`):**
  - Citation `readerHref` now deep-links directly to `s.reader` (`/student/classes/:classId/documents/:documentId?page=...`) whenever `cite.document_id` is a valid UUID and the citation is accessible, preserving `s.documents` fallback when only an opaque `source_id` is present.
  - `classHasSources` and `quickNotes` are now fetched from Document (`live-document.ts`) via optional `documentContext` (instead of hardcoded `true` / `[]`), while keeping `s.history` enriched with class notes when present.
- **Classroom home & overviews (`live-dashboard.ts` + `live.ts`):**
  - `s.overview` enriches `recentMaterials` (top 4 class documents linking to `s.reader`) and `upcomingActivities` (active assessments, assignments, and practices from Learning).
  - `t.overview` enriches `upcomingActivities` (teacher activities list) and computes `teacherMetrics` (`pendingGrading` + `openActivities`) from Learning `gradingQueue` and `activities`.
  - `t.home` aggregates real `stats.pendingGrading` across active assigned classes (leaving it `undefined` -> *"Chưa có dữ liệu"* when all Learning calls fail).
  - `s.home` populates `upcomingTasks` (assessments & assignments sorted by due time), `recentStudies` (practice progress/results), and `tutorSuggestions` (real per-class Tutor shortcuts).
- **Integrations ledger (`integrations.ts`):** updated `calendar.operations.learningDeadlineProjection` from `'pending-service'` to `'contracted'`.
- **Fault isolation & session safety:** `optionalServiceRead` (`learning-wire.ts`) catches non-`401` dependency/network failures to isolate outages across Document, AI, and Learning (`QAS-01`/`QAS-02`) without falling back to preview fixtures, while strictly rethrowing `401` and verifying `sessionGeneration()` after awaits.

## Verification (`2026-11-22`)

- `apps/web`: `npm run check` passed boundaries, TypeScript, and **314/314 tests** (including new `tests/live-cross-service.test.ts`); `NEXT_PUBLIC_DATA_MODE=live npm run build` built all 32 static/dynamic routes and passed `check:release`.
- Backends aligned alongside this UI sync: `services/ai` (`39 passed, 8 skipped`, including `test_document_client.py`) and `services/learning` (`34 passed, 1 skipped`, including HTTP `ActiveGrantsResponse` wire test) both accept `services/document`'s real `ActiveGrantsResponse` (`items` with per-item `school_id` & `serving_version_id`) while remaining compatible with legacy `GrantSnapshot`.
