"""
POST /api/v1/leads/{tenant_slug}

Validates the form payload, runs a dedup check, creates a Lead row,
enqueues the process_lead Celery task, and returns 202.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.lead import LeadCreateRequest, LeadResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])


@router.post("/{tenant_slug}", status_code=202, response_model=LeadResponse)
async def create_lead(
    tenant_slug: str,
    payload: LeadCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> LeadResponse:
    # Model imports are inside the function to avoid circular imports at module load time.
    # Python caches modules after the first load so there is no runtime overhead.
    from app.models.campaign_enrollment import CampaignEnrollment
    from app.models.lead import Lead
    from app.models.tenant import Tenant

    # 1. Look up tenant
    result = await session.execute(
        select(Tenant)
        .where(Tenant.slug == tenant_slug)
        .where(Tenant.is_active.is_(True))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_slug}' not found")

    email = str(payload.email).lower()
    now = datetime.now(timezone.utc)

    # 2. Dedup check — look for the most recent lead from this email for this tenant
    existing_result = await session.execute(
        select(Lead)
        .where(Lead.tenant_id == tenant.id)
        .where(Lead.email_address == email)
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    existing = existing_result.scalars().first()

    if existing:
        age = now - existing.created_at.replace(tzinfo=timezone.utc)

        # Submitted < 24h ago and not completed → already in the pipeline
        if age < timedelta(hours=24) and existing.status != "completed":
            return LeadResponse(
                id=existing.id,
                status="already_enrolled",
                created_at=existing.created_at,
                message="We already have your enquiry and our team is working on it. You will hear from us very soon.",
            )

        # Submitted 24h–30d ago → check for active enrollment
        if timedelta(hours=24) <= age <= timedelta(days=30):
            enrollment_result = await session.execute(
                select(CampaignEnrollment)
                .where(CampaignEnrollment.lead_id == existing.id)
                .where(CampaignEnrollment.status == "active")
                .limit(1)
            )
            active_enrollment = enrollment_result.scalars().first()
            if active_enrollment:
                return LeadResponse(
                    id=existing.id,
                    status="already_submitted",
                    created_at=existing.created_at,
                    message="You are already part of our journey sequence. Keep an eye on your inbox — we will be in touch.",
                )

    # 3. Create new Lead row
    form_data = payload.model_dump(mode="json")
    lead = Lead(
        tenant_id=tenant.id,
        email_address=email,
        form_data=form_data,
        status="received",
    )
    session.add(lead)
    await session.flush()   # get the UUID
    await session.commit()  # commit BEFORE enqueuing — Celery task must find the row in DB

    # 4. Enqueue Celery task — imported here to avoid module-level circular import
    from app.workers.tasks.process_lead import process_lead
    process_lead.delay(str(lead.id))

    logger.info("Lead created and queued: id=%s tenant=%s email=%s", lead.id, tenant_slug, email)

    return LeadResponse(
        id=lead.id,
        status="received",
        created_at=lead.created_at,
        message="Thank you for your enquiry. You will receive a personalised email from us within the next few minutes.",
    )
