import uuid
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog


def write_audit_log(
    session: Session,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    event: str,
    old_status: str | None,
    new_status: str,
    meta: dict | None = None,
) -> None:
    log = AuditLog(
        tenant_id=tenant_id,
        lead_id=lead_id,
        event=event,
        old_status=old_status,
        new_status=new_status,
        meta=meta or {},
    )
    session.add(log)
    session.flush()
