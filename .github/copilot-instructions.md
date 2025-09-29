# Copilot instructions for AI Study Assistant

Purpose: Make agents immediately productive in this monorepo (FastAPI backend + Next.js frontend). Keep responses concise, concrete, and aligned with these project conventions.

## Architecture overview
- Backend (FastAPI, Python): `AI_study_assistant/app/*`
  - Entry: `app/main.py` exposes `/api/v1/*`, OpenAPI at `/api/docs`.
  - Routers: `app/api/v1/routes/**` (auth, user, materials, assessments, tutoring, debug, flash-cards).
  - Core: `app/core/*` for config (`Settings`), logging, security (JWT, TOTP), genai client (Gemini with retry), response helpers.
  - DB: async SQLAlchemy in `app/db/deps.py` (AsyncSessionLocal, `get_db()`), models in `app/models/*`, Alembic in `migrations/`.
  - Services: domain logic (AI, flash_cards generator, material processing, mailers, usage/subscription tracking).
  - Schemas: Pydantic input/output models.
- Frontend (Next.js 15, React 19, TS): `ai_study_assistant_frontend/src/app/*`
  - Auth: NextAuth credentials provider at `app/api/auth/[...nextauth]/route.ts`; session JWT contains `accessToken` pulled from backend `/auth/login` response.
  - API client: `src/app/lib/api-client-services/axios-client.ts` injects Bearer token by calling Next route `GET /api/auth/token` when endpoint isn’t public.
  - Feature clients live under `src/app/lib/api-client-services/*` (FlashCards, Profile, etc.).
  - Global error handling and plan-limit UX: `hooks/useApiErrorHandler.ts` + `providers/UpgradeModalProvider.tsx`.

## Cross-component contracts
- Response envelope: Backend should return `{"status":"success"|"error","msg":string,"data":any}`. Error variant may also include `error_code` and `details` in dev. Use helpers `success_response`/`error_response`/`validation_error_response`.
- Auth:
  - POST `/api/v1/auth/login` returns `{ access_token, refresh_token, token_type }` inside the envelope. Frontend maps `access_token` into NextAuth JWT `accessToken` and serves it via `/api/auth/token`.
  - Protected routes read `Authorization: Bearer <JWT>` (FastAPI `oauth2_scheme`). Use `get_current_user`.
- Plan limits: When enforcing limits, use `plan_limit_error(...)` so frontend can detect `status 403` + `error_code: "PLAN_LIMIT_EXCEEDED"` and open the upgrade modal.
- Flash cards domain:
  - Model: `FlashCardSet(cards_payload: [{prompt, correspondingInformation, hint?}], status: processing|completed|failed)`.
  - Endpoints:
    - GET `/flash-cards/by-material/{material_id}/all` -> list summary items (includes `status` and `count`).
    - GET `/flash-cards/{set_id}` -> full set with `cards` (hints guaranteed by server).
    - POST `/flash-cards/` -> create manual set (validates plan limits; increments usage).
    - POST `/flash-cards/generate` -> returns `{ id, status: "processing" }`; background task fills cards and updates `status`.
    - DELETE `/flash-cards/{set_id}`.
  - Generation: `services/flash_cards/generator.py` uses Gemini via `core/genai_client.py` with robust JSON parsing and hint backfill.

## Backend workflows
- Env/config: `app/core/config.py` loads from `.env` and validates required keys (DATABASE_URL, GOOGLE_API_KEY, JWT_* etc.).
- Run API locally:
  - VS Code task: "run-backend" (python -m app.main) or set `$env:PORT=8101` task variants.
  - CORS honors `settings.ALLOWED_ORIGINS`.
- DB:
  - Async SQLAlchemy engine is created from `settings.DATABASE_URL` (echo=True in dev).
  - Alembic: `alembic upgrade head` (task: "alembic-upgrade-head").
- Logging: centralized via `core/logging_config.setup_logging()` (file `app.log` + console). Use `get_logger(name)`.
- Error handling: Central handlers in `app/main.py` convert `HTTPException` and `RequestValidationError` to envelope errors.

## Frontend workflows
- Dev server: `npm run dev` under `ai_study_assistant_frontend` with NEXT_PUBLIC_API_URL pointing to backend (e.g., http://localhost:8100/api/v1 base handled by axios baseURL).
- API usage: Import from `src/app/lib/api-client-services/**`; do not create ad-hoc axios instances.
- Auth flow: Use NextAuth APIs; obtain headers implicitly via axios interceptor. Do not manually attach tokens.
- Errors and plan limits: Always pass caught errors to `useApiErrorHandler().handleApiError(error)` to trigger upgrade modal on 403 PLAN_LIMIT_EXCEEDED.

## Patterns and conventions
- Endpoints return the envelope; frontend expects `data` field. Examples:
  - FlashCardsApi.get(id) -> `data.data as FlashCardSetDetail`.
- Async background work: Long-running generation happens via `BackgroundTasks` updating `status` field; clients should poll or refetch as needed.
- LLM usage: Use `core/genai_client.get_gemini_model()` (singleton with retries). Validate `.text`, strip code fences, robustly `json.loads` with fallback extraction.
- Hints consistency: Server ensures every card has a non-empty `hint` before returning; clients can rely on it.
- Security: Use `get_current_user` in protected routes; raise `error_response` with appropriate status.

## Gotchas
- Config validation will raise at import time if required envs are missing (DATABASE_URL, GOOGLE_API_KEY). For tests or scripts, set dummy envs.
- `FlashCardsApi.list()` is not used; prefer `listFlashCardSetsByMaterial(materialId)` which maps to `/by-material/{id}/all`.
- When adding new plan limit types, ensure `error_code: "PLAN_LIMIT_EXCEEDED"` and include `data.error_type`, `data.current_plan`, and `data.limit` so the modal can derive copy and numbers.
- Don’t name SQLAlchemy columns `metadata` on mapped classes (use `extra_metadata`).

## Examples
- Server success:
  return success_response("Created", data={"id": row.id})
- Server plan limit:
  return plan_limit_error(message="Upgrade", error_type="MONTHLY_FLASHCARDS_LIMIT_EXCEEDED", current_plan=plan.name, metric="monthly_flash_card_sets", used=usage.count, limit=plan.monthly_flash_cards_limit)
- Client fetch:
  const detail = await FlashCardsApi.get(id);
  // detail.cards has hints
- Client error handling:
  try { await FlashCardsApi.generate(req); } catch (e) { handleApiError(e); }

## Where to look
- Backend: `app/api/v1/routes/*`, `app/services/*`, `app/core/*`, `app/models/*`, `app/schemas/*`.
- Frontend: `src/app/lib/api-client-services/*`, `src/app/hooks/useApiErrorHandler.ts`, `src/app/providers/UpgradeModalProvider.tsx`.
