# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Lead Generation System

A domain-agnostic lead capture and automated outreach system. A prospect fills a web form, and within 60 seconds receives a rich AI-personalized email. Over 14 days, a follow-up sequence fires automatically. Every action is audited. Leads sync to HubSpot.

**Current demo tenant:** Africa Horizons Travel
**Purpose:** Portfolio project to demonstrate to potential clients and win projects.

---

## Stack

| Layer | Technology |
|---|---|
| API | FastAPI (async) + Pydantic v2 |
| DB | PostgreSQL 15 · SQLAlchemy 2.0 async (asyncpg at runtime, psycopg2 for Alembic only) |
| Background jobs | Celery 5.4 + Redis 7 |
| Email | AWS SES via boto3 (sync — runs in Celery tasks, never in route handlers) |
| LLM | Anthropic Claude API (anthropic SDK 0.34.2) |
| CRM | HubSpot REST API via httpx |
| Infra | Docker + docker-compose (5 services) |
| Migrations | Alembic 1.13.3 |

---

## Common dev commands

```bash
# Start all services
docker-compose up --build

# Apply migrations
docker-compose exec api alembic upgrade head

# Generate a new migration after model changes
docker-compose exec api alembic revision --autogenerate -m "describe_change"

# Verify API is up
curl http://localhost:8000/api/v1/health

# Tail API logs
docker-compose logs -f api

# Tail worker logs
docker-compose logs -f celery_worker

# Interactive psql shell
docker-compose exec postgres psql -U leadgen -d leadgen

# Inspect registered Celery tasks
docker-compose exec celery_worker celery -A app.workers.celery_app inspect registered

# Manually trigger a task
docker-compose exec celery_worker celery -A app.workers.celery_app call workers.tasks.process_lead.process_lead --args='["<lead_id>"]'

# Seed tenant rows from config/tenants/*.json (idempotent)
docker-compose exec api python seed.py
```

---

## Architecture rules (enforce these in every PR)

1. Every DB record has `tenant_id` — data never mixes across tenants
2. Tenant behavior comes from `backend/app/config/tenants/<slug>.json` — never hardcode tenant logic
3. FastAPI routes are always `async def` — no blocking calls in route handlers
4. Celery tasks are always `def` (not `async def`) — Celery workers are not async runtimes
5. All external API calls (Claude, SES, HubSpot) must be wrapped in `try/except` — failures log to `audit_logs`, never crash the pipeline
6. Secrets only from environment variables — never in code or config JSON
7. Every lead status change writes a row to `audit_logs`
8. Every Claude API call writes a row to `llm_cost_logs` (for cost tracking)

---

## Architecture overview

### Request flow
```
POST /api/v1/leads/{tenant_slug}
  → check_rate_limit (3 req/IP/hr via Redis)
  → validate with LeadCreateRequest schema
  → dedup check (24h window → active enrollment check up to 30d)
  → write Lead row (status: received)
  → enqueue process_lead Celery task
  → return 202

process_lead task:
  → load tenant config JSON
  → score lead (0–100 via _score_lead)
  → call Claude API → write llm_cost_logs
  → send SES email → write email_logs
  → update lead status → write audit_logs
  → create CampaignEnrollment (status: active, next_send_at: now + delay_days[1])
  → upsert HubSpot contact (non-blocking)
  [on 3rd retry failure] → send admin alert email

celery_beat (every 15 min):
  → run_followup task
  → SELECT enrollments WHERE next_send_at <= now() AND status = active
  → for each: generate next email, send, advance current_step, set next next_send_at

POST /api/v1/webhooks/ses?token=<WEBHOOK_SECRET>
  → parse SNS envelope
  → SubscriptionConfirmation → auto-confirm via SubscribeURL
  → Bounce (Permanent only) → pause CampaignEnrollment + audit log
  → Complaint → pause CampaignEnrollment + audit log
  → Received (inbound reply) → match In-Reply-To → set enrollment replied + audit log + admin alert

GET /api/v1/admin/{tenant_slug}/dashboard      → KPIs + status breakdown
GET /api/v1/admin/{tenant_slug}/leads          → paginated list (filter by status/email)
GET /api/v1/admin/{tenant_slug}/leads/{id}     → lead detail + email logs + audit trail
GET /api/v1/admin/{tenant_slug}/email-logs     → paginated email log (filter by status/lead_id)
All admin routes require X-Admin-Key header matching ADMIN_API_KEY
```

