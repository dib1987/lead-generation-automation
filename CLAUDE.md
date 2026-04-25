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

# Manually trigger a task (once process_lead.py exists)
docker-compose exec celery_worker celery -A app.workers.celery_app call workers.tasks.process_lead.process_lead --args='["<lead_id>"]'
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

### Request flow (Phase 1B target)
```
POST /api/v1/leads/{tenant_slug}
  → validate with LeadCreateRequest schema
  → write Lead row (status: received)
  → enqueue process_lead Celery task
  → return 202

process_lead task:
  → load tenant config JSON
  → call Claude API → write llm_cost_logs
  → send SES email → write email_logs
  → update lead status → write audit_logs
  → create CampaignEnrollment (status: active, next_send_at: now + delay_days[1])

celery_beat (every 15 min):
  → run_followup task
  → SELECT enrollments WHERE next_send_at <= now() AND status = active
  → for each: generate next email, send, advance current_step, set next next_send_at
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

---

## Phase status

| Phase | Description | Status |
|---|---|---|
| 1A | DB models + Docker + health endpoint | **Complete** |
| 1B | Services, schemas, Celery tasks, API route, tenant/campaign JSON configs | **TODO** |
| 1C | Frontend HTML form for Africa Horizons Travel | **TODO** |

### Not yet built (Phase 1B)
- `backend/app/config/tenants/africa_travel.json`
- `backend/app/config/campaigns/africa_14day.json`
- `backend/app/schemas/lead.py` — `LeadCreateRequest`, `LeadResponse`
- `backend/app/services/llm_service.py` — Claude API wrapper + `llm_cost_logs` write
- `backend/app/services/email_service.py` — AWS SES wrapper
- `backend/app/services/crm_service.py` — HubSpot contact upsert
- `backend/app/services/audit_service.py` — `audit_logs` write helper
- `backend/app/workers/tasks/process_lead.py` — Day 0 email pipeline
- `backend/app/workers/tasks/run_followup.py` — Beat-driven follow-up task
- `backend/app/api/v1/leads.py` — `POST /api/v1/leads/{tenant_slug}`

### Not yet built (Phase 1C)
- `frontend/index.html` — Africa Horizons Travel lead capture form
  - Warm earthy tones (terracotta, sand, deep greens) — premium feel, not generic SaaS
  - Full-page layout with hero image or gradient background
  - Fields: full name, email, destination interest, travel dates, group size, budget range, special requests
  - Tailwind CDN (no build step — single HTML file)
  - Submits to `POST /api/v1/leads/africa_travel`

---

## How to add a new tenant

1. Copy `backend/app/config/tenants/africa_travel.json` → `<new_slug>.json`
2. Copy `backend/app/config/campaigns/africa_14day.json` → `<new_slug>_campaign.json`
3. Update company name, prompts, branding, and CRM config in both files
4. The endpoint is live with no code changes: `POST /api/v1/leads/<new_slug>`
