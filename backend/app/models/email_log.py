import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # null for Day 0 (initial) email
    campaign_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaign_enrollments.id", ondelete="SET NULL"),
        nullable=True,
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    ses_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # sent / failed / bounced
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="sent")
    sent_at: Mapped[datetime] = mapped_column(server_default=text("now()"), index=True)
