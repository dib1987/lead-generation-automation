import logging
from datetime import datetime, timezone

import httpx

from app.core.settings import settings

logger = logging.getLogger(__name__)

_HUBSPOT_UPSERT_URL = "https://api.hubapi.com/crm/v3/objects/contacts/upsert"


def upsert_contact(form_data: dict, tenant_config: dict) -> str | None:
    """
    Upsert a HubSpot contact using email as the dedup key.
    Returns the HubSpot contact ID on success, None if skipped or failed.

    SYNC function — safe to call from Celery tasks.
    Raises on unexpected HTTP errors so the caller can log them.
    """
    if not settings.hubspot_api_key:
        logger.debug("HubSpot API key not configured — skipping CRM sync")
        return None

    phone = f"{form_data.get('phone_country_code', '')} {form_data.get('phone_number', '')}".strip()
    full_name = form_data.get("full_name", "")
    name_parts = full_name.split(None, 1)
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[1] if len(name_parts) > 1 else ""

    properties = {
        "email": form_data.get("email", ""),
        "firstname": first_name,
        "lastname": last_name,
        "phone": phone,
        "hs_lead_status": "NEW",
        "lifecyclestage": tenant_config.get("hubspot", {}).get("lifecycle_stage", "lead"),
        # Custom properties — store raw form data for reference in HubSpot
        "destination_interest": form_data.get("destination", ""),
        "travel_month": f"{form_data.get('travel_month', '')} {form_data.get('travel_year', '')}".strip(),
        "preferred_contact_method": form_data.get("preferred_contact_method", ""),
        "budget_range": form_data.get("budget_range", ""),
        "trip_motivation": form_data.get("trip_motivation", ""),
    }
    # Remove empty string values — HubSpot rejects them on some property types
    properties = {k: v for k, v in properties.items() if v}

    payload = {
        "properties": properties,
        "idProperty": "email",
    }

    headers = {
        "Authorization": f"Bearer {settings.hubspot_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=10.0) as client:
        response = client.post(_HUBSPOT_UPSERT_URL, json=payload, headers=headers)

    if response.status_code in (200, 201):
        contact_id = response.json().get("id")
        logger.info("HubSpot contact upserted: id=%s email=%s", contact_id, form_data.get("email"))
        return contact_id

    logger.error(
        "HubSpot upsert failed: status=%d body=%s",
        response.status_code,
        response.text[:500],
    )
    response.raise_for_status()
    return None