### Non-obvious design decisions
- **`backend/app/db/base.py`** imports all 7 model modules. This is required — Alembic's `autogenerate` discovers tables via `Base.metadata`, which is only populated after models are imported. Never remove these imports.
- **`backend/alembic/env.py`** rewrites `postgresql+asyncpg://` to `postgresql+psycopg2://` at migration time. Alembic's runner is synchronous; the swap is required.
- **`audit_logs.meta`** is mapped as `metadata` in Python (column alias) to avoid shadowing SQLAlchemy's reserved `metadata` attribute on `DeclarativeBase`.
- The `get_db()` dependency in `session.py` commits on success and rolls back on exception — routes must not call `session.commit()` themselves.

---

## Model conventions

All 7 models (`tenant`, `lead`, `campaign`, `campaign_enrollment`, `email_log`, `audit_log`, `llm_cost_log`) follow:
- `uuid.UUID` primary keys with `default=uuid.uuid4`
- `tenant_id` FK on every table with `ondelete="CASCADE"`
- `Mapped[...]` typed column syntax (SQLAlchemy 2.0 style)
- `server_default=func.now()` for timestamps (DB-side)
- JSONB columns for variable-shape data (`form_data`, `config`, `steps`, `meta`)

**Campaign step JSON shape** (stored in `campaigns.steps` JSONB array):
```json
{
  "step": 0,
  "delay_days": 0,
  "subject_template": "string with {variables}",
  "prompt_template": "Claude system prompt with {form_data_fields}"
}
```

**LLM cost formula** (in `llm_cost_log.py`):
```
estimated_cost_usd = (input_tokens * 0.000003) + (output_tokens * 0.000015)
```

---

## Lead status flow

```
received → processing → email_sent → (campaign active) → completed
                     ↘ email_failed
```

## Campaign enrollment status

```
active → completed   (all steps sent)
       → paused      (bounce or complaint received)
       → replied     (prospect replied — stops sequence immediately)
```

---

## Environment variables

