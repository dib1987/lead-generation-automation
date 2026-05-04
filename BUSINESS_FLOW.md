# Lead Generation System — Business Flow & Requirements

> **Living document.** Update this file whenever business rules, flows, or requirements change.
> Last updated: 2026-05-04

---

## What This System Does

A prospect fills in a web form. Within 60 seconds they receive a rich, AI-personalised email written specifically for them — their destination, their travel dates, their group, their budget. Over the following 14 days, five more emails fire automatically. Every action is logged. Every lead is scored. Their details land in a CRM. No human needs to be in the loop.

If the lead replies to any email — their sequence stops immediately. The admin is alerted. A human can take over from that point.

If an email bounces permanently or the lead marks it as spam — their sequence pauses immediately. No further emails are sent.

**Current demo tenant:** Africa Horizons Travel (luxury African safari and travel company)
**Purpose:** Portfolio project to demonstrate to potential clients and win projects.

---

## System Components

| Component | What it does |
|---|---|
| Frontend form | Captures lead data — 9 required fields + 7 optional |
| `POST /api/v1/leads/{tenant_slug}` | Validates, deduplicates, creates the lead record, queues the task |
| `process_lead` (Celery task) | Day 0 pipeline — scores, generates email via Claude, sends via SES, syncs CRM |
| `run_followup` (Celery Beat) | Fires every 15 minutes — sends the next email in the sequence for any lead that is due |
| `POST /api/v1/webhooks/ses` | Receives SNS notifications — handles bounces, complaints, and inbound replies |
| Admin dashboard (`admin/index.html`) | Vanilla JS SPA — KPI cards, leads table with detail view, email log |
| `GET /api/v1/admin/{tenant}/…` | Admin API — dashboard, leads, lead detail, email logs |
| Services layer | Thin wrappers for Claude, SES, HubSpot, and audit logging |
| Database | PostgreSQL — 7 tables tracking every lead, email, cost, and audit event |

---

## The Lead Lifecycle

A lead moves through the following statuses. Every transition writes a row to `audit_logs`.

```
received
   ↓
processing
   ↓              ↘
email_sent       email_failed
   ↓
[campaign active — 14-day sequence firing]
   ↓
completed
```

### Lead status definitions

| Status | Meaning |
|---|---|
| `received` | Form submitted, Lead row created, task queued |
| `processing` | Celery task has picked it up, work in progress |
| `email_sent` | Day 0 email delivered via SES successfully |
| `email_failed` | Day 0 email attempt failed (Claude error or SES error) |
| `completed` | All campaign steps sent — sequence finished |

### Campaign enrollment statuses

| Status | Meaning |
|---|---|
| `active` | Enrolled in sequence, emails still firing |
| `completed` | All steps sent |
| `paused` | Permanent bounce or complaint received — sequence halted |
| `replied` | Prospect replied to an email — sequence stops immediately, admin alerted |

---

## Stage 1 — The Form (Frontend)

The prospect fills in the Africa Horizons Travel lead capture form at `frontend/index.html`.

### Required fields (must be filled to submit)

| Field | Why required |
|---|---|
| Full name | Personalisation in every email |
| Email address | Primary identifier — dedup key, email sending, CRM |
| Phone country code | CRM + WhatsApp contact |
| Phone number | CRM + WhatsApp contact |
| Preferred contact method | WhatsApp / Email / Call — determines how the travel designer follows up |
| Destination | Drives all email content |
| Travel month | Drives seasonal content, urgency, availability |
| Travel year | Same as above |
| Adults in group | Group size affects recommendations and urgency |

### Optional fields (enrich Claude prompts if provided)

| Field | How it is used |
|---|---|
| Children under 12 | Lodge recommendations, kid-friendly itinerary advice |
| Age range of adults | Tailoring suggestions |
| Trip duration (days) | Adds detail to itinerary emails |
| Accommodation preference | Luxury / mid-range / budget — drives Day 11 urgency email |
| Trip motivation | Honeymoon / family / adventure / etc. — emotional tone of all emails |
| Budget range | Affects lead score + Day 11 availability email |
| Special requests | Free text — injected verbatim into Claude prompt |

**Rule:** Empty optional fields are normalised to `None` server-side before storage. Claude prompts treat `None` as "Not specified" — emails read naturally even when fields are blank.

---

## Stage 2 — The API Route (`POST /api/v1/leads/{tenant_slug}`)

The server receives the form payload. Before creating anything it runs two checks: **rate limiting** and **deduplication**.

### Rate limiting

