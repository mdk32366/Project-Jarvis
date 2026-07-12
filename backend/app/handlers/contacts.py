"""Contacts — so JARVIS stops asking for the same email address every call.

The gap this closes: she emails the owner a transcript after every single call,
and still asked "what email should I send it to?" — because the transcript is
addressed by `settings.owner_email_resolved` deep in the job queue, which the
model never sees. The address existed; it just wasn't reachable as knowledge.

Two fixes, both here:

  * `whoami` — the owner's own details (email, home airport, frequent-flier
    numbers, etc). These live in config, not the DB: they're identity, they
    rarely change, and they should be set once and forgotten.
  * `lookup_contact` / `save_contact` — other people. These DO live in the DB,
    because the list grows and JARVIS should be able to add to it herself:
    "Nick's email is nictipoff@gmail.com" -> saved -> never asked again.

`save_contact` is NOT gated. Writing a name and address to a private table is
trivially reversible and asking for confirmation every time would be tiresome.
Contrast `send_email`, which is irreversible and speaks as the user.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import settings
from app.handlers.base import Context, Registry
from app.models import Contact

log = logging.getLogger(__name__)


def _whoami(args: dict, ctx: Context) -> str:
    """The owner's own details. Stop asking them for their own email address."""
    bits: list[str] = []
    if settings.owner_name:
        bits.append(f"Name: {settings.owner_name}")
    if settings.owner_email_resolved:
        bits.append(f"Email: {settings.owner_email_resolved}")
    if settings.owner_phone:
        bits.append(f"Phone: {settings.owner_phone}")
    if settings.owner_home_airport:
        bits.append(f"Home airport: {settings.owner_home_airport}")
    if settings.owner_home_address:
        bits.append(f"Home address: {settings.owner_home_address}")
    if settings.owner_work_address:
        bits.append(f"Work address: {settings.owner_work_address}")
    if settings.owner_frequent_flyer:
        bits.append(f"Frequent flyer: {settings.owner_frequent_flyer}")
    if settings.owner_vehicle:
        bits.append(f"Vehicle: {settings.owner_vehicle}")
    if settings.owner_boat:
        bits.append(f"Boat: {settings.owner_boat}")
    if settings.owner_places:
        bits.append(f"Named places: {settings.owner_places}")
    if settings.owner_notes:
        bits.append(f"Notes: {settings.owner_notes}")
    if settings.calendar_timezone:
        bits.append(f"Timezone: {settings.calendar_timezone}")

    if not bits:
        return ("Nothing on file about the owner. Set OWNER_NAME, OWNER_EMAIL, "
                "OWNER_HOME_AIRPORT etc. in the environment.")
    return "The owner's details:\n" + "\n".join(bits)


def _lookup_contact(args: dict, ctx: Context) -> str:
    q = (args.get("name") or "").strip().lower()
    if not q:
        return "Who are you looking for?"

    rows = ctx.db.execute(select(Contact)).scalars().all()

    # Exact-ish first, then substring. Never guess between two people — ask.
    exact = [c for c in rows if c.name.lower() == q]
    partial = [c for c in rows if q in c.name.lower() or c.name.lower() in q]
    hits = exact or partial

    if not hits:
        if not rows:
            from app.google_oauth import is_configured

            if is_configured():
                return ("The address book is empty. Offer to sync their Google contacts "
                        "(sync_google_contacts).")
            return ("The address book is empty and Google isn't connected. Ask the user "
                    "for the address, then save_contact it so you never ask twice.")
        known = ", ".join(c.name for c in rows[:20])
        return (f"No contact called '{args.get('name')}'. Known: {known}. "
                f"Ask the user for the address — don't guess it — then save_contact it.")
    if len(hits) > 1:
        names = ", ".join(f"{c.name} ({c.email})" for c in hits)
        return f"Several matches — ask which one: {names}"

    c = hits[0]
    out = f"{c.name}: {c.email}"
    if c.phone:
        out += f", {c.phone}"
    if c.notes:
        out += f" ({c.notes})"
    return out


def _save_contact(args: dict, ctx: Context) -> str:
    name = (args.get("name") or "").strip()
    email = (args.get("email") or "").strip()
    if not name:
        return "Need a name."

    existing = (
        ctx.db.execute(select(Contact).where(Contact.name.ilike(name)))
        .scalars()
        .first()
    )
    if existing:
        if email:
            existing.email = email
        if args.get("phone"):
            existing.phone = args["phone"]
        if args.get("notes"):
            existing.notes = args["notes"]
        ctx.db.commit()
        return f"Updated {existing.name}: {existing.email}"

    c = Contact(
        name=name[:120],
        email=email[:255],
        phone=(args.get("phone") or "")[:40],
        notes=(args.get("notes") or "")[:500],
    )
    ctx.db.add(c)
    ctx.db.commit()
    return f"Saved {c.name}: {c.email}"


def _list_contacts(args: dict, ctx: Context) -> str:
    rows = ctx.db.execute(select(Contact).order_by(Contact.name)).scalars().all()
    if not rows:
        return "No contacts saved yet."
    return f"{len(rows)} contacts:\n" + "\n".join(f"- {c.name}: {c.email}" for c in rows)