| Variable | Default | Required |
|---|---|---|
| `DATABASE_URL` | — | Yes (`postgresql+asyncpg://...`) |
| `REDIS_URL` | redis://redis:6379/0 | Yes |
| `AWS_ACCESS_KEY_ID` | "" | For email |
| `AWS_SECRET_ACCESS_KEY` | "" | For email |
| `AWS_REGION` | us-east-1 | For email |
| `SES_VERIFIED_SENDER` | "" | For email |
| `ANTHROPIC_API_KEY` | "" | For LLM |
| `HUBSPOT_API_KEY` | "" | For CRM |
| `ENVIRONMENT` | development | No |
| `SECRET_KEY` | change-me | No |
| `CORS_ORIGINS` | http://localhost:3000 | No (comma-separated) |
| `ADMIN_ALERT_EMAIL` | "" | Phase 1C — failure alert emails |
| `WEBHOOK_SECRET` | "" | Phase 1C — guards /webhooks/ses endpoint |
| `ADMIN_API_KEY` | "" | Phase 2 — guards /api/v1/admin/* endpoints; leave empty to disable auth |

---

## Phase status

| Phase | Description | Status |
|---|---|---|
| 1A | DB models, Docker, health endpoint, migrations | **Complete** |
| 1B | Services, Celery tasks, API route, tenant/campaign configs, frontend form, live email pipeline | **Complete** |
| 1C | Production hardening — failure alerting, SES bounce webhook, rate limiting, seed script, structured logging | **Complete** |
| 2 | Multi-tenant admin dashboard — leads view, email history, cost tracking | **Complete** |
| 3 | Webhook handling — SES bounce/complaint, reply detection | **Complete** |

### Key non-obvious decisions made in Phase 1B
- **Sync DB session for Celery** (`db/sync_session.py`): asyncpg is incompatible with Celery prefork workers. A separate psycopg2-backed sync session factory is used in all Celery tasks.
- **Explicit commit before `.delay()`** in `leads.py`: `await session.commit()` is called before `process_lead.delay()` to prevent a race condition where the Celery task starts before the Lead row is committed. This is the only place a route handler commits explicitly — `get_db()` still handles commit/rollback for everything else.
- **Campaign row auto-bootstrap**: `process_lead` creates the Campaign DB row from JSON config on first run — no seed script needed for campaigns.
- **Tenant row requires manual seed**: Run `docker-compose exec api python seed.py` after a fresh container start. The seed script reads all `config/tenants/*.json` files and is idempotent.
- **Code fence stripping in `llm_service.py`**: Claude sometimes wraps HTML output in markdown code fences. `_strip_code_fences()` removes them before the body is sent to SES.

### Key non-obvious decisions made in Phase 1C
- **Structured logging** (`core/logging_config.py`): `configure_logging()` called at startup in both `main.py` and `celery_app.py`. Plain text in development, JSON-per-line in production (CloudWatch/Datadog compatible).
- **Rate limiting** (`core/rate_limit.py`): Redis-backed sliding window via INCR + EXPIRE. Default: 3 submissions per IP per hour on the `/leads` route. Fails open if Redis is unreachable — rate limiting is a protection layer, not a critical gate.
- **SES bounce/complaint webhook** (`api/v1/webhooks.py`): `POST /api/v1/webhooks/ses` receives SNS notifications. Handles `SubscriptionConfirmation` (auto-confirms), permanent `Bounce`, and `Complaint` — sets affected `CampaignEnrollment` rows to `paused` and writes audit logs. Only permanent bounces trigger a pause; transient (mailbox full) are ignored.
- **Failure alerting** (`email_service.send_admin_alert`): called by `process_lead` after all 3 Celery retries are exhausted. Sends a plain SES email to `ADMIN_ALERT_EMAIL`. Silently skips if not configured.
- **Seed script** (`backend/seed.py`): reads all `config/tenants/*.json` files and inserts missing tenant rows. Idempotent — skips slugs that already exist.

### Key non-obvious decisions made in Phase 2
- **Admin API** (`api/v1/admin.py`): four endpoints — `GET /{tenant}/dashboard`, `GET /{tenant}/leads`, `GET /{tenant}/leads/{id}`, `GET /{tenant}/email-logs`. All guarded by `X-Admin-Key` header (verified against `ADMIN_API_KEY` env var; auth skipped if env var is empty).
- **Admin dashboard** (`admin/index.html`): vanilla JS SPA served from the `admin/` directory. Login stores tenant + API key in `localStorage` for session persistence. Tabs: Dashboard (KPI cards + status breakdown bars), Leads (filterable + searchable table with slide-in detail panel), Emails (filterable log with body preview modal).
- **Admin schemas** (`schemas/admin.py`): Pydantic v2 models with `from_attributes=True` for ORM → response serialization. `LeadSummary` flattens `form_data.full_name` and `form_data.destination` for the table view.
- **`ADMIN_API_KEY` env var** added to `settings.py`. Leave empty in dev to disable auth.

### Key non-obvious decisions made in Phase 3
- **Reply detection via SES inbound** (`api/v1/webhooks.py`): The existing `POST /api/v1/webhooks/ses` endpoint handles a third notification type: `Received`. When a lead replies, SES inbound receipt rules publish an SNS notification with `notificationType: Received`. The handler extracts `mail.commonHeaders.inReplyTo`, strips angle brackets, and looks up the matching `email_logs.ses_message_id`.
- **In-Reply-To angle bracket stripping**: SES Message-IDs arrive wrapped in `<...>` in the `In-Reply-To` header (e.g. `<0102abc@us-east-1.amazonses.com>`). The lookup strips these before querying `email_logs`.
- **`replied_at` column on `campaign_enrollments`**: Migration `a7f3c891d042` adds a nullable `DateTime` column. Set when an enrollment transitions to `replied`.
- **Admin alert on reply**: `send_admin_alert()` fires when a reply is matched — a reply is a high-intent signal and the admin should know immediately.
- **Infrastructure prerequisite**: SES inbound receipt rules must be configured manually in AWS to publish to an SNS topic that delivers to this webhook endpoint. The code handles the notification; the AWS wiring is ops config.

---

## Admin dashboard

The dashboard lives at `admin/index.html` — a self-contained vanilla JS SPA. No build step.

```bash
# Open locally (any static file server works)
# Option 1 — Python
python -m http.server 5500 --directory admin

# Option 2 — VS Code Live Server → point to admin/index.html

# Default API target is http://localhost:8000 — change API_BASE in the <script> for other envs
```

Login flow: enter tenant slug + `ADMIN_API_KEY` value → credentials stored in `localStorage` for session persistence.

---

## How to add a new tenant

1. Copy `backend/app/config/tenants/africa_travel.json` → `<new_slug>.json`
2. Copy `backend/app/config/campaigns/africa_14day.json` → `<new_slug>_campaign.json`
3. Update company name, prompts, branding, and CRM config in both files
4. The endpoint is live with no code changes: `POST /api/v1/leads/<new_slug>`