- Redis-backed sliding window: 3 submissions per IP per hour
- Returns HTTP 429 on breach
- Fails open if Redis is unreachable — rate limiting is a protection layer, not a critical gate

### Deduplication rules

| Scenario | Action | Response |
|---|---|---|
| No prior submission from this email | Create new Lead row, queue task | 202 `received` |
| Same email, submitted < 24 hours ago, not completed | Do NOT create new row — return existing lead | 202 `already_enrolled` — "We already have your enquiry and are working on it." |
| Same email, submitted 24h–30 days ago, active enrollment | Do NOT create new row — existing sequence continues | 202 `already_submitted` — "You are already in our journey sequence." |
| Same email, submitted > 30 days ago OR previous journey completed | Treat as fresh — create new Lead row, queue task | 202 `received` |

**Why 202 and not 200?** HTTP 202 means "Accepted — we received your request and are processing it asynchronously." 200 would imply the work is done. The email hasn't been sent yet at this point.

**Why deduplicate at the route level?** To avoid creating junk Lead rows in the database for accidental double-submits (double-click, browser back + resubmit, etc).

---

## Stage 3 — Day 0 Pipeline (`process_lead` Celery task)

Runs in the background after the API route returns 202. The visitor does not wait for this.

### Full step-by-step flow

```
1.  Fetch Lead + Tenant from database
2.  Load tenant config from JSON: config/tenants/{slug}.json
3.  Load campaign config from JSON: config/campaigns/{slug}.json
4.  Safety dedup check (idempotency — Celery can retry tasks)
5.  Update lead status → "processing" + write audit_log
6.  Score the lead (0–100)
7.  Save lead_score to database
8.  Find or create Campaign DB row (bootstrapped from JSON on first run)
9.  Generate Day 0 email via Claude API → write llm_cost_logs
10. Send email via AWS SES → write email_logs
11. Create CampaignEnrollment (step=0, status=active, next_send_at = enrolled_at + 2 days)
12. Update lead status → "email_sent" + write audit_log
13. Sync contact to HubSpot CRM (new session, non-blocking — failure is logged, not fatal)
[on 3rd retry failure] → send admin alert email
```

### Lead scoring rules (0–100)

Scoring tells the travel designer which leads to prioritise. Higher score = higher intent + higher value.

| Signal | Points |
|---|---|
| Budget: £10,000+ | +20 |
| Budget: £5,000–£10,000 | +15 |
| Budget: £2,500–£5,000 | +10 |
| Budget: any other or not specified | +5 |
| Adults: 4 or more | +15 |
| Adults: 2–3 | +10 |
| Adults: 1 | +5 |
| Accommodation preference: luxury | +15 |
| Accommodation preference: mid-range | +10 |
| Accommodation preference: other | +5 |
| Trip motivation filled in | +10 |
| Special requests filled in | +5 |
| Trip duration: 10+ days | +10 |
| Trip duration: 7–9 days | +5 |
| **Maximum** | **100** |

Score is saved on the Lead row. Used in the admin dashboard for lead prioritisation. Available for future routing logic (e.g. high-score leads get WhatsApp outreach).

### Email generation rules (Claude API)

- Model: Anthropic Claude (configurable per tenant — defaults to claude-sonnet-4-6)
- Input: the campaign step's `prompt_template` + all form data as variables
- `first_name` is extracted from `full_name` (first word before space)
- `signature_name` is injected from tenant config
- Missing optional fields default to `"Not specified"` in the prompt
- Claude outputs **HTML body only** — `<p>` tags, no `<html>/<head>/<body>` envelope
- The email service wraps the body in a full HTML envelope before sending
- Markdown code fences (` ```html … ``` `) are stripped before the body reaches SES
- Every Claude call writes a row to `llm_cost_logs`:
  - `cost_usd = (input_tokens × $0.000003) + (output_tokens × $0.000015)`
  - This enables per-lead, per-tenant, per-step cost tracking

### Email sending rules (AWS SES)

- Sender: `SES_VERIFIED_SENDER` environment variable (never hardcoded)
- From name: `ses.from_name` from tenant config JSON
- Reply-to: `ses.reply_to` from tenant config JSON (leads reply to this address)
- After send: writes `EmailLog` row (to address, subject, SES message ID, step number, status)
- On failure: logs to `audit_logs` with event `email_failed`, updates lead status, Celery retries up to 3×

### Failure alerting (process_lead)

- After all 3 Celery retries are exhausted, `_send_failure_alert()` is called
- Sends a plain SES email to `ADMIN_ALERT_EMAIL` with the lead ID and error
- Silently skips if `ADMIN_ALERT_EMAIL` is not configured

### CRM sync rules (HubSpot)

