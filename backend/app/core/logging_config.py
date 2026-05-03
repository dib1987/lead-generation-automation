"""
Structured JSON logging configuration.

In development (ENVIRONMENT != production), logs are plain text for readability.
In production, every log line is a JSON object — queryable in CloudWatch / Datadog.

Call configure_logging() once at application startup (main.py and celery_app.py).
"""
import json
import logging
import sys
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(environment: str = "development") -> None:
    """
    Set up root logger. Call once at startup.
    - development → human-readable text
    - production  → JSON per line
    """
    handler = logging.StreamHandler(sys.stdout)

    if environment == "production":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
        )

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Remove any handlers added before this call (e.g., by uvicorn)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
