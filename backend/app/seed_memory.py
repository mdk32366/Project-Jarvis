"""Hand-seed JARVIS's persona + standing preferences so it sounds like you from
day one. Edit the entries below, then run:  python -m app.seed_memory

Re-running is safe: persona facts are de-duplicated by content, preferences by key.
"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ContactWhitelist, PersonaProfile, Preference

# ── EDIT ME ──────────────────────────────────────────────────────────────────
# These are placeholders. Replace with real facts about you. The richer and more
# specific these are, the more JARVIS will think and write like you.

PERSONA = [
    ("identity", "Matt — builder of personal automation systems (JARVIS, FFIS, Sentinel)."),
    ("style", "Prefers concise, direct communication. Minimal preamble. No filler."),
    ("style", "Comfortable with technical depth; appreciates trade-offs stated plainly."),
    ("values", "Bias toward action and shipping; values guardrails on anything irreversible."),
    ("context", "Runs several self-hosted apps on Fly.io. Cares about uptime and spend."),
]

PREFERENCES = {
    "confirmation": "Always confirm financial or irreversible actions before executing.",
    "reply_length": "Keep email/SMS replies short and skimmable; lead with the answer.",
    "tone": "Professional but warm. No emoji unless I use them first.",
    "briefing_time": "Prefer a morning briefing around 6:00 AM local.",
}

# Email addresses allowed to command JARVIS (your real inbox, not the bot account).
WHITELIST = [
    # ("email", "mdk32366@gmail.com", "Matt (primary)"),
]
# ─────────────────────────────────────────────────────────────────────────────


def seed() -> None:
    db = SessionLocal()
    try:
        added = {"persona": 0, "preferences": 0, "whitelist": 0}

        for category, content in PERSONA:
            exists = (
                db.execute(select(PersonaProfile).where(PersonaProfile.content == content))
                .scalars()
                .first()
            )
            if not exists:
                db.add(PersonaProfile(category=category, content=content))
                added["persona"] += 1

        for key, value in PREFERENCES.items():
            pref = (
                db.execute(select(Preference).where(Preference.key == key))
                .scalars()
                .first()
            )
            if pref:
                pref.value = value
            else:
                db.add(Preference(key=key, value=value))
                added["preferences"] += 1

        for channel, identifier, label in WHITELIST:
            exists = (
                db.execute(
                    select(ContactWhitelist).where(ContactWhitelist.identifier == identifier)
                )
                .scalars()
                .first()
            )
            if not exists:
                db.add(ContactWhitelist(channel=channel, identifier=identifier, label=label))
                added["whitelist"] += 1

        db.commit()
        print(f"Seed complete: {added}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