def register(reg: Registry) -> None:
    reg.register(
        {
            "name": "whoami",
            "description": (
                "The owner's own hard facts: email, phone, home and work addresses, the CITY "
                "THEY LIVE IN, home airport, frequent-flyer numbers, vehicle and plate, boat "
                "and hull number, named places, timezone.\n"
                "Call this to ANSWER a question about them ('what city do I live in?', "
                "'what's my hull number?') AND before asking them for a detail you could just "
                "look up. This is CONFIGURED ground truth \u2014 it beats anything you think you "
                "remember."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _whoami,
    )
    reg.register(
        {
            "name": "lookup_contact",
            "description": "Look up a person's email or phone by name. Use before asking the "
                           "user for an address they may have already given you.",
            "input_schema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
        _lookup_contact,
    )
    reg.register(
        {
            "name": "save_contact",
            "description": "Save or update a person's contact details. Use whenever the user "
                           "tells you someone's email address, so you never have to ask twice.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "notes": {"type": "string", "description": "e.g. 'brother', 'work'"},
                },
                "required": ["name"],
            },
        },
        _save_contact,
    )
    reg.register(
        {
            "name": "google_status",
            "description": "Check whether JARVIS is connected to the user's Google account "
                           "(contacts, tasks). Use if a Google-backed action seems unavailable.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _google_status,
    )
    reg.register(
        {
            "name": "sync_google_contacts",
            "description": (
                "Import the user's Google Contacts into JARVIS's address book. Use when "
                "they ask you to sync/import contacts, or when you can't find someone you "
                "would expect to know. Runs in the background."
            ),
            "input_schema": {"type": "object", "properties": {}},
        },
        _sync_contacts,
    )
    reg.register(
        {
            "name": "list_contacts",
            "description": "List all saved contacts.",
            "input_schema": {"type": "object", "properties": {}},
        },
        _list_contacts,
    )


# ── Google Contacts sync (needs OAuth — a service account CANNOT do this) ────
def sync_google_contacts(db, limit: int = 2000) -> str:
    """Pull Google Contacts into the local table.

    Upsert by name: a contact already known locally is updated, not duplicated.
    Local edits to a name JARVIS learned conversationally therefore survive a
    sync, which is what you want — she may know "Nick" from a phone call before
    Google ever does.

    Runs as a JOB (see app/jobs.py), not inline: a full address book is a few
    hundred rows and several paginated API calls, which is far too slow to sit
    inside a phone call.
    """
    from app.google_oauth import NOT_CONNECTED, people_service

    svc = people_service()
    if svc is None:
        return NOT_CONNECTED

    added = updated = skipped = 0
    page = None
    fetched = 0

    try:
        return _do_sync(db, svc, limit)
    except Exception as e:  # noqa: BLE001
        from app.google_oauth import explain

        hint = explain(e)
        if hint:
            log.error("contact sync blocked: %s", hint)
            raise RuntimeError(hint) from e
        raise


def _do_sync(db, svc, limit: int) -> str:
    added = updated = skipped = 0
    page = None
    fetched = 0

    while fetched < limit:
        resp = (
            svc.people()
            .connections()
            .list(
                resourceName="people/me",
                pageSize=min(1000, limit - fetched),
                personFields="names,emailAddresses,phoneNumbers",
                pageToken=page,
            )
            .execute()
        )
        people = resp.get("connections", []) or []
        fetched += len(people)

        for person in people:
            names = person.get("names") or []
            name = names[0].get("displayName", "").strip() if names else ""
            if not name:
                skipped += 1          # a contact with no name is unusable to us
                continue

            emails = person.get("emailAddresses") or []
            phones = person.get("phoneNumbers") or []
            email = emails[0].get("value", "").strip() if emails else ""
            phone = phones[0].get("value", "").strip() if phones else ""

            if not email and not phone:
                skipped += 1          # nothing to reach them by
                continue

            existing = (
                db.execute(select(Contact).where(Contact.name.ilike(name)))
                .scalars()
                .first()
            )
            if existing:
                # Don't clobber something she learned conversationally with a blank.
                if email and not existing.email:
                    existing.email = email[:255]
                if phone and not existing.phone:
                    existing.phone = phone[:40]
                updated += 1
            else:
                db.add(Contact(name=name[:120], email=email[:255], phone=phone[:40],
                               notes="google"))
                added += 1

        db.commit()
        page = resp.get("nextPageToken")
        if not page:
            break

    log.info("google contacts sync: +%d ~%d skip %d", added, updated, skipped)
    return f"Synced Google contacts: {added} new, {updated} updated, {skipped} skipped."


def _sync_contacts(args: dict, ctx: Context) -> str:
    """Tool: kick off the sync out-of-band."""
    from app.google_oauth import NOT_CONNECTED, is_configured

    if not is_configured():
        return NOT_CONNECTED

    from app.jobs import enqueue

    enqueue(ctx.db, "sync_contacts", {}, channel=ctx.channel,
            thread_key=ctx.thread_key, actor=ctx.actor)
    return ("Syncing your Google contacts now — it runs in the background and takes "
            "a few seconds. Ask me again shortly and they'll be there.")


def _google_status(args: dict, ctx: Context) -> str:
    """Is Google connected? Answer plainly rather than looking mysteriously broken."""
    from app.google_oauth import is_configured

    n = ctx.db.execute(select(Contact)).scalars().all()
    if not is_configured():
        return ("Google is NOT connected (no OAuth token), so I can't reach your Google "
                "contacts or push tasks to your phone. Calendar still works — it uses a "
                f"service account. Local address book: {len(n)} contacts.")
    return (f"Google is connected. Local address book: {len(n)} contacts. "
            f"Tasks I create are pushed to Google Tasks.")