- Upserts contact using email as the dedup key
- Maps form fields to HubSpot contact properties
- Skips `owner_id`, `pipeline_id`, `deal_stage_id` if null in tenant config
- **Non-blocking:** if HubSpot fails, the failure is logged to `audit_logs` — the pipeline does NOT crash. The email was already sent; CRM sync is recoverable
- On success: saves `crm_contact_id` and `crm_synced_at` to the Lead row

---

## Stage 4 — 14-Day Follow-Up Sequence (`run_followup` Celery Beat task)

### How it runs

Celery Beat fires `run_followup` every 15 minutes.

The task queries:
```sql
SELECT id FROM campaign_enrollments
WHERE next_send_at <= NOW() AND status = 'active'
```

For each enrollment that is due, it processes in its own isolated DB session:

1. Re-fetch enrollment; if status is no longer `active` — skip (safety check)
2. Load lead + tenant + campaign from DB
3. Get the next step config from the campaign JSON
4. Generate the next email via Claude → write `llm_cost_logs`
5. Send via SES → write `EmailLog` row
6. Increment `current_step`
7. If this was the **last step** → `enrollment.status = completed`, `lead.status = completed`, write `sequence_completed` audit event
8. If more steps remain → `next_send_at = enrolled_at + timedelta(days=next_step.delay_days)`

**Rule: `delay_days` is always relative to `enrolled_at`, not relative to the previous email.**
Day 8 always fires 8 days after enrollment. If Day 5 was delayed by a retry, Day 8 is unaffected.

**Rule: failures on individual enrollments are isolated.**
If 20 leads are due and one fails, the other 19 still get their emails.

**Rule: paused and replied enrollments are never processed.**
The `WHERE status = 'active'` filter excludes them. No code change needed when a lead replies.

---

## The Africa Horizons 14-Day Email Sequence

| Step | Day | Subject theme | Goal |
|---|---|---|---|
| 0 | 0 — immediate | Personal welcome | First impression — warm, specific to their trip |
| 1 | Day 2 | Destination spotlight | Why their travel month is great for this destination |
| 2 | Day 5 | Social proof | Testimonials relevant to their trip motivation |
| 3 | Day 8 | Expert practical tips | Visas, vaccinations, packing — builds trust as insiders |
| 4 | Day 11 | Availability urgency | Luxury lodges book out — soft, factual pressure to act |
| 5 | Day 14 | Graceful final check-in | No hard sell — leave a positive last impression |

---

## Stage 5 — SES Webhook (`POST /api/v1/webhooks/ses`)

AWS SNS delivers SES delivery notifications and inbound email events to this endpoint.

**Security:** query parameter `?token=<WEBHOOK_SECRET>` guards the endpoint. Auth skipped if `WEBHOOK_SECRET` is empty (dev only).

### Subscription confirmation

When the SNS topic is first configured, SNS sends a `SubscriptionConfirmation` message. The webhook auto-confirms by making a GET request to the `SubscribeURL` in the envelope. Required before SNS sends real notifications.

### Permanent bounce

- `notificationType: Bounce` + `bounceType: Permanent`
- Affected email addresses extracted from `bounce.bouncedRecipients`
- For each address: all active enrollments found and set to `paused` + audit log written
- Email log rows for that address set to `status = bounce`
- Transient bounces (mailbox full) are **ignored** — they may recover on their own

### Complaint

- `notificationType: Complaint`
- Affected email addresses extracted from `complaint.complainedRecipients`
- For each address: all active enrollments found and set to `paused` + audit log written
- Email log rows for that address set to `status = complaint`

### Reply detection (inbound email)

This is the high-intent signal. When a lead replies to any outbound email:

1. SES inbound receipt rules route the email to SNS, which delivers `notificationType: Received`
2. The webhook extracts `mail.commonHeaders.inReplyTo` from the SNS payload
3. Angle brackets are stripped from the In-Reply-To value (SES wraps message IDs as `<id@ses>`)
4. The stripped value is looked up in `email_logs.ses_message_id` to find the original outbound email
5. The lead's active enrollment(s) are found and set to `replied` + `replied_at = now()`
6. An `enrollment_replied` audit log row is written with sender, subject, and original message ID
7. An admin alert email is fired (asynchronously, non-blocking) — a reply = hot lead

**Infrastructure prerequisite:** SES inbound receipt rules must be configured in AWS to route email for the `reply_to` address through the SNS topic that delivers to this webhook endpoint. This is a one-time AWS console configuration step — not application code.

---

## Stage 6 — Admin Dashboard (`GET /api/v1/admin/{tenant_slug}/…`)

