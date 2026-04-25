from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models here so Alembic autogenerate can detect them
from app.models.tenant import Tenant  # noqa: F401, E402
from app.models.lead import Lead  # noqa: F401, E402
from app.models.audit_log import AuditLog  # noqa: F401, E402
from app.models.campaign import Campaign  # noqa: F401, E402
from app.models.campaign_enrollment import CampaignEnrollment  # noqa: F401, E402
from app.models.email_log import EmailLog  # noqa: F401, E402
from app.models.llm_cost_log import LLMCostLog  # noqa: F401, E402
