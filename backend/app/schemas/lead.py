"""
Pydantic schemas for lead submission and response.

LeadCreateRequest  — validated payload from the frontend form.
LeadResponse       — acknowledgement returned to the frontend after submission.

Field names match the frontend form exactly so form_data can be stored as-is.
"""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, model_validator


class LeadCreateRequest(BaseModel):
    """
    All fields map 1-to-1 with the frontend form.
    The full validated dict is stored in Lead.form_data (JSONB).
    Lead.email_address is extracted separately for indexed querying.
    """

    # ── Required ──────────────────────────────────────────────────────────────
    full_name: str
    email: EmailStr
    phone_country_code: str                              # required for dedup + CRM
    phone_number: str                                    # required for dedup + CRM
    preferred_contact_method: Literal["WhatsApp", "Email", "Call"]
    destination: str
    travel_month: str
    travel_year: str
    adults: str

    # ── Optional ──────────────────────────────────────────────────────────────
    # Frontend sends "" for unfilled selects; normalised to None below.
    children_under_12: Optional[str] = None
    adult_age_range: Optional[str] = None
    trip_duration_days: Optional[str] = None
    accommodation_preference: Optional[str] = None
    trip_motivation: Optional[str] = None
    budget_range: Optional[str] = None
    special_requests: Optional[str] = None
    utm_source:   Optional[str] = None
    utm_medium:   Optional[str] = None
    utm_campaign: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _empty_strings_to_none(cls, values: Any) -> Any:
        """Convert empty / whitespace-only strings to None before field validation."""
        if isinstance(values, dict):
            return {
                k: (None if isinstance(v, str) and v.strip() == "" else v)
                for k, v in values.items()
            }
        return values


class LeadResponse(BaseModel):
    """
    Lean 202 acknowledgement. The route handler sets `message` based on
    whether this is a new lead, an active duplicate, or a lapsed re-enquiry.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str       # Lead pipeline status: received / already_enrolled / already_submitted
    created_at: datetime
    message: str = "" # Human-readable result shown on the frontend success screen
