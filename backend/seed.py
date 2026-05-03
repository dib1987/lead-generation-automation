"""
Seed script — populates the tenants table from config/tenants/*.json

Run inside the API container after migrations are applied:
  docker-compose exec api python seed.py

Idempotent: skips slugs that already exist.
"""
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Resolve paths relative to this file (works both locally and in-container)
_BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(_BASE))

from app.core.settings import settings
from app.db.base import Base  # noqa: F401 — registers all models with metadata
from app.models.tenant import Tenant


def _sync_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def main() -> None:
    engine = create_engine(_sync_url(), pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    tenant_configs = sorted((_BASE / "app" / "config" / "tenants").glob("*.json"))
    if not tenant_configs:
        print("No tenant JSON files found in app/config/tenants/")
        return

    with Session() as session:
        for config_path in tenant_configs:
            config = json.loads(config_path.read_text())
            slug = config["slug"]
            name = config["name"]

            existing = session.execute(
                select(Tenant).where(Tenant.slug == slug)
            ).scalars().first()

            if existing:
                print(f"  SKIP  {slug!r} — already exists (id={existing.id})")
                continue

            tenant = Tenant(slug=slug, name=name, config=config, is_active=True)
            session.add(tenant)
            session.flush()
            print(f"  INSERT {slug!r} — id={tenant.id}")

        session.commit()
        print("\nDone.")


if __name__ == "__main__":
    main()
