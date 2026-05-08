"""
GET /api/v1/unsubscribe/{token}

Public endpoint — the token in the URL is the only auth mechanism.
Sets unsubscribed_at on the Lead and pauses all active CampaignEnrollments.
Returns a plain HTML confirmation page (no JSON — this is clicked from an email client).
"""
import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.campaign_enrollment import CampaignEnrollment
from app.models.lead import Lead

logger = logging.getLogger(__name__)

router = APIRouter(tags=["unsubscribe"])

_STYLE = (
    "body{font-family:Georgia,serif;max-width:500px;margin:80px auto;padding:24px;"
    "color:#2c2c2c;text-align:center;line-height:1.7}"
    "h1{font-size:24px;margin-bottom:16px}"
    "p{font-size:16px;color:#555}"
)

_PAGE_CONFIRMED = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    f"<title>Unsubscribed</title><style>{_STYLE}</style></head>"
    "<body><h1>You've been unsubscribed</h1>"
    "<p>You will no longer receive emails from this address.<br>This change is permanent.</p>"
    "</body></html>"
)

_PAGE_INVALID = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    f"<title>Link not found</title><style>{_STYLE}</style></head>"
    "<body><h1>Link not found</h1>"
    "<p>This unsubscribe link is invalid or has already been used.</p>"
    "</body></html>"
)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str, session: AsyncSession = Depends(get_db)) -> HTMLResponse:
    try:
        token_uuid = uuid.UUID(token)
    except ValueError:
        return HTMLResponse(content=_PAGE_INVALID, status_code=200)

    lead = (await session.execute(
        select(Lead).where(Lead.unsubscribe_token == token_uuid)
    )).scalars().first()

    if not lead:
        return HTMLResponse(content=_PAGE_INVALID, status_code=200)

    if lead.unsubscribed_at:
        return HTMLResponse(content=_PAGE_CONFIRMED, status_code=200)

    now = datetime.utcnow()
    lead.unsubscribed_at = now

    enrollments = (await session.execute(
        select(CampaignEnrollment)
        .where(CampaignEnrollment.lead_id == lead.id)
        .where(CampaignEnrollment.status == "active")
    )).scalars().all()

    for enrollment in enrollments:
        enrollment.status = "paused"
        session.add(AuditLog(
            tenant_id=enrollment.tenant_id,
            lead_id=lead.id,
            event="unsubscribed",
            old_status="active",
            new_status="paused",
            meta={"source": "unsubscribe_link"},
        ))

    session.add(AuditLog(
        tenant_id=lead.tenant_id,
        lead_id=lead.id,
        event="unsubscribed",
        old_status=lead.status,
        new_status=lead.status,
        meta={"source": "unsubscribe_link"},
    ))

    logger.info("unsubscribe: lead %s opted out (enrollments paused: %d)", lead.id, len(enrollments))
    return HTMLResponse(content=_PAGE_CONFIRMED, status_code=200)
