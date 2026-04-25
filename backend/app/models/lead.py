import uuid
from datetime import datetime
from sqlalchemy import String, ForeignKey, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    form_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="received", index=True
    )
    email_address: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    crm_contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crm_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )
