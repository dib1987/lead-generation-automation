"""
POST /api/v1/webhooks/ses

Receives SES delivery and inbound email notifications from AWS SNS.

Flow:
  SNS → POST /api/v1/webhooks/ses?token=<WEBHOOK_SECRET>
    → parse SNS envelope
    → if SubscriptionConfirmation: confirm via SubscribeURL
    → if Notification:
        Bounce (Permanent)  → pause CampaignEnrollment + audit log
        Complaint           → pause CampaignEnrollment + audit log
        Received            → match In-Reply-To → set enrollment replied + audit log + admin alert

Security: WEBHOOK_SECRET query token. Leave it empty in dev (auth skipped).

Reply detection:
  SES inbound receipt rules must be configured to publish to an SNS topic that
  delivers to this endpoint. When a lead replies to any outbound email, SES sets
  the notificationType to "Received". The handler extracts the In-Reply-To
  Message-ID, looks it up in email_logs.ses_message_id, finds the active
  CampaignEnrollment for that lead, and marks it replied.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.campaign_enrollment import CampaignEnrollment
from app.models.email_log import EmailLog
from app.services.email_service import send_admin_alert

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


# ── Auth dependency ────────────────────────────────────────────────────────────

def _verify_token(token: str = Query(default="")) -> None:
    if settings.webhook_secret and token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook token")


# ── Main endpoint ──────────────────────────────────────────────────────────────

@router.post("/ses")
async def ses_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(_verify_token),
) -> dict:
    # SNS sometimes sends Content-Type: text/plain — read raw body regardless
    raw = await request.body()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("ses_webhook: could not parse SNS body")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    msg_type = envelope.get("Type", "")

    if msg_type == "SubscriptionConfirmation":
        await _confirm_subscription(envelope)
        return {"status": "confirmed"}

    if msg_type == "Notification":
        message_str = envelope.get("Message", "{}")
        try:
            message = json.loads(message_str)
        except json.JSONDecodeError:
            logger.warning("ses_webhook: could not parse SNS Message payload")
            return {"status": "ignored"}

        notification_type = message.get("notificationType", "")

        if notification_type == "Bounce":
            recipients = [
                r.get("emailAddress", "").lower()
                for r in message.get("bounce", {}).get("bouncedRecipients", [])
            ]
            bounce_type = message.get("bounce", {}).get("bounceType", "")
            # Only pause on permanent bounces (Transient = mailbox full, may recover)
            if bounce_type == "Permanent" and recipients:
                await _pause_enrollments(session, recipients, event="bounce")
            return {"status": "handled", "type": "bounce", "recipients": recipients}

        if notification_type == "Complaint":
            recipients = [
                r.get("emailAddress", "").lower()
                for r in message.get("complaint", {}).get("complainedRecipients", [])
            ]
            if recipients:
                await _pause_enrollments(session, recipients, event="complaint")
            return {"status": "handled", "type": "complaint", "recipients": recipients}

        if notification_type == "Received":
            result = await _handle_reply(session, message)
            return {"status": "handled", "type": "received", **result}

        logger.debug("ses_webhook: unhandled notification type %r — ignoring", notification_type)
        return {"status": "ignored"}

    logger.debug("ses_webhook: unhandled SNS message type %r — ignoring", msg_type)
    return {"status": "ignored"}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _confirm_subscription(envelope: dict) -> None:
    """Call the SNS SubscribeURL to confirm the subscription."""
    url = envelope.get("SubscribeURL", "")
    if not url:
        logger.warning("ses_webhook: SubscriptionConfirmation missing SubscribeURL")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            logger.info("ses_webhook: SNS subscription confirmed (status=%d)", resp.status_code)
    except Exception as exc:
        logger.error("ses_webhook: could not confirm SNS subscription: %s", exc)


async def _handle_reply(session: AsyncSession, message: dict) -> dict:
    """
    Handle a notificationType=Received SNS message from SES inbound receipt rules.

    Extracts the In-Reply-To Message-ID from the inbound email headers, looks up
    the matching EmailLog row, marks the lead's active CampaignEnrollment as
    'replied', and fires an admin alert (a reply = hot lead).

    Returns a dict summarising the outcome for the endpoint response.
    """
    mail = message.get("mail", {})
    common = mail.get("commonHeaders", {})

    # inReplyTo may be absent (brand-new thread, not a reply)
    in_reply_to_raw = common.get("inReplyTo", "").strip()
    if not in_reply_to_raw:
        logger.debug("ses_webhook: Received notification has no In-Reply-To — ignoring")
        return {"matched": False, "reason": "no_in_reply_to"}

    # SES Message-IDs arrive wrapped in angle brackets in the In-Reply-To header
    ses_message_id = in_reply_to_raw.strip("<>")

    sender = mail.get("source", "").lower()
    subject = common.get("subject", "")

    # Look up the outbound email that triggered this reply
    email_log = (await session.execute(
        select(EmailLog).where(EmailLog.ses_message_id == ses_message_id)
    )).scalars().first()

    if not email_log:
        logger.info(
            "ses_webhook: reply received but no matching email_log for message_id=%r sender=%s",
            ses_message_id, sender,
        )
        return {"matched": False, "reason": "unknown_message_id", "ses_message_id": ses_message_id}

    lead_id = email_log.lead_id

    # Find the active enrollment — mark it replied
    enrollments = (await session.execute(
        select(CampaignEnrollment)
        .where(CampaignEnrollment.lead_id == lead_id)
        .where(CampaignEnrollment.status == "active")
    )).scalars().all()

    now = datetime.now(timezone.utc)
    for enrollment in enrollments:
        enrollment.status = "replied"
        enrollment.replied_at = now
        session.add(AuditLog(
            tenant_id=enrollment.tenant_id,
            lead_id=lead_id,
            event="enrollment_replied",
            old_status="active",
            new_status="replied",
            meta={
                "sender": sender,
                "subject": subject,
                "in_reply_to": ses_message_id,
                "source": "ses_inbound",
            },
        ))
        logger.info(
            "ses_webhook: enrollment %s marked replied for lead %s (sender=%s)",
            enrollment.id, lead_id, sender,
        )

    # Alert admin — a reply is a high-intent signal.
    # send_admin_alert is sync (boto3), so run in a thread to avoid blocking the event loop.
    asyncio.create_task(asyncio.to_thread(
        send_admin_alert,
        subject=f"[Lead Reply] {sender} replied — {subject[:60]}",
        html_body=(
            f"<p>A lead has replied to one of your emails.</p>"
            f"<p><strong>From:</strong> {sender}<br>"
            f"<strong>Subject:</strong> {subject}<br>"
            f"<strong>Lead ID:</strong> {lead_id}</p>"
            f"<p>Their follow-up sequence has been stopped automatically.</p>"
        ),
    ))

    return {
        "matched": True,
        "lead_id": str(lead_id),
        "sender": sender,
        "enrollments_updated": len(enrollments),
    }


async def _pause_enrollments(session: AsyncSession, email_addresses: list[str], event: str) -> None:
    """
    For each email address, find all active campaign enrollments and pause them.
    Also mark the most recent email_log row as bounced/complained.
    """
    for email in email_addresses:
        # Find lead IDs that received email to this address
        log_rows = (await session.execute(
            select(EmailLog).where(EmailLog.to_address == email)
        )).scalars().all()

        lead_ids = {row.lead_id for row in log_rows}

        for lead_id in lead_ids:
            # Find active enrollments for this lead
            enrollments = (await session.execute(
                select(CampaignEnrollment)
                .where(CampaignEnrollment.lead_id == lead_id)
                .where(CampaignEnrollment.status == "active")
            )).scalars().all()

            for enrollment in enrollments:
                enrollment.status = "paused"
                session.add(AuditLog(
                    tenant_id=enrollment.tenant_id,
                    lead_id=lead_id,
                    event=f"enrollment_{event}",
                    old_status="active",
                    new_status="paused",
                    meta={"email": email, "source": "ses_webhook"},
                ))
                logger.info(
                    "ses_webhook: paused enrollment %s for lead %s (%s)",
                    enrollment.id, lead_id, event,
                )

        # Mark email log rows as bounced/complained
        for row in log_rows:
            row.status = event  # "bounce" or "complaint"
