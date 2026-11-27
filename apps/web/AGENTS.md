# Athena Web agent instructions (additive to repository-root `AGENTS.md`)

- Read `docs/FRONTEND.md`, the repository's `docs/architect/BUSINESS_RULES.md`, and `src/lib/data/integrations.ts` before editing relevant features.
- `app/**/page.tsx` are minimal route entry points; implement actual UI in `src/features`, shared `src/components`, or explicitly owned libraries.
- No direct business network calls or fixture imports from feature pages. Use `src/lib/data/port.ts` through `src/lib/data/index.ts`. Preserve the `@data-adapter` single entry and `next.config.mjs` live/mock isolation.
- Only operations allowlisted in `src/lib/api/operations.ts` (Core & Document), `src/lib/api/ai-operations.ts` (AI), and `src/lib/api/learning-operations.ts` (Learning) may be called through `src/lib/api/proxy.ts`. Contract approval is required before extending service proxy/decoders.
- Keep `NEXT_PUBLIC_DATA_MODE=mock` limited to local preview; no mock production build, fixture fallback, fake persisted success or fabricated evidence.
- Keep session, permission and API failure semantics; the server enforces roles and ownership. Never display Student join code/self-register or invent new roles.
- Write meaningful loading, error, empty, 401/403/409, validation and responsive/keyboard states appropriate to the task.
- Preserve uncommitted work and avoid edits to services, migrations, root rules and unrelated modules unless explicitly required.
- Validation from this folder: `npm run check` and `npm run build` in **live** mode; report exact exit codes and any blocked tests. A passing build is not evidence of a real backend journey.
