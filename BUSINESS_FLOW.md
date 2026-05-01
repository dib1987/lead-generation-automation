# Lead Generation System — Business Flow & Requirements

> **Living document.** Update this file whenever business rules, flows, or requirements change.
> Last updated: 2026-04-29

---

## What This System Does

A prospect fills in a web form. Within 60 seconds they receive a rich, AI-personalised email written specifically for them — their destination, their travel dates, their group, their budget. Over the following 14 days, five more emails fire automatically. Every action is logged. Every lead is scored. Their details land in a CRM. No human needs to be in the loop.

**Current demo tenant:** Africa Horizons Travel (luxury African safari and travel company)
**Purpose:** Portfolio project to demonstrate to potential clients and win projects.

---

## System Components

| Component | What it does |
|---|---|
| Frontend form | Captures lead data — 9 required fields + 7 optional |
| API route | Validates, deduplicates, creates the lead record, queues the task |
| process_lead (Celery task) | Day 0 pipeline — scores, generates email via Claude, sends via SES, syncs CRM |
| run_followup (Celery Beat) | Fires every 15 minutes — sends the next email in the sequence for any lead that is due |
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

### Status definitions

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
| `paused` | Bounce or complaint received — sequence halted |
| `replied` | Prospect replied to an email — sequence stops immediately |

---

## Stage 1 — The Form (Frontend)

The prospect fills in the Africa Horizons Travel lead capture form.

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

The server receives the form payload. Before creating anything it runs a **deduplication check**.

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
1. Fetch Lead + Tenant from database
2. Load tenant config from JSON: config/tenants/{slug}.json
3. Load campaign config from JSON: config/campaigns/{slug}.json
4. Safety dedup check (idempotency — Celery can retry tasks)
5. Update lead status → "processing" + write audit_log
6. Score the lead (0–100)
7. Save lead_score to database
8. Find or create Campaign DB row (bootstrapped from JSON on first run)
9. Generate Day 0 email via Claude API
10. Send email via AWS SES
11. Write EmailLog row
12. Create CampaignEnrollment (step=0, status=active, next_send_at = now + 2 days)
13. Update lead status → "email_sent" + write audit_log
14. Sync contact to HubSpot CRM (non-blocking — failure is logged, not fatal)
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

Score is saved on the Lead row. Not used for routing logic yet — available for CRM, dashboards, and future prioritisation features.

### Email generation rules (Claude API)

- Model: Claude (Anthropic API — latest Sonnet or configured model)
- Input: the campaign step's `prompt_template` + all form data as variables
- `first_name` is extracted from `full_name` (first word before space)
- `signature_name` is injected from tenant config
- Missing optional fields default to `"Not specified"` in the prompt
- Claude outputs **HTML body only** — `<p>` tags, no `<html>/<head>/<body>` envelope
- The email service wraps the body in a full HTML envelope before sending
- Every Claude call writes a row to `llm_cost_logs`:
  - `cost_usd = (input_tokens × $0.000003) + (output_tokens × $0.000015)`
  - This enables per-lead, per-tenant, per-step cost tracking

### Email sending rules (AWS SES)

- Sender: `SES_VERIFIED_SENDER` environment variable (never hardcoded)
- From name: `ses.from_name` from tenant config JSON
- Reply-to: `ses.reply_to` from tenant config JSON
- After send: writes `EmailLog` row (to address, subject, SES message ID, step number, status)
- On failure: logs to `audit_logs` with event `email_failed`, updates lead status, Celery retries up to 3×

### CRM sync rules (HubSpot)

- Upserts contact using email as the dedup key
- Maps form fields to HubSpot contact properties
- Skips `owner_id`, `pipeline_id`, `deal_stage_id` if null in tenant config (currently always null — HubSpot not yet configured for Africa Horizons)
- **Non-blocking:** if HubSpot fails (network error, bad key, service down), the failure is logged to `audit_logs` — the pipeline does NOT crash. The email was already sent; CRM sync is recoverable
- On success: saves `crm_contact_id` and `crm_synced_at` to the Lead row

---

## Stage 4 — 14-Day Follow-Up Sequence (`run_followup` Celery Beat task)

### How it runs

Celery Beat fires `run_followup` every 15 minutes.

The task queries: `SELECT * FROM campaign_enrollments WHERE next_send_at <= NOW() AND status = 'active'`

For each enrollment that is due, it:
1. Loads the lead + tenant + campaign from DB
2. Gets the next step config from the campaign JSON
3. Generates the next email via Claude
4. Sends via SES
5. Writes `EmailLog` row
6. Increments `current_step`
7. If all steps complete → enrollment `status = completed`, lead `status = completed`
8. If more steps remain → sets `next_send_at = enrolled_at + timedelta(days=next_step.delay_days)`
9. Writes `audit_log` row

**Rule: `delay_days` is always relative to `enrolled_at`, not relative to the previous email.**
Day 8 always fires 8 days after enrollment. If Day 5 was delayed by a retry, Day 8 is unaffected.

**Rule: failures on individual enrollments are isolated.**
If 20 leads are due and one fails, the other 19 still get their emails.

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

## Audit Trail Rules

Every meaningful event writes a row to `audit_logs`. This table is **append-only and never deleted**.

| Event written | Trigger |
|---|---|
| `lead_received` | Lead row created in route handler |
| `lead_processing` | process_lead task starts |
| `email_sent` | SES send succeeded |
| `email_failed` | SES or Claude raised an exception |
| `crm_synced` | HubSpot upsert succeeded |
| `crm_sync_failed` | HubSpot raised an exception |
| `followup_sent` | Beat task sent a follow-up email |
| `sequence_completed` | Final step sent, enrollment closed |

The audit trail is the source of truth for debugging, billing, compliance, and future dashboards.

---

## Error Handling Philosophy

| External call | On success | On failure |
|---|---|---|
| Claude API | Generate email body | Log to audit_logs, update lead status → email_failed, Celery retries up to 3× |
| AWS SES | Send email, write EmailLog | Log to audit_logs, update lead status → email_failed, Celery retries |
| HubSpot | Write crm_contact_id to lead | Log to audit_logs — pipeline continues, no retry (non-critical path) |

**Core rule: the pipeline must never crash over a CRM failure.** The email was already sent. HubSpot is a nice-to-have on Day 0; the prospect's experience is not.

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
> **Email format rules (as of 2026-04-29):** Opens with "Hello, {first_name}," → intro line with company website → 2 short body paragraphs → CTA mentions "via email and phone" → signature includes website.

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
| 1A | DB models, Docker, health endpoint, migrations | Complete |
| 1B | Services, Celery tasks, API route, tenant/campaign configs, frontend form | In progress |
| 1C | Production hardening, monitoring, error alerting | Not started |
| 2 | Multi-tenant admin dashboard | Not started |
| 3 | Webhook handling (HubSpot reply detection, SES bounce/complaint) | Not started |

---

## Open Questions / Future Decisions

- [ ] What should happen when a prospect replies to an email? (Phase 3 — webhook from SES/HubSpot to set enrollment status = `replied`)
- [ ] Should lead_score drive any routing logic (e.g., high-score leads get a WhatsApp outreach on Day 1)?
- [ ] Should the system support A/B testing of email sequences (different prompt templates for split testing)?
- [ ] What is the strategy for adding a second tenant? (Documented above — zero code changes needed)

---

*Update this document whenever: a business rule changes, a new tenant is added, the email sequence is modified, or a new phase begins.*
