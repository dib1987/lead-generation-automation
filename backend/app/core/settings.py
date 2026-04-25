from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    environment: str = "development"
    secret_key: str = "change-me"

    # CORS — accepts a comma-separated string from env and splits it
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # Database
    database_url: str

    # Redis / Celery
    redis_url: str = "redis://redis:6379/0"

    # AWS SES
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    ses_verified_sender: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # HubSpot
    hubspot_api_key: str = ""


settings = Settings()
