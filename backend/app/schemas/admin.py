import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AuditEntry(BaseModel):
    event: str
    old_status: Optional[str]
    new_status: str
    meta: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailLogSummary(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    to_address: str
    subject: str
    step_number: int
    status: str
    sent_at: datetime
    body_preview: Optional[str]

    model_config = {"from_attributes": True}


class LeadSummary(BaseModel):
    id: uuid.UUID
    email_address: str
    status: str
    lead_score: Optional[int]
    created_at: datetime
    full_name: str
    destination: str

    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    items: list[LeadSummary]
    total: int
    page: int
    page_size: int


class LeadDetailResponse(BaseModel):
    id: uuid.UUID
    email_address: str
    status: str
    lead_score: Optional[int]
    crm_contact_id: Optional[str]
    form_data: dict
    created_at: datetime
    updated_at: datetime
    email_logs: list[EmailLogSummary]
    audit_trail: list[AuditEntry]

    model_config = {"from_attributes": True}


class EmailLogListResponse(BaseModel):
    items: list[EmailLogSummary]
    total: int
    page: int
    page_size: int


class DashboardResponse(BaseModel):
    total_leads: int
    leads_by_status: dict[str, int]
    emails_sent: int
    avg_lead_score: Optional[float]
