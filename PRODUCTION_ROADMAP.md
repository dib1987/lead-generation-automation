# Production Roadmap — Lead Generation System
Last updated: 2026-05-06

This document covers everything needed to take the system from a working local demo to a production-grade lead generation platform a real company would trust.

Phases 1–3 are complete. This roadmap covers Phase 4 onwards.

---

## Phase 4 — Conversion Tracking & Lead Management
**Priority: HIGH — needed before any client demo that involves sales conversations**

### 4A — True Conversion Tracking
**The problem:** "Sequence Completed" just means all 6 emails were sent. There is no way to mark a lead as actually booked/converted.

**What to build:**
- Add `booked` status to the lead status enum
- New API endpoint: `POST /api/v1/admin/{tenant}/leads/{id}/convert`
  - Sets `lead.status = "booked"`, writes audit log entry
  - Requires `X-Admin-Key` header (same as all admin routes)
- Admin Leads detail panel: add a green **"Mark as Booked ✓"** button
  - Only visible on leads with `email_sent` or `completed` status
  - Replaces itself with a "Booked" badge after click
- Dashboard KPI: add a new **"Booked"** card counting `status = 'booked'`

**Files to touch:**
- `backend/app/models/lead.py` — add `booked` to status values
- `backend/app/api/v1/admin.py` — new `/convert` endpoint
- `backend/app/schemas/admin.py` — add booked to response schemas
- `admin/index.html` — button in detail panel, new KPI card

---

### 4B — Internal Lead Notes
**The problem:** When a travel designer calls a prospect, there is nowhere to record what was discussed.

**What to build:**
- New `lead_notes` DB table: `id, tenant_id, lead_id, author, body, created_at`
- New Alembic migration
- API endpoints:
  - `POST /api/v1/admin/{tenant}/leads/{id}/notes` — add note
  - `GET /api/v1/admin/{tenant}/leads/{id}/notes` — list notes (already part of lead detail)
- Admin detail panel: notes section with textarea + "Add Note" button, notes list below

---

### 4C — UTM / Lead Source Tracking
**The problem:** You don't know if leads came from Google Ads, a Facebook post, or a referral link.

**What to build:**
- Frontend form: on page load, read `utm_source`, `utm_medium`, `utm_campaign`, `utm_content` from URL query params and store in hidden fields
- Include UTM fields in the JSON payload to the API
- `leads` table: add `source`, `medium`, `campaign` columns (migration required)
- Admin leads table: add a "Source" column

**Example URLs:**
```
http://127.0.0.1:5500/frontend/index.html?utm_source=instagram&utm_campaign=kenya_q4
```

---

### 4D — CSV Export
**The problem:** No way to bulk-export leads for offline analysis or import into Excel/CRM.

**What to build:**
- New API endpoint: `GET /api/v1/admin/{tenant}/leads/export.csv`
  - Streams a CSV with all lead fields + form_data flattened
  - Respects existing status/email filters as query params
- Admin dashboard: "Export CSV" button in the Leads tab header

---

## Phase 5 — Email Compliance & Intelligence
**Priority: HIGH — unsubscribe is a legal requirement (CAN-SPAM / GDPR)**

### 5A — Unsubscribe Link (LEGAL REQUIREMENT)
**What to build:**
- New `lead_preferences` table: `lead_id, unsubscribed_at, unsubscribe_token (UUID)`
- New migration
- New endpoint: `GET /api/v1/unsubscribe/{token}` — sets `unsubscribed_at`, returns a plain HTML confirmation page
- `run_followup` task: check `unsubscribed_at` before sending each follow-up — skip if set
- All prompt templates: add instruction to include an unsubscribe link at the bottom
  ```
  Format: ... At the very bottom, add a plain-text line:
  <p style="font-size:11px;color:#999;">
    <a href="{unsubscribe_url}">Unsubscribe</a> from Africa Horizons Travel emails.
  </p>
  ```
- `llm_service.py` / `email_service.py`: inject `unsubscribe_url` into variables before rendering

---

### 5B — Email Open Tracking
**What to build:**
- New endpoint: `GET /api/v1/track/open/{email_log_id}.png` — returns a 1x1 transparent GIF, logs `opened_at` on `email_logs`
- `email_logs` table: add `opened_at` nullable DateTime column (migration)
- `email_service.py`: append `<img src="{tracking_url}/{log_id}.png" width="1" height="1">` to every email body before sending
- Admin Emails tab: add "Opened" column with timestamp

---

### 5C — Click Tracking
**What to build:**
- New endpoint: `GET /api/v1/track/click/{email_log_id}?url={encoded_url}` — logs click, redirects to destination
- `email_logs` table: add `clicked_at`, `click_count` columns
- `email_service.py`: rewrite all `<a href="...">` links through the tracking proxy before sending
- Admin Emails tab: show click indicator

---

## Phase 6 — Production Infrastructure
**Priority: HIGH — required before any real traffic or client data**

### 6A — SES Production Access
**The problem:** SES sandbox mode blocks emails to unverified addresses. Every real customer will hit this.

**Steps (manual AWS ops):**
1. AWS Console → SES → Account Dashboard → Request production access
2. Fill in the use-case form: transactional emails, lead nurture sequences, opt-in form
3. Typical approval: 24–48 hours
4. Once approved: no email verification needed for recipients

**Until then:** verify each test email address individually in SES Verified Identities.

---