All admin routes require the `X-Admin-Key` header matching `ADMIN_API_KEY` env var. Auth skipped if the env var is empty.

### Endpoints

| Endpoint | Returns |
|---|---|
| `GET /{tenant}/dashboard` | `total_leads`, `leads_by_status` breakdown, `emails_sent`, `avg_lead_score` |
| `GET /{tenant}/leads` | Paginated leads list (filter by `status`, search by `q` email) |
| `GET /{tenant}/leads/{id}` | Full lead detail: form data, email history, complete audit trail |
| `GET /{tenant}/email-logs` | Paginated email log (filter by `status`, `lead_id`) |

### Admin SPA (`admin/index.html`)

Vanilla JS single-page app. No build step. Opened directly in browser or via static server.

- **Login:** enter tenant slug + `ADMIN_API_KEY` value → stored in `localStorage`
- **Dashboard tab:** KPI cards (total leads, emails sent, avg score) + status distribution bars
- **Leads tab:** filterable + searchable paginated table; click a row → slide-in detail panel with audit trail
- **Emails tab:** paginated email log with body preview modal
- **Status colours:** `received` blue, `email_sent` green, `email_failed` red, `completed` teal, `paused` pink, `replied` amber (bold)

---

## Audit Trail Rules

Every meaningful event writes a row to `audit_logs`. This table is **append-only and never deleted**.

| Event | Trigger |
|---|---|
| `lead_received` | Lead row created in route handler |
| `lead_processing` | process_lead task starts |
| `email_sent` | Day 0 SES send succeeded |
| `email_failed` | SES or Claude raised an exception |
| `crm_synced` | HubSpot upsert succeeded |
| `crm_sync_failed` | HubSpot raised an exception |
| `followup_sent` | Beat task sent a follow-up email |
| `followup_email_failed` | Beat task Claude or SES error |
| `sequence_completed` | Final step sent, enrollment closed |
| `enrollment_bounce` | Permanent bounce received via webhook |
| `enrollment_complaint` | Spam complaint received via webhook |
| `enrollment_replied` | Lead replied — sequence stopped, admin alerted |

The audit trail is the source of truth for debugging, billing, compliance, and dashboards.

---

## Error Handling Philosophy

| External call | On success | On failure |
|---|---|---|
| Claude API | Generate email body | Log to audit_logs, update lead status → email_failed, Celery retries up to 3× |
| AWS SES | Send email, write EmailLog | Log to audit_logs, update lead status → email_failed, Celery retries |
| HubSpot | Write crm_contact_id to lead | Log to audit_logs — pipeline continues, no retry (non-critical path) |
| SNS webhook (bounce/complaint/reply) | Pause or mark replied enrollment | Log and return gracefully — never raise 5xx to SNS |

**Core rule: the pipeline must never crash over a CRM failure.** The email was already sent. HubSpot is a nice-to-have on Day 0.

**Core rule: the webhook must always return 2xx.** SNS retries on anything other than 2xx. Invalid or unrecognised messages are logged and return `{"status": "ignored"}` — never a 4xx or 5xx.

---

## Configuration Model

All tenant-specific and campaign-specific behaviour lives in JSON config files. **No hardcoded tenant logic anywhere in the codebase.**

| Config file | Contains |
|---|---|
| `config/tenants/{slug}.json` | Company name, SES from/reply-to, HubSpot IDs, signature |
| `config/campaigns/{slug}.json` | Number of steps, delay days per step, subject templates, Claude prompt templates |

**Adding a new tenant requires zero code changes.** Copy the tenant JSON, copy the campaign JSON, update the values. The endpoint `POST /api/v1/leads/{new_slug}` is live immediately.

---

## Multi-Tenancy Rules

- Every database row has a `tenant_id` foreign key — data never mixes across tenants
- Tenant lookup happens by slug from the URL path (`/api/v1/leads/africa_travel`)
- If the slug doesn't exist or the tenant is inactive → 404
- Campaign enrollment always belongs to a specific tenant + campaign combination
- Admin API is scoped to a tenant slug — operators only see their own data

---

## Sample Email — Day 0 Welcome (Africa Horizons Travel)

**Lead profile used for this example:**
- Name: Sarah Thompson
- Destination: Kenya — Masai Mara
- Travel: July 2026
- Group: 2 adults, honeymoon
- Accommodation: Luxury tented camp
- Budget: £5,000–£10,000 per person
- Special requests: Anniversary surprise element if possible

---

**Subject:** Your Kenya Masai Mara journey — a first look, Sarah

---

**Email body (HTML rendered as plain text for readability):**

Hello Sarah,

