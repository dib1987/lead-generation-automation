"""
Beat-driven follow-up task: fires every 15 minutes.

Fully synchronous — uses psycopg2 via sync_session, compatible with Celery prefork.

Finds all CampaignEnrollments where next_send_at <= now() and status = active,
then sends the next email in the sequence for each one.
Each enrollment is processed independently — a failure on one does not block others.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.sync_session import get_sync_session
from app.models.campaign import Campaign
from app.models.campaign_enrollment import CampaignEnrollment
from app.models.lead import Lead
from app.models.tenant import Tenant
from app.services import audit_service, email_service, llm_service
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _load_campaign_config(slug: str) -> dict:
    path = _CONFIG_DIR / "campaigns" / f"{slug}.json"
    return json.loads(path.read_text())


def _load_tenant_config(slug: str) -> dict:
    path = _CONFIG_DIR / "tenants" / f"{slug}.json"
    return json.loads(path.read_text())


def _process_enrollment(enrollment_id: uuid.UUID) -> None:
    """Process a single due enrollment in its own DB session."""
    with get_sync_session() as session:
        enrollment = session.get(CampaignEnrollment, enrollment_id)
        if not enrollment or enrollment.status != "active":
            return

        lead = session.get(Lead, enrollment.lead_id)
        tenant = session.get(Tenant, enrollment.tenant_id)
        campaign = session.get(Campaign, enrollment.campaign_id)

        if not all([lead, tenant, campaign]):
            logger.error("run_followup: missing related record for enrollment %s", enrollment_id)
            return

        tenant_config = _load_tenant_config(tenant.slug)
        campaign_config = _load_campaign_config(campaign.slug)

        next_step_index = enrollment.current_step + 1
        if next_step_index >= len(campaign_config["steps"]):
            enrollment.status = "completed"
            enrollment.completed_at = datetime.now(timezone.utc)
            lead.status = "completed"
            audit_service.write_audit_log(
                session, enrollment.tenant_id, enrollment.lead_id,
                "sequence_completed", "email_sent", "completed",
            )
            return

        step_config = campaign_config["steps"][next_step_index]

        # Generate email via Claude
        try:
            subject, html_body = llm_service.generate_email(
                session=session,
                tenant_id=enrollment.tenant_id,
                lead_id=enrollment.lead_id,
                step_config=step_config,
                form_data=lead.form_data,
                tenant_config=tenant_config,
            )
        except Exception as exc:
            logger.error(
                "run_followup: Claude failed for enrollment %s step %d: %s",
                enrollment_id, next_step_index, exc,
            )
            audit_service.write_audit_log(
                session, enrollment.tenant_id, enrollment.lead_id,
                "followup_email_failed", lead.status, lead.status,
                {"error": str(exc), "step": next_step_index, "stage": "llm"},
            )
            return

        # Send via SES
        try:
            email_service.send_email(
                session=session,
                tenant_id=enrollment.tenant_id,
                lead_id=enrollment.lead_id,
                to_address=lead.email_address,
                subject=subject,
                html_body=html_body,
                tenant_config=tenant_config,
                step_number=next_step_index,
                campaign_enrollment_id=enrollment.id,
            )
        except Exception as exc:
            logger.error(
                "run_followup: SES failed for enrollment %s step %d: %s",
                enrollment_id, next_step_index, exc,
            )
            audit_service.write_audit_log(
                session, enrollment.tenant_id, enrollment.lead_id,
                "followup_email_failed", lead.status, lead.status,
                {"error": str(exc), "step": next_step_index, "stage": "ses"},
            )
            return

        # Advance enrollment
        enrollment.current_step = next_step_index
        is_last_step = (next_step_index + 1) >= len(campaign_config["steps"])

        if is_last_step:
            enrollment.status = "completed"
            enrollment.completed_at = datetime.now(timezone.utc)
            lead.status = "completed"
            audit_service.write_audit_log(
                session, enrollment.tenant_id, enrollment.lead_id,
                "sequence_completed", "email_sent", "completed",
                {"step": next_step_index},
            )
        else:
            next_step_config = campaign_config["steps"][next_step_index + 1]
            # enrolled_at is timezone-naive from DB — add UTC tzinfo before arithmetic
            enrolled_at = enrollment.enrolled_at
            if enrolled_at.tzinfo is None:
                enrolled_at = enrolled_at.replace(tzinfo=timezone.utc)
            enrollment.next_send_at = enrolled_at + timedelta(days=next_step_config["delay_days"])
            audit_service.write_audit_log(
                session, enrollment.tenant_id, enrollment.lead_id,
                "followup_sent", lead.status, lead.status,
                {"step": next_step_index, "next_send_at": enrollment.next_send_at.isoformat()},
            )

        logger.info(
            "run_followup: sent step %d for enrollment %s (last=%s)",
            next_step_index, enrollment_id, is_last_step,
        )


@celery_app.task(name="workers.tasks.run_followup.run_followup")
def run_followup() -> None:
    """Celery Beat task — finds all due enrollments and sends their next email."""
    logger.info("run_followup: tick")
    now = datetime.now(timezone.utc)

    with get_sync_session() as session:
        due_ids = session.execute(
            select(CampaignEnrollment.id)
            .where(CampaignEnrollment.status == "active")
            .where(CampaignEnrollment.next_send_at <= now)
        ).scalars().all()

    logger.info("run_followup: %d enrollment(s) due", len(due_ids))

    for enrollment_id in due_ids:
        try:
            _process_enrollment(enrollment_id)
        except Exception as exc:
            logger.error("run_followup: unhandled error for enrollment %s: %s", enrollment_id, exc)