### 6B — HTTPS / Nginx Reverse Proxy
**What to build (for local demo / staging):**
```nginx
# nginx.conf
server {
    listen 443 ssl;
    server_name your-domain.com;
    ssl_certificate     /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;

    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        root /var/www/frontend;
        try_files $uri $uri/ /index.html;
    }
}
```
- Add `nginx` service to `docker-compose.yml`
- Serve `frontend/` and `admin/` as static files through Nginx
- Remove the need for the Python static server hack

---

### 6C — Environment Separation
**What to build:**
- `.env.development` — local defaults (current `.env`)
- `.env.staging` — staging server values
- `.env.production` — production values (never committed to git)
- `docker-compose.yml`: use `env_file: .env.${ENVIRONMENT:-development}`
- `frontend/index.html`: `API_BASE` should be injected at build/deploy time, not hardcoded

---

### 6D — Error Monitoring (Sentry)
**What to build:**
- Add `sentry-sdk[fastapi,celery]` to `requirements.txt`
- `main.py`: `sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)`
- `celery_app.py`: same init
- New env var: `SENTRY_DSN`
- Every unhandled exception in FastAPI and Celery workers will auto-report

---

### 6E — Database Backup
**What to build:**
- Add a `backup` service to `docker-compose.yml` using `postgres:15-alpine`
- Daily `pg_dump` to S3 via a cron-based container
- Alternatively: enable RDS automated backups if migrating to AWS RDS

---

### 6F — Structured Health Checks & Uptime Monitoring
**What to build:**
- Expand `GET /api/v1/health` to include Redis ping and Celery queue depth
- Register the health endpoint with UptimeRobot (free tier) or AWS Route 53 Health Checks
- Alert to `ADMIN_ALERT_EMAIL` if health check fails for >2 minutes

---

## Phase 7 — Advanced Lead Gen Features
**Priority: MEDIUM — differentiators for enterprise clients**

### 7A — WhatsApp Follow-up (Twilio)
- After step 0 email is sent, optionally send a WhatsApp message via Twilio API
- Only fires if `preferred_contact_method == "WhatsApp"` in form data
- New env var: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`
- New Celery task: `send_whatsapp.py`

### 7B — Calendly / Booking CTA in Emails
- Add `calendly_url` to tenant config JSON
- Inject a "Book a 15-min call" button in step 1 email (destination spotlight)
- Track clicks via the click tracking endpoint from Phase 5C

### 7C — HubSpot Bi-directional Sync
- Currently: one-way upsert on `email_sent`
- Add: sync HubSpot deal stage back to `lead.status` via HubSpot webhooks
- New endpoint: `POST /api/v1/webhooks/hubspot`

### 7D — Lead Scoring Refinement
- Move scoring weights to `tenant config JSON` so each tenant can tune them
- Add scoring dimensions: reply = +30 points, email open = +5, click = +10
- Expose score breakdown in admin lead detail panel

### 7E — A/B Testing Email Subjects
- Add `subject_variants` array to campaign step config
- Randomly assign variant on send
- Track open rates per variant in `email_logs`
- Admin Emails tab: show which variant was used

---

## Phase 8 — Multi-tenant SaaS
**Priority: LOW — needed only when selling to multiple clients**

### 8A — Tenant Management UI
- Admin page to create/edit/deactivate tenants without editing JSON files
- New API endpoints: `POST/PUT/DELETE /api/v1/admin/tenants`
- Replaces manual `seed.py` + JSON editing workflow

### 8B — Per-tenant Custom Domains
- Each tenant's form served at `https://client.yourdomain.com`
- Nginx vhost routing by subdomain → tenant slug

### 8C — White-label Admin Dashboard
- Per-tenant branding (logo, colors) in the admin panel
- Tenant-specific login URL

### 8D — Usage & Cost Dashboard per Tenant
- Already have `llm_cost_logs` — surface this per tenant
- Monthly cost summary, email volume, lead volume
- Useful for SaaS billing if charging per lead/email

---

## Recommended Implementation Order

| Order | Phase | Why |
|---|---|---|
| 1 | **6A** — SES production access | Blocks real testing with any email address |
| 2 | **5A** — Unsubscribe link | Legal requirement — do before any real leads |
| 3 | **4A** — Conversion tracking | Makes the demo meaningful for sales conversations |
| 4 | **4C** — UTM tracking | Low effort, high demo value — shows marketing attribution |
| 5 | **4D** — CSV export | Every client asks for this in first demo |
| 6 | **6D** — Sentry monitoring | Catches silent failures before clients report them |
| 7 | **6B** — HTTPS/Nginx | Required before any public URL |
| 8 | **5B** — Email open tracking | Adds real engagement data to the dashboard |
| 9 | **4B** — Lead notes | Useful for travel designers managing conversations |
| 10 | **7A** — WhatsApp | High differentiation for travel/hospitality niche |

---

## Quick Pre-Demo Checklist (do before every client demo)

```bash
# 1. Start services
docker-compose up -d

# 2. Start static server
python -m http.server 5500

# 3. Seed tenant (idempotent)
docker-compose exec api python seed.py

# 4. Clear old test enrollments for demo email
docker-compose exec postgres psql -U leadgen -d leadgen -c \
  "UPDATE campaign_enrollments SET status='completed' WHERE id IN \
  (SELECT ce.id FROM campaign_enrollments ce \
   JOIN leads l ON ce.lead_id=l.id \
   WHERE l.email_address='YOUR_DEMO_EMAIL' AND ce.status='active');"

# 5. Open customer form
# http://127.0.0.1:5500/frontend/index.html

# 6. Open admin dashboard
# http://127.0.0.1:5500/admin/index.html
# Tenant: africa_travel | Key: (ADMIN_API_KEY from .env)
```
