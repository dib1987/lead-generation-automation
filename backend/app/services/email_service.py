import uuid
import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.email_log import EmailLog

logger = logging.getLogger(__name__)

_HTML_ENVELOPE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Georgia,serif;font-size:16px;line-height:1.7;color:#2c2c2c;max-width:600px;margin:0 auto;padding:32px 24px;">
{body}
</body>
</html>"""


def send_email(
    session: Session,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    to_address: str,
    subject: str,
    html_body: str,
    tenant_config: dict,
    step_number: int = 0,
    campaign_enrollment_id: uuid.UUID | None = None,
) -> str:
    """
    Send an HTML email via AWS SES. Write EmailLog row regardless of outcome.
    Returns the SES MessageId on success.
    Raises on failure so the caller (Celery task) can handle retry logic.

    SYNC function — safe to call from Celery tasks.
    """
    from_name = tenant_config.get("ses", {}).get("from_name", tenant_config.get("name", ""))
    reply_to = tenant_config.get("ses", {}).get("reply_to", "")
    sender = f"{from_name} <{settings.ses_verified_sender}>"

    full_html = _HTML_ENVELOPE.format(body=html_body)
    body_preview = html_body[:500] if html_body else None

    ses = boto3.client(
        "ses",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )

    ses_message_id = None
    status = "sent"

    try:
        send_args = {
            "Source": sender,
            "Destination": {"ToAddresses": [to_address]},
            "Message": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": full_html, "Charset": "UTF-8"}},
            },
        }
        if reply_to:
            send_args["ReplyToAddresses"] = [reply_to]

        response = ses.send_email(**send_args)
        ses_message_id = response["MessageId"]
        logger.info("SES email sent: to=%s step=%d message_id=%s", to_address, step_number, ses_message_id)

    except (BotoCoreError, ClientError) as exc:
        status = "failed"
        logger.error("SES send failed: to=%s step=%d error=%s", to_address, step_number, exc)
        _write_email_log(
            session, tenant_id, lead_id, campaign_enrollment_id,
            step_number, to_address, subject, body_preview, None, status,
        )
        raise

    _write_email_log(
        session, tenant_id, lead_id, campaign_enrollment_id,
        step_number, to_address, subject, body_preview, ses_message_id, status,
    )
    return ses_message_id


def _write_email_log(
    session: Session,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    campaign_enrollment_id: uuid.UUID | None,
    step_number: int,
    to_address: str,
    subject: str,
    body_preview: str | None,
    ses_message_id: str | None,
    status: str,
) -> None:
    log = EmailLog(
        tenant_id=tenant_id,
        lead_id=lead_id,
        campaign_enrollment_id=campaign_enrollment_id,
        step_number=step_number,
        to_address=to_address,
        subject=subject,
        body_preview=body_preview,
        ses_message_id=ses_message_id,
        status=status,
    )
    session.add(log)
