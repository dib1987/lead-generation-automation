from celery import Celery
from app.core.settings import settings

celery_app = Celery(
    "leadgen",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Beat schedule — tasks registered here in Phase 1B
    beat_schedule={},
)
