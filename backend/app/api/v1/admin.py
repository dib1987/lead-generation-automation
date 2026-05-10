import csv
import io
import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.lead import Lead
from app.models.email_log import EmailLog
from app.models.audit_log import AuditLog
from app.models.campaign_enrollment import CampaignEnrollment
from app.schemas.admin import (
    DashboardResponse,
    LeadListResponse,
    LeadSummary,
    LeadDetailResponse,
    EmailLogSummary,
    EmailLogListResponse,
    AuditEntry,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


async def verify_admin_key(x_admin_key: str = Header(...)):
    if settings.admin_api_key and x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


async def _get_tenant(tenant_slug: str, session: AsyncSession) -> Tenant:
    result = await session.execute(
        select(Tenant).where(Tenant.slug == tenant_slug, Tenant.is_active == True)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_slug}' not found")
    return tenant


@router.get("/{tenant_slug}/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    tenant_slug: str,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    total_result = await session.execute(
        select(func.count()).where(Lead.tenant_id == tenant.id)
    )
    total_leads = total_result.scalar_one()

    status_result = await session.execute(
        select(Lead.status, func.count().label("cnt"))
        .where(Lead.tenant_id == tenant.id)
        .group_by(Lead.status)
    )
    leads_by_status = {row.status: row.cnt for row in status_result}

    emails_result = await session.execute(
        select(func.count()).where(EmailLog.tenant_id == tenant.id)
    )
    emails_sent = emails_result.scalar_one()

    score_result = await session.execute(
        select(func.avg(Lead.lead_score))
        .where(Lead.tenant_id == tenant.id, Lead.lead_score.isnot(None))
    )
    avg_score_raw = score_result.scalar_one()
    avg_lead_score = float(round(avg_score_raw, 1)) if avg_score_raw is not None else None

    booked_count = leads_by_status.get("booked", 0)

    return DashboardResponse(
        total_leads=total_leads,
        leads_by_status=leads_by_status,
        emails_sent=emails_sent,
        avg_lead_score=avg_lead_score,
        booked_count=booked_count,
    )


@router.get("/{tenant_slug}/leads", response_model=LeadListResponse)
async def list_leads(
    tenant_slug: str,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by email address"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    base_filter = [Lead.tenant_id == tenant.id]
    if status:
        base_filter.append(Lead.status == status)
    if q:
        base_filter.append(Lead.email_address.ilike(f"%{q}%"))

    count_result = await session.execute(
        select(func.count()).select_from(Lead).where(*base_filter)
    )
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    leads_result = await session.execute(
        select(Lead)
        .where(*base_filter)
        .order_by(Lead.created_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    leads = leads_result.scalars().all()

    items = [
        LeadSummary(
            id=lead.id,
            email_address=lead.email_address,
            status=lead.status,
            lead_score=lead.lead_score,
            created_at=lead.created_at,
            full_name=lead.form_data.get("full_name", ""),
            destination=lead.form_data.get("destination", ""),
            utm_source=lead.utm_source,
        )
        for lead in leads
    ]

    return LeadListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{tenant_slug}/leads/export")
async def export_leads_csv(
    tenant_slug: str,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search by email address"),
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    base_filter = [Lead.tenant_id == tenant.id]
    if status:
        base_filter.append(Lead.status == status)
    if q:
        base_filter.append(Lead.email_address.ilike(f"%{q}%"))

    leads_result = await session.execute(
        select(Lead).where(*base_filter).order_by(Lead.created_at.desc())
    )
    leads = leads_result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Name", "Email", "Destination", "Status", "Score",
        "UTM Source", "UTM Medium", "UTM Campaign",
        "Booked At", "Created At",
    ])
    for lead in leads:
        writer.writerow([
            lead.form_data.get("full_name", ""),
            lead.email_address,
            lead.form_data.get("destination", ""),
            lead.status,
            lead.lead_score if lead.lead_score is not None else "",
            lead.utm_source or "",
            lead.utm_medium or "",
            lead.utm_campaign or "",
            lead.booked_at.isoformat() if lead.booked_at else "",
            lead.created_at.isoformat(),
        ])

    filename = f"leads_{tenant_slug}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{tenant_slug}/leads/{lead_id}", response_model=LeadDetailResponse)
async def get_lead_detail(
    tenant_slug: str,
    lead_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    lead_result = await session.execute(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant.id)
    )
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    email_result = await session.execute(
        select(EmailLog)
        .where(EmailLog.lead_id == lead_id, EmailLog.tenant_id == tenant.id)
        .order_by(EmailLog.sent_at)
    )
    email_logs = [EmailLogSummary.model_validate(e) for e in email_result.scalars().all()]

    audit_result = await session.execute(
        select(AuditLog)
        .where(AuditLog.lead_id == lead_id, AuditLog.tenant_id == tenant.id)
        .order_by(AuditLog.created_at)
    )
    audit_trail = [
        AuditEntry(
            event=a.event,
            old_status=a.old_status,
            new_status=a.new_status,
            meta=a.meta,
            created_at=a.created_at,
        )
        for a in audit_result.scalars().all()
    ]

    return LeadDetailResponse(
        id=lead.id,
        email_address=lead.email_address,
        status=lead.status,
        lead_score=lead.lead_score,
        crm_contact_id=lead.crm_contact_id,
        form_data=lead.form_data,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        booked_at=lead.booked_at,
        email_logs=email_logs,
        audit_trail=audit_trail,
        utm_source=lead.utm_source,
        utm_medium=lead.utm_medium,
        utm_campaign=lead.utm_campaign,
    )


@router.patch("/{tenant_slug}/leads/{lead_id}/mark-booked", response_model=LeadDetailResponse)
async def mark_lead_booked(
    tenant_slug: str,
    lead_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    lead_result = await session.execute(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant.id)
    )
    lead = lead_result.scalar_one_or_none()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "booked":
        raise HTTPException(status_code=400, detail="Lead is already marked as booked")

    old_status = lead.status
    lead.status = "booked"
    lead.booked_at = datetime.utcnow()

    enrollment_result = await session.execute(
        select(CampaignEnrollment)
        .where(
            CampaignEnrollment.lead_id == lead_id,
            CampaignEnrollment.status == "active",
        )
        .limit(1)
    )
    active_enrollment = enrollment_result.scalars().first()
    if active_enrollment:
        active_enrollment.status = "paused"

    audit = AuditLog(
        tenant_id=tenant.id,
        lead_id=lead.id,
        event="lead_booked",
        old_status=old_status,
        new_status="booked",
        meta={"source": "admin"},
    )
    session.add(audit)
    await session.commit()
    await session.refresh(lead)

    email_result = await session.execute(
        select(EmailLog)
        .where(EmailLog.lead_id == lead_id, EmailLog.tenant_id == tenant.id)
        .order_by(EmailLog.sent_at)
    )
    email_logs = [EmailLogSummary.model_validate(e) for e in email_result.scalars().all()]

    audit_result = await session.execute(
        select(AuditLog)
        .where(AuditLog.lead_id == lead_id, AuditLog.tenant_id == tenant.id)
        .order_by(AuditLog.created_at)
    )
    audit_trail = [
        AuditEntry(
            event=a.event,
            old_status=a.old_status,
            new_status=a.new_status,
            meta=a.meta,
            created_at=a.created_at,
        )
        for a in audit_result.scalars().all()
    ]

    return LeadDetailResponse(
        id=lead.id,
        email_address=lead.email_address,
        status=lead.status,
        lead_score=lead.lead_score,
        crm_contact_id=lead.crm_contact_id,
        form_data=lead.form_data,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
        booked_at=lead.booked_at,
        email_logs=email_logs,
        audit_trail=audit_trail,
        utm_source=lead.utm_source,
        utm_medium=lead.utm_medium,
        utm_campaign=lead.utm_campaign,
    )


@router.get("/{tenant_slug}/email-logs", response_model=EmailLogListResponse)
async def list_email_logs(
    tenant_slug: str,
    status: Optional[str] = Query(None),
    lead_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    tenant = await _get_tenant(tenant_slug, session)

    base_filter = [EmailLog.tenant_id == tenant.id]
    if status:
        base_filter.append(EmailLog.status == status)
    if lead_id:
        base_filter.append(EmailLog.lead_id == lead_id)

    count_result = await session.execute(
        select(func.count()).select_from(EmailLog).where(*base_filter)
    )
    total = count_result.scalar_one()

    offset = (page - 1) * page_size
    logs_result = await session.execute(
        select(EmailLog)
        .where(*base_filter)
        .order_by(EmailLog.sent_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    items = [EmailLogSummary.model_validate(e) for e in logs_result.scalars().all()]

    return EmailLogListResponse(items=items, total=total, page=page, page_size=page_size)
