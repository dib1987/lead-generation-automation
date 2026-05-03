"""
Day 0 pipeline: called by the API route immediately after a Lead row is created.

Fully synchronous — uses psycopg2 via sync_session, compatible with Celery prefork.

Flow:
  fetch lead + tenant → load JSON configs → dedup safety check → score lead
  → generate email (Claude) → send email (SES) → create CampaignEnrollment
  → update lead status → sync CRM (non-blocking)
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.sync_session import get_sync_session
from app.models.audit_log import AuditLog
from app.models.campaign import Campaign
from app.models.campaign_enrollment import CampaignEnrollment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services import audit_service, crm_service, email_service, llm_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_tenant_config(slug: str) -> dict:
    path = _CONFIG_DIR / "tenants" / f"{slug}.json"
    return json.loads(path.read_text())


def _load_campaign_config(slug: str) -> dict:
    path = _CONFIG_DIR / "campaigns" / f"{slug}.json"
    return json.loads(path.read_text())


# ── Lead scoring ───────────────────────────────────────────────────────────────

def _score_lead(form_data: dict) -> int:
    score = 0

    budget = form_data.get("budget_range") or ""
    if "10,000" in budget or "10000" in budget:
        score += 20
    elif "5,000" in budget or "5000" in budget:
        score += 15
    elif "2,500" in budget or "2500" in budget:
        score += 10
    else:
        score += 5

    try:
        adults = int(form_data.get("adults") or 1)
    except ValueError:
        adults = 1
    if adults >= 4:
        score += 15
    elif adults >= 2:
        score += 10
    else:
        score += 5

    accommodation = (form_data.get("accommodation_preference") or "").lower()
    if "luxury" in accommodation:
        score += 15
    elif "mid" in accommodation:
        score += 10
    else:
        score += 5

    if form_data.get("trip_motivation"):
        score += 10

    if form_data.get("special_requests"):
        score += 5

    try:
        duration = int(form_data.get("trip_duration_days") or 0)
    except ValueError:
        duration = 0
    if duration >= 10:
        score += 10
    elif duration >= 7:
        score += 5

    return min(score, 100)


# ── Main pipeline ──────────────────────────────────────────────────────────────

def _run(lead_id: str) -> None:
    with get_sync_session() as session:
        # 1. Fetch lead
        lead = session.get(Lead, uuid.UUID(lead_id))
        if not lead:
            logger.error("process_lead: lead %s not found", lead_id)
            return

        # 2. Fetch tenant
        tenant = session.get(Tenant, lead.tenant_id)
        if not tenant:
            logger.error("process_lead: tenant %s not found", lead.tenant_id)
            return

        # 3. Load JSON configs
        tenant_config = _load_tenant_config(tenant.slug)
        campaign_slug = tenant_config["default_campaign_slug"]
        campaign_config = _load_campaign_config(campaign_slug)

        # 4. Safety dedup — idempotency: skip if this lead already got an email
        existing_log = session.execute(
            select(AuditLog)
            .where(AuditLog.lead_id == lead.id)
            .where(AuditLog.event == "email_sent")
            .limit(1)
        ).scalars().first()
        if existing_log:
            logger.info("process_lead: lead %s already processed — skipping", lead_id)
            return

        # 5. Mark processing + audit — commit immediately so status persists if later steps fail
        old_status = lead.status
        lead.status = "processing"
        audit_service.write_audit_log(
            session, lead.tenant_id, lead.id,
            "lead_processing", old_status, "processing",
        )
        session.commit()

        # 6. Score lead
        score = _score_lead(lead.form_data)
        lead.lead_score = score

        # 7. Find or create Campaign DB row (bootstrapped from JSON on first run)
        campaign = session.execute(
            select(Campaign)
            .where(Campaign.tenant_id == lead.tenant_id)
            .where(Campaign.slug == campaign_slug)
        ).scalars().first()
        if not campaign:
            campaign = Campaign(
                tenant_id=lead.tenant_id,
                slug=campaign_slug,
                name=campaign_config["name"],
                steps=campaign_config["steps"],
                is_active=True,
            )
            session.add(campaign)
        session.commit()

        step0 = campaign_config["steps"][0]

        # 8. Generate email via Claude
        try:
            subject, html_body = llm_service.generate_email(
                session=session,
                tenant_id=lead.tenant_id,
                lead_id=lead.id,
                step_config=step0,
                form_data=lead.form_data,
                tenant_config=tenant_config,
            )
        except Exception as exc:
            logger.error("process_lead: Claude failed for lead %s: %s", lead_id, exc)
            lead.status = "email_failed"
            audit_service.write_audit_log(
                session, lead.tenant_id, lead.id,
                "email_failed", "processing", "email_failed",
                {"error": str(exc), "stage": "llm"},
            )
            session.commit()  # persist failure state before re-raising for retry
            raise

        # 9. Send via SES
        try:
            email_service.send_email(
                session=session,
                tenant_id=lead.tenant_id,
                lead_id=lead.id,
                to_address=lead.email_address,
                subject=subject,
                html_body=html_body,
                tenant_config=tenant_config,
                step_number=0,
                campaign_enrollment_id=None,
            )
        except Exception as exc:
            logger.error("process_lead: SES failed for lead %s: %s", lead_id, exc)
            lead.status = "email_failed"
            audit_service.write_audit_log(
                session, lead.tenant_id, lead.id,
                "email_failed", "processing", "email_failed",
                {"error": str(exc), "stage": "ses"},
            )
            session.commit()  # persist failure state before re-raising for retry
            raise

        # 10. Create CampaignEnrollment
        step1_delay = campaign_config["steps"][1]["delay_days"] if len(campaign_config["steps"]) > 1 else None
        next_send_at = (
            datetime.now(timezone.utc) + timedelta(days=step1_delay)
            if step1_delay is not None
            else None
        )
        enrollment = CampaignEnrollment(
            tenant_id=lead.tenant_id,
            lead_id=lead.id,
            campaign_id=campaign.id,
            current_step=0,
            status="active",
            next_send_at=next_send_at,
        )
        session.add(enrollment)
        session.flush()

        # 11. Mark email_sent + audit — session commits when context manager exits
        lead.status = "email_sent"
        audit_service.write_audit_log(
            session, lead.tenant_id, lead.id,
            "email_sent", "processing", "email_sent",
            {"step": 0, "campaign_slug": campaign_slug, "lead_score": score},
        )

    # 12. CRM sync — new session, non-blocking failure
    try:
        contact_id = crm_service.upsert_contact(lead.form_data, tenant_config)
        if contact_id:
            with get_sync_session() as crm_session:
                crm_lead = crm_session.get(Lead, lead.id)
                crm_lead.crm_contact_id = contact_id
                crm_lead.crm_synced_at = datetime.now(timezone.utc)
                audit_service.write_audit_log(
                    crm_session, lead.tenant_id, lead.id,
                    "crm_synced", "email_sent", "email_sent",
                    {"crm_contact_id": contact_id},
                )
    except Exception as exc:
        logger.warning("process_lead: CRM sync failed for lead %s: %s", lead_id, exc)
        try:
            with get_sync_session() as crm_session:
                audit_service.write_audit_log(
                    crm_session, lead.tenant_id, lead.id,
                    "crm_sync_failed", "email_sent", "email_sent",
                    {"error": str(exc)},
                )
        except Exception:
            pass


# ── Celery task entry point ────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks.process_lead.process_lead",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_lead(self, lead_id: str) -> None:
    logger.info("process_lead: starting for lead %s", lead_id)
    try:
        _run(lead_id)
    except Exception as exc:
        logger.error("process_lead: failed for lead %s — retrying: %s", lead_id, exc)
        if self.request.retries >= self.max_retries:
            _send_failure_alert(lead_id, exc)
        raise self.retry(exc=exc)


def _send_failure_alert(lead_id: str, exc: Exception) -> None:
    try:
        email_service.send_admin_alert(
            subject=f"[ALERT] Lead processing failed: {lead_id}",
            html_body=(
                f"<p><strong>Lead ID:</strong> {lead_id}</p>"
                f"<p><strong>Error:</strong> {exc}</p>"
                f"<p>All 3 retries exhausted. Manual intervention required.</p>"
                f"<p><em>Sent by Lead Generation System</em></p>"
            ),
        )
    except Exception as alert_exc:
        logger.error("process_lead: could not send failure alert: %s", alert_exc)