We are The Africa Horizons Travel Design Team and I am part of the Africa Horizons Travel Design Team — your dedicated journey creators at www.africahorizonstravel.com.

July in the Masai Mara is one of Africa's most extraordinary spectacles. The Great Wildebeest Migration is at its dramatic peak — vast golden plains alive with thundering herds crossing the Mara River, lion hunts in amber afternoon light, and mornings that begin with birdsong inside your luxury tented camp. We have noted your anniversary surprise request, and our designers already have ideas that will make those moments utterly unforgettable.

One of our dedicated Travel Designers will be reaching out to you personally via email and phone within 24 hours to begin crafting your tailored itinerary. In the meantime, please reply with any questions, wishes, or ideas — we are here and listening.

With warmth and wanderlust,
The Africa Horizons Travel Design Team
Africa Horizons Travel
www.africahorizonstravel.com

---

> **Note on sample:** This is a representative example of what Claude generates. Actual output will vary slightly per lead based on all form fields provided.
> **Email format rules:** Opens with "Hello, {first_name}," → intro line with company website → 2 short body paragraphs → CTA mentions "via email and phone" → signature includes website.

---

## Technology Stack

| Layer | Technology | Why |
|---|---|---|
| API | FastAPI (async) | High performance, native async, Pydantic validation built in |
| Database | PostgreSQL 15 + SQLAlchemy 2.0 (async) | Reliable, JSONB for flexible form data, async for FastAPI |
| Background jobs | Celery 5.4 + Redis | Industry standard for distributed task queues |
| Email | AWS SES via boto3 | Cheap, reliable, handles bounce/complaint management |
| LLM | Anthropic Claude API | Best-in-class for nuanced, personalised long-form writing |
| CRM | HubSpot REST API | Most common SMB CRM — easy to demonstrate to clients |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| Infra | Docker + docker-compose | Reproducible local dev, easy to demo to clients |

---

## Phase Status

| Phase | Description | Status |
|---|---|---|
| 1A | DB models, Docker, health endpoint, migrations | **Complete** |
| 1B | Services, Celery tasks, API route, tenant/campaign configs, frontend form, live email pipeline | **Complete** |
| 1C | Production hardening — failure alerting, SES bounce webhook, rate limiting, seed script, structured logging | **Complete** |
| 2 | Multi-tenant admin dashboard — leads view, email history, cost tracking, KPI dashboard | **Complete** |
| 3 | Reply detection — SES inbound webhook, enrollment marked replied, admin alert | **Complete** |

---

## How to Add a New Tenant

1. Copy `backend/app/config/tenants/africa_travel.json` → `<new_slug>.json`
2. Copy `backend/app/config/campaigns/africa_14day.json` → `<new_slug>_campaign.json`
3. Update company name, prompts, branding, and CRM config in both files
4. Run `docker-compose exec api python seed.py` — inserts the new tenant row
5. The endpoint is live with no code changes: `POST /api/v1/leads/<new_slug>`

---

## Deployment Checklist

```bash
# 1. Start services
docker-compose up --build

# 2. Apply migrations
docker-compose exec api alembic upgrade head

# 3. Seed tenant rows from config/tenants/*.json (idempotent)
docker-compose exec api python seed.py

# 4. Verify API is up
curl http://localhost:8000/api/v1/health

# 5. Submit a test lead
curl -X POST http://localhost:8000/api/v1/leads/africa_travel \
  -H "Content-Type: application/json" \
  -d '{"full_name":"Test User","email":"test@example.com","phone_country_code":"+27","phone_number":"0821234567","preferred_contact_method":"Email","destination":"Botswana","travel_month":"June","travel_year":"2026","adults":"2"}'

# 6. Verify admin API
curl -H "X-Admin-Key: <ADMIN_API_KEY>" http://localhost:8000/api/v1/admin/africa_travel/dashboard

# 7. Open admin dashboard
# admin/index.html — any static server at port 5500 or 8080 works
# Login: tenant=africa_travel, key=<ADMIN_API_KEY>
```

---

## Open Questions / Future Decisions

- [ ] Should lead_score drive routing logic (e.g., high-score leads get a WhatsApp outreach on Day 1)?
- [ ] Should the system support A/B testing of email sequences (different prompt templates for split testing)?
- [ ] Should `email_failed` leads be surfaced more prominently in the admin dashboard (not just buried in audit trail)?
- [ ] Should the admin dashboard support a webhook test tool (fire a simulated bounce/reply to verify the flow)?

---

*Update this document whenever: a business rule changes, a new tenant is added, the email sequence is modified, or a new phase begins.*
