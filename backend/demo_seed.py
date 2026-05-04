"""
Demo seed — pre-populates DB with realistic-looking leads for client demos.

Run after seed.py (which creates the tenant rows):
  docker-compose exec api python demo_seed.py

Idempotent: skips any lead whose email address already exists for the tenant.
To wipe and re-seed: docker-compose exec api python demo_seed.py --reset
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, delete
from sqlalchemy.orm import sessionmaker

_BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(_BASE))

from app.core.settings import settings
from app.db.base import Base  # noqa: F401 — registers all models
from app.models.audit_log import AuditLog
from app.models.campaign import Campaign
from app.models.campaign_enrollment import CampaignEnrollment
from app.models.email_log import EmailLog
from app.models.lead import Lead
from app.models.tenant import Tenant


def _sync_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


_NOW = datetime.now(timezone.utc)
_DELAY_DAYS = [0, 2, 5, 8, 11, 14]

_DEMO_EMAILS = [
    "sarah.mitchell@outlook.com",
    "carlos.mendoza@gmail.com",
    "emma.vandenberg@yahoo.com",
    "james.okafor@gmail.com",
    "annika.j@hotmail.com",
    "michael.chen@techventures.com",
    "leila.hassan@gmail.com",
    "priya.sharma@gmail.com",
    "tom.blackwell@gmail.com",
    "david.harrington@live.com",
    "robert.king@enterprise.org",
    "fiona.obrien@gmail.com",
]


def _ago(days: float) -> datetime:
    return _NOW - timedelta(days=days)


def _fake_ses_id() -> str:
    return f"0102{uuid.uuid4().hex[:18]}@us-east-1.amazonses.com"


DEMO_LEADS = [
    # ── Completed (all 6 emails sent) ──────────────────────────────────────────
    {
        "full_name": "Sarah Mitchell",
        "email": "sarah.mitchell@outlook.com",
        "destination": "Botswana",
        "travel_month": "September",
        "travel_year": "2026",
        "trip_motivation": "Honeymoon — private safari and sundowners in the Okavango",
        "budget_range": "$5,000–$8,000 per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Luxury private camps",
        "phone_country_code": "+44", "phone_number": "07700900123",
        "preferred_contact_method": "Email",
        "special_requests": "Private dining setup for at least one evening.",
        "trip_duration_days": "12",
        "score": 85, "enrolled_days_ago": 20,
        "steps_sent": 6, "enrollment_status": "completed",
    },
    {
        "full_name": "Carlos Mendoza",
        "email": "carlos.mendoza@gmail.com",
        "destination": "South Africa",
        "travel_month": "July",
        "travel_year": "2026",
        "trip_motivation": "Wildlife photography — Big Five with a professional guide",
        "budget_range": "$3,000–$5,000 per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Safari tented camps",
        "phone_country_code": "+1", "phone_number": "3055550182",
        "preferred_contact_method": "Email",
        "special_requests": "Reliable power outlets for camera equipment.",
        "trip_duration_days": "10",
        "score": 78, "enrolled_days_ago": 18,
        "steps_sent": 6, "enrollment_status": "completed",
    },
    {
        "full_name": "Emma van der Berg",
        "email": "emma.vandenberg@yahoo.com",
        "destination": "Tanzania",
        "travel_month": "October",
        "travel_year": "2026",
        "trip_motivation": "First Africa trip — Great Migration and Zanzibar extension",
        "budget_range": "$5,000–$8,000 per person",
        "adults": "2", "children_under_12": "1",
        "accommodation_preference": "Luxury tented camps",
        "phone_country_code": "+31", "phone_number": "0612345678",
        "preferred_contact_method": "Email",
        "special_requests": "Child-friendly options — daughter is 8 years old.",
        "trip_duration_days": "14",
        "score": 72, "enrolled_days_ago": 17,
        "steps_sent": 6, "enrollment_status": "completed",
    },

    # ── Replied (lead responded — sequence stopped automatically) ─────────────
    {
        "full_name": "James Okafor",
        "email": "james.okafor@gmail.com",
        "destination": "Kenya",
        "travel_month": "August",
        "travel_year": "2026",
        "trip_motivation": "Family adventure — Masai Mara migration and children's first safari",
        "budget_range": "$3,000–$5,000 per person",
        "adults": "2", "children_under_12": "2",
        "accommodation_preference": "Family-friendly lodges",
        "phone_country_code": "+234", "phone_number": "08012345678",
        "preferred_contact_method": "Email",
        "special_requests": "Kids are 6 and 9 — guide who is great with children.",
        "trip_duration_days": "10",
        "score": 88, "enrolled_days_ago": 12,
        "steps_sent": 3, "enrollment_status": "replied",
        "replied_at_days_ago": 3,
    },
    {
        "full_name": "Annika Johansson",
        "email": "annika.j@hotmail.com",
        "destination": "Mozambique",
        "travel_month": "December",
        "travel_year": "2026",
        "trip_motivation": "Beach and bush — Bazaruto Archipelago then a short safari inland",
        "budget_range": "$8,000+ per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Boutique beachfront lodges",
        "phone_country_code": "+46", "phone_number": "0701234567",
        "preferred_contact_method": "Email",
        "special_requests": "Both certified divers — can we include a dive itinerary?",
        "trip_duration_days": "14",
        "score": 82, "enrolled_days_ago": 10,
        "steps_sent": 2, "enrollment_status": "replied",
        "replied_at_days_ago": 7,
    },

    # ── Active (sequence in progress) ─────────────────────────────────────────
    {
        "full_name": "Michael Chen",
        "email": "michael.chen@techventures.com",
        "destination": "Rwanda",
        "travel_month": "November",
        "travel_year": "2026",
        "trip_motivation": "Gorilla trekking — bucket-list trip with my partner",
        "budget_range": "$8,000+ per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Luxury eco-lodges",
        "phone_country_code": "+65", "phone_number": "91234567",
        "preferred_contact_method": "Email",
        "special_requests": "Combine with chimpanzee tracking in Nyungwe if possible.",
        "trip_duration_days": "8",
        "score": 91, "enrolled_days_ago": 8,
        "steps_sent": 4, "enrollment_status": "active",
    },
    {
        "full_name": "Leila Hassan",
        "email": "leila.hassan@gmail.com",
        "destination": "Ethiopia",
        "travel_month": "September",
        "travel_year": "2026",
        "trip_motivation": "Cultural heritage — Lalibela, Danakil Depression, and Omo Valley",
        "budget_range": "$3,000–$5,000 per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Boutique heritage guesthouses",
        "phone_country_code": "+251", "phone_number": "0911234567",
        "preferred_contact_method": "Email",
        "special_requests": "Keen to meet local communities and artisans where possible.",
        "trip_duration_days": "12",
        "score": 68, "enrolled_days_ago": 5,
        "steps_sent": 3, "enrollment_status": "active",
    },
    {
        "full_name": "Priya Sharma",
        "email": "priya.sharma@gmail.com",
        "destination": "Zambia",
        "travel_month": "August",
        "travel_year": "2026",
        "trip_motivation": "Victoria Falls and a Kafue National Park safari with family",
        "budget_range": "$5,000–$8,000 per person",
        "adults": "4", "children_under_12": "1",
        "accommodation_preference": "Luxury lodges with family suites",
        "phone_country_code": "+91", "phone_number": "9876543210",
        "preferred_contact_method": "Email",
        "special_requests": "Mother-in-law has limited mobility — level access preferred.",
        "trip_duration_days": "10",
        "score": 75, "enrolled_days_ago": 2,
        "steps_sent": 2, "enrollment_status": "active",
    },
    {
        "full_name": "Tom Blackwell",
        "email": "tom.blackwell@gmail.com",
        "destination": "Uganda",
        "travel_month": "October",
        "travel_year": "2026",
        "trip_motivation": "Gorilla and chimpanzee trekking — I am a primatologist",
        "budget_range": "$8,000+ per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Eco-lodges and forest camps",
        "phone_country_code": "+61", "phone_number": "412345678",
        "preferred_contact_method": "Email",
        "special_requests": "Extended permits — hoping for 2+ gorilla tracking sessions.",
        "trip_duration_days": "9",
        "score": 80, "enrolled_days_ago": 1,
        "steps_sent": 1, "enrollment_status": "active",
    },

    # ── Paused (bounce or complaint) ──────────────────────────────────────────
    {
        "full_name": "David Harrington",
        "email": "david.harrington@live.com",
        "destination": "Zimbabwe",
        "travel_month": "September",
        "travel_year": "2026",
        "trip_motivation": "Victoria Falls and Hwange elephant herds",
        "budget_range": "$1,500–$3,000 per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Mid-range lodges",
        "phone_country_code": "+44", "phone_number": "07911123456",
        "preferred_contact_method": "Email",
        "special_requests": "",
        "trip_duration_days": "7",
        "score": 45, "enrolled_days_ago": 7,
        "steps_sent": 1, "enrollment_status": "paused",
        "pause_event": "bounce",
    },
    {
        "full_name": "Robert King",
        "email": "robert.king@enterprise.org",
        "destination": "Botswana",
        "travel_month": "July",
        "travel_year": "2026",
        "trip_motivation": "Luxury private safari — Okavango Delta and Chobe River",
        "budget_range": "$8,000+ per person",
        "adults": "2", "children_under_12": "0",
        "accommodation_preference": "Private game reserves",
        "phone_country_code": "+1", "phone_number": "6175550199",
        "preferred_contact_method": "Email",
        "special_requests": "",
        "trip_duration_days": "10",
        "score": 77, "enrolled_days_ago": 9,
        "steps_sent": 2, "enrollment_status": "paused",
        "pause_event": "complaint",
    },

    # ── Just submitted (today) ─────────────────────────────────────────────────
    {
        "full_name": "Fiona O'Brien",
        "email": "fiona.obrien@gmail.com",
        "destination": "Namibia",
        "travel_month": "August",
        "travel_year": "2026",
        "trip_motivation": "Photography expedition — Sossusvlei dunes and desert wildlife",
        "budget_range": "$3,000–$5,000 per person",
        "adults": "1", "children_under_12": "0",
        "accommodation_preference": "Desert lodges and glamping",
        "phone_country_code": "+353", "phone_number": "0851234567",
        "preferred_contact_method": "Email",
        "special_requests": "Solo traveller — guides with photography expertise preferred.",
        "trip_duration_days": "10",
        "score": 71, "enrolled_days_ago": 0.2,
        "steps_sent": 1, "enrollment_status": "active",
    },
]


def _subjects(d: dict) -> list[str]:
    fn = d["full_name"].split()[0]
    dest = d["destination"]
    month = d["travel_month"]
    return [
        f"Your {dest} journey — a first look, {fn}",
        f"Why {dest} in {month} is exceptional, {fn}",
        f"What our travellers say about {dest}",
        f"Preparing for {dest} — what to know before you go",
        f"{month} availability at top {dest} properties is limited",
        f"Still dreaming of Africa, {fn}?",
    ]


def _previews(d: dict) -> list[str]:
    fn = d["full_name"].split()[0]
    dest = d["destination"]
    month = d["travel_month"]
    year = d["travel_year"]
    return [
        f"<p>Hello, {fn},</p><p>We are The Africa Horizons Travel Design Team — your dedicated journey creators at www.africahorizonstravel.com. Your interest in {dest} for {month} {year} caught our attention, and we wanted to reach out personally to begin crafting your experience...</p>",
        f"<p>Dear {fn},</p><p>Two days ago you reached out about {dest}, and we've been reflecting on your trip. {month} is one of the most rewarding times to visit — let us share exactly why this timing could make your journey truly exceptional...</p>",
        f"<p>Dear {fn},</p><p>Choosing the right travel partner for a trip to {dest} is a significant decision. Here is what a few of our recent travellers had to say about their experience — in their own words...</p>",
        f"<p>Dear {fn},</p><p>We want to help you start thinking practically about your journey to {dest}. A few things worth knowing before you go — covering visas, health considerations, and one insider tip most visitors miss...</p>",
        f"<p>Dear {fn},</p><p>{month} is one of the most popular times to visit {dest}, and availability at the finest properties is beginning to fill. We'd love to place a provisional hold while you make your decision — no commitment required...</p>",
        f"<p>Dear {fn},</p><p>We know life gets busy. This is the last note in our sequence. Your {dest} dream will still be here whenever the time is right — and so will we...</p>",
    ]


def _seed_lead(session, tenant_id: uuid.UUID, campaign: Campaign, d: dict) -> None:
    email = d["email"]
    steps_sent = d["steps_sent"]
    enrollment_status = d["enrollment_status"]
    enrolled_at = _ago(d["enrolled_days_ago"])

    existing = session.execute(
        select(Lead).where(Lead.email_address == email, Lead.tenant_id == tenant_id)
    ).scalars().first()
    if existing:
        print(f"  SKIP  {email}")
        return

    lead = Lead(
        tenant_id=tenant_id,
        form_data={
            "full_name": d["full_name"],
            "email": email,
            "destination": d["destination"],
            "travel_month": d["travel_month"],
            "travel_year": d["travel_year"],
            "trip_motivation": d["trip_motivation"],
            "budget_range": d["budget_range"],
            "adults": d["adults"],
            "children_under_12": d["children_under_12"],
            "accommodation_preference": d["accommodation_preference"],
            "phone_country_code": d["phone_country_code"],
            "phone_number": d["phone_number"],
            "preferred_contact_method": d["preferred_contact_method"],
            "special_requests": d["special_requests"],
            "trip_duration_days": d["trip_duration_days"],
        },
        status="email_sent",
        email_address=email,
        lead_score=d["score"],
        created_at=enrolled_at,
        updated_at=enrolled_at,
    )
    session.add(lead)
    session.flush()

    # Audit trail: received → processing → email_sent
    for event, old, new, delta, meta in [
        ("lead_received",  None,         "received",   timedelta(0),        {"source": "web_form"}),
        ("lead_scored",    "received",   "processing", timedelta(seconds=4), {"score": d["score"], "source": "process_lead"}),
        ("email_sent",     "processing", "email_sent", timedelta(seconds=12), {"step": 0, "to": email, "source": "process_lead"}),
    ]:
        session.add(AuditLog(
            tenant_id=tenant_id, lead_id=lead.id,
            event=event, old_status=old, new_status=new,
            meta=meta, created_at=enrolled_at + delta,
        ))

    # Enrollment
    current_step = steps_sent - 1
    completed_at = replied_at = next_send_at = None

    if enrollment_status == "completed":
        completed_at = enrolled_at + timedelta(days=_DELAY_DAYS[-1], hours=2)
    elif enrollment_status == "replied":
        replied_at = _ago(d.get("replied_at_days_ago", 1))
    elif enrollment_status == "active":
        next_idx = steps_sent  # index of the next step to fire
        if next_idx < len(_DELAY_DAYS):
            next_send_at = enrolled_at + timedelta(days=_DELAY_DAYS[next_idx])

    enrollment = CampaignEnrollment(
        tenant_id=tenant_id, lead_id=lead.id, campaign_id=campaign.id,
        current_step=current_step, status=enrollment_status,
        next_send_at=next_send_at, enrolled_at=enrolled_at,
        completed_at=completed_at, replied_at=replied_at,
    )
    session.add(enrollment)
    session.flush()

    # Email logs
    subj = _subjects(d)
    prev = _previews(d)
    email_status = d["pause_event"] if enrollment_status == "paused" else "sent"

    for step in range(steps_sent):
        sent_at = (
            enrolled_at + timedelta(seconds=12) if step == 0
            else enrolled_at + timedelta(days=_DELAY_DAYS[step])
        )
        session.add(EmailLog(
            tenant_id=tenant_id, lead_id=lead.id,
            campaign_enrollment_id=None if step == 0 else enrollment.id,
            step_number=step,
            to_address=email,
            subject=subj[step],
            body_preview=prev[step],
            ses_message_id=_fake_ses_id(),
            status=email_status,
            sent_at=sent_at,
        ))
        if step > 0:
            session.add(AuditLog(
                tenant_id=tenant_id, lead_id=lead.id,
                event="followup_email_sent",
                old_status="email_sent", new_status="email_sent",
                meta={"step": step, "to": email, "source": "run_followup"},
                created_at=sent_at,
            ))

    # Lifecycle audit for terminal / paused states
    if enrollment_status == "paused":
        pause_event = d.get("pause_event", "bounce")
        # pause fires after the last email was delivered (+ half a day)
        pause_at = enrolled_at + timedelta(days=_DELAY_DAYS[steps_sent - 1] + 0.5)
        session.add(AuditLog(
            tenant_id=tenant_id, lead_id=lead.id,
            event=f"enrollment_{pause_event}",
            old_status="active", new_status="paused",
            meta={"email": email, "source": "ses_webhook"},
            created_at=pause_at,
        ))
    elif enrollment_status == "replied":
        session.add(AuditLog(
            tenant_id=tenant_id, lead_id=lead.id,
            event="enrollment_replied",
            old_status="active", new_status="replied",
            meta={"sender": email, "subject": f"Re: {subj[steps_sent - 1]}", "source": "ses_inbound"},
            created_at=replied_at,
        ))
    elif enrollment_status == "completed":
        session.add(AuditLog(
            tenant_id=tenant_id, lead_id=lead.id,
            event="enrollment_completed",
            old_status="active", new_status="completed",
            meta={"total_steps": 6, "source": "run_followup"},
            created_at=completed_at,
        ))

    print(f"  INSERT {email:<40} status={enrollment_status:<10} score={d['score']}  emails={steps_sent}")


def _get_or_create_campaign(session, tenant_id: uuid.UUID) -> Campaign:
    config_path = _BASE / "app" / "config" / "campaigns" / "africa_14day.json"
    config = json.loads(config_path.read_text())
    slug = config["slug"]

    existing = session.execute(
        select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.slug == slug)
    ).scalars().first()
    if existing:
        print(f"  Campaign {slug!r} already exists (id={existing.id})")
        return existing

    campaign = Campaign(
        tenant_id=tenant_id, slug=slug,
        name=config["name"], steps=config["steps"], is_active=True,
    )
    session.add(campaign)
    session.flush()
    print(f"  Campaign {slug!r} created (id={campaign.id})")
    return campaign


def _reset(session, tenant_id: uuid.UUID) -> None:
    rows = session.execute(
        select(Lead).where(
            Lead.tenant_id == tenant_id,
            Lead.email_address.in_(_DEMO_EMAILS),
        )
    ).scalars().all()
    for lead in rows:
        session.delete(lead)
    session.flush()
    print(f"  Deleted {len(rows)} demo lead(s) (cascade removes enrollments, emails, audit logs)")


def main() -> None:
    reset_mode = "--reset" in sys.argv

    engine = create_engine(_sync_url(), pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        tenant = session.execute(
            select(Tenant).where(Tenant.slug == "africa_travel")
        ).scalars().first()

        if not tenant:
            print("ERROR: tenant 'africa_travel' not found — run seed.py first.")
            sys.exit(1)

        print(f"Tenant: {tenant.name} (id={tenant.id})\n")

        if reset_mode:
            print("Resetting demo data...")
            _reset(session, tenant.id)
            session.commit()
            print("Reset complete. Re-run without --reset to seed.\n")
            return

        campaign = _get_or_create_campaign(session, tenant.id)

        print(f"\nSeeding {len(DEMO_LEADS)} demo leads...\n")
        for d in DEMO_LEADS:
            _seed_lead(session, tenant.id, campaign, d)

        session.commit()
        print(f"\nDone. Admin dashboard is ready for demo.")


if __name__ == "__main__":
    main()
