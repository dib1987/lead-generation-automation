from celery import Celery
from celery.schedules import crontab
from app.core.settings import settings
import app.db.base  # noqa: F401 — pre-loads all SQLAlchemy models before Celery imports task modules, breaking the circular import chain

celery_app = Celery(
    "leadgen",
    broker=settings.redis_url,
    backend=settings.redis_url,
    # Explicitly list task modules so Celery registers them on startup
    include=[
        "app.workers.tasks.process_lead",
        "app.workers.tasks.run_followup",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "run-followup-every-15-min": {
            "task": "workers.tasks.run_followup.run_followup",
            "schedule": crontab(minute="*/15"),
        },
    },
)
