from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

class PersonaProfile(Base):
    __tablename__ = "persona_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Preference(Base):
    __tablename__ = "preferences"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(64), default="general")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), default="conversation")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    # Portable embedding storage: JSON array of floats. In production a parallel
    # pgvector table (see vectorstore.PgVectorStore) mirrors this for fast ANN;
    # this column keeps the app DB-portable (SQLite dev/tests) and is the source
    # of truth for the in-Python cosine fallback.
    embedding: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ContactWhitelist(Base):
    __tablename__ = "contacts_whitelist"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), default="email")
    identifier: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ActionAudit(Base):
    __tablename__ = "actions_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), default="")
    actor: Mapped[str] = mapped_column(String(255), default="")
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"
    id: Mapped[int] = mapped_column(primary_key=True)
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Groups the gated actions created in ONE request so "do this, that, and the
    # other" can be read back together and cleared with a single confirm
    # (TDD-multi-action-buffering). NULL for a standalone single action.
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Second factor (book_flight only; unused/NULL for every other gated tool).
    # See flight-booking TDD §2.3: after 'confirm' clears the readback, status
    # goes pending -> awaiting_code, and execution waits on a TOTP code instead
    # of running immediately. code_deadline is a hard 5-min TTL; code_attempts
    # caps at 3 and then CANCELS the row rather than allowing retries.
    code_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    code_attempts: Mapped[int] = mapped_column(Integer, default=0)

class Job(Base):
    """Durable background job. Survives restarts; claimed and run by the worker."""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")        # JSON args
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|error
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    channel: Mapped[str] = mapped_column(String(32), default="")
    thread_key: Mapped[str] = mapped_column(String(255), default="")
    actor: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AgentConfig(Base):
    """Data-driven specialist agent. Editable from the admin tab; read live by
    the orchestrator's delegate tool. Seeded from agents.DEFAULT_AGENTS."""
    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    tools: Mapped[str] = mapped_column(Text, default="[]")   # JSON array of tool names
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VoiceTurn(Base):
    """One spoken turn of a phone call (TDD 6.2).

    The orchestrator can exceed Twilio's ~15s webhook timeout, so /voice/gather
    returns TwiML immediately and orchestrates in a BackgroundTask; /voice/poll
    collects the result from here.

    A DB table rather than in-process state: today min_machines_running=1 so
    consecutive webhooks hit the same api machine, but that is a config value
    that will change for unrelated reasons, and a Fly restart mid-call has the
    same effect. In-memory state fails intermittently and presents as "voice
    randomly hangs up."
    """

    __tablename__ = "voice_turns"
    __table_args__ = (UniqueConstraint("call_sid", "turn", name="uq_voice_turn"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    call_sid: Mapped[str] = mapped_column(String(64), index=True)
    turn: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|error
    user_text: Mapped[str] = mapped_column(Text, default="")
    reply: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    # Set when the caller was handed off (held past the budget, or asked to be
    # called/emailed) while this turn was still running. run_turn emails the
    # finished answer on completion — so a slow research turn the caller stopped
    # waiting for still reaches them, rather than being silently orphaned.
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Task(Base):
    """A task JARVIS owns.

    Deliberately NOT Google Tasks: the calendar integration uses a service
    account, and service accounts cannot access a consumer Google account's task
    list (no domain-wide delegation for @gmail.com). Rather than force an OAuth
    refresh-token flow just for tasks, JARVIS keeps its own — surfaced in the
    dashboard and the morning briefing, which is where the rest of its state
    already lives. Google Tasks sync, if ever wanted, becomes an export, not the
    source of truth.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str] = mapped_column(Text, default="")
    # open | done | cancelled
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high
    source: Mapped[str] = mapped_column(String(32), default="")          # channel that created it
    # Google Tasks id, once pushed. Empty until the sync job lands (or forever,
    # if Google isn't connected — the local table is the source of truth).
    google_id: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Idea(Base):
    """A captured idea.

    Written to the DB immediately on capture (so a network failure can never eat
    the thought), then committed to a git repo out-of-band by the `commit_idea`
    job. `committed_sha` is NULL until that lands.
    """

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(300), default="")   # comma-separated
    source: Mapped[str] = mapped_column(String(32), default="")  # channel
    committed_sha: Mapped[str] = mapped_column(String(64), default="")
    commit_error: Mapped[str] = mapped_column(Text, default="")
    # Set when the idea is promoted into its own GitHub project repo — the repo
    # html_url. Non-empty means "already a project"; a second promotion is refused.
    promoted_url: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Trip(Base):
    """An itinerary parsed out of a confirmation email.

    No airline credentials, no scraping: the airline mails the confirmation to
    JARVIS's inbox, the email pipeline already reads that inbox, and the parser
    turns it into structure. JARVIS knows about the trip because the trip was
    mailed to it. That is the correct trust boundary, not a workaround.
    """

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier: Mapped[str] = mapped_column(String(64), default="")
    confirmation: Mapped[str] = mapped_column(String(32), default="", index=True)
    origin: Mapped[str] = mapped_column(String(8), default="")
    destination: Mapped[str] = mapped_column(String(8), default="")
    depart_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flight_no: Mapped[str] = mapped_column(String(16), default="")
    seat: Mapped[str] = mapped_column(String(16), default="")
    raw: Mapped[str] = mapped_column(Text, default="")           # source email, for re-parsing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FlightOffer(Base):
    """A Duffel offer JARVIS retrieved herself, via search_flights.

    THE LOAD-BEARING TABLE (flight-booking TDD §2.2a). book_flight accepts an
    offer_id ONLY if a row exists here. A flight described in free text, or
    'found' on a web page, has no row and is refused — the web-search surface
    is structurally disconnected from the spending surface, enforced in code,
    not by convention.

    Short-lived by design: Duffel offers themselves expire in ~30 minutes, so a
    stale row is harmless — Duffel will reject the offer_id anyway (fails
    closed) and book_flight surfaces that in English rather than a raw 422.
    """
    __tablename__ = "flight_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    offer_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    total_amount: Mapped[str] = mapped_column(String(32), default="")
    total_currency: Mapped[str] = mapped_column(String(8), default="")
    carrier: Mapped[str] = mapped_column(String(64), default="")
    route: Mapped[str] = mapped_column(String(32), default="")        # "SEA-SFO"
    depart_at: Mapped[str] = mapped_column(String(64), default="")    # ISO, as Duffel sent it
    summary: Mapped[str] = mapped_column(Text, default="")            # spoken-friendly line, for the readback
    raw: Mapped[str] = mapped_column(Text, default="")                # full offer JSON, needed to book
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Contact(Base):
    """People JARVIS knows. Distinct from ContactWhitelist, which is an AUTH
    boundary (who may command JARVIS). This is an address book (who JARVIS can
    look up), and being in it grants no permissions whatsoever."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    notes: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboundCall(Base):
    """A call JARVIS places TO the owner.

    This is the piece that turns her from an IVR into an assistant. Work that
    can't fit inside a phone call's poll budget no longer has to die in a log or
    get demoted to an email: she hangs up, does the work, and rings back.

    Three kinds:
      * briefing  — scheduled. The morning brief, as a call rather than an alarm.
      * callback  — she owes an answer to something asked on an earlier call.
      * alert     — something happened that she judged worth interrupting for.

    `opening` is the load-bearing field. On an INBOUND call the caller speaks
    first, so JARVIS can just say "JARVIS here." On an OUTBOUND call she is the
    one who rang, so she must open by saying WHY — otherwise the person answering
    has no idea what this is about.
    """

    __tablename__ = "outbound_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_number: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default="callback")  # briefing|callback|alert
    # What she says the moment the call connects. Generated BEFORE dialling, so
    # there is no dead air while an LLM thinks.
    opening: Mapped[str] = mapped_column(Text, default="")
    # Context handed to the orchestrator once the conversation starts.
    context: Mapped[str] = mapped_column(Text, default="")
    # queued | ringing | answered | no_answer | failed | done
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    call_sid: Mapped[str] = mapped_column(String(64), default="", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    # Don't ring at 3am. The scheduler respects this.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Watch(Base):
    """A condition JARVIS monitors and calls the owner about.

    The inversion that turns a tool into an assistant: until now she only ever
    moved when called. A watch means she acts while you're not thinking about her.
    """

    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool: Mapped[str] = mapped_column(String(64))
    tool_args: Mapped[str] = mapped_column(Text, default="{}")
    # Plain English. An LLM judges the tool's prose output against it — far more
    # robust than trying to regex "under $200" out of free text.
    condition: Mapped[str] = mapped_column(Text)
    # What she SAYS when she rings. Written before the call, as always.
    opening: Mapped[str] = mapped_column(Text)
    every_minutes: Mapped[int] = mapped_column(Integer, default=15)
    # False = tell them once and stop. A watch that nags is one the user disables.
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    fire_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(16), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocationPing(Base):
    """A position report FROM the phone.

    The phone pushes; JARVIS receives. Nothing here lets a voice on a phone line
    reach into the device — that asymmetry is the reason this is safe to build.
    """

    __tablename__ = "location_pings"

    id: Mapped[int] = mapped_column(primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(32), default="phone")
    label: Mapped[str] = mapped_column(String(120), default="")   # e.g. "leaving home"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Episode(Base):
    """A distilled, dated record of one conversation — the episodic memory tier.

    Tier 3 of the memory model (TDD #14 §1): `persona/preferences` are
    authoritative, `memories` are atomic inferred facts, and episodes are the
    *narrative* layer — "on DATE we discussed X." The raw turns stay in their
    per-channel cold store (`voice_turns` for calls, `messages` for text);
    an Episode is distilled FROM them at conversation close and is what JARVIS
    actually remembers with. `source_ref` points back at the cold store so
    "show me the actual call" stays answerable.

    The summary is interpretation (fallible, like a Memory). Anything
    load-bearing — a decision, a commitment — lives in EpisodeQuote, verbatim.
    """

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))                  # voice|sms|email|web
    thread_key: Mapped[str] = mapped_column(String(255), index=True)
    # THE temporal handle ("a couple of years ago") — owner-local calendar date.
    occurred_on: Mapped[date] = mapped_column(Date, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(512), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    topics: Mapped[str] = mapped_column(Text, default="[]")           # JSON array of tags
    action_items: Mapped[str] = mapped_column(Text, default="[]")     # JSON array
    salience: Mapped[float] = mapped_column(Float, default=0.5)
    # Portable embedding storage: JSON floats, searched with an in-Python cosine
    # fallback (episodic.py). Unlike Memory, episodes have NO pgvector mirror yet
    # — a mirror table can slot in later (audit L8 corrected the prior claim).
    embedding: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    quotes: Mapped[list["EpisodeQuote"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class EpisodeQuote(Base):
    """A VERBATIM fragment anchoring an episode's load-bearing claims.

    The faithfulness guarantee (TDD #14 §3): "you decided X" must be
    quote-anchored, and a quote is stored ONLY if it is a byte-for-byte
    substring of a raw turn (speaker-matched). A paraphrase is dropped at
    distill time — the one unacceptable failure is laundering a hallucination
    into "your exact words."
    """

    __tablename__ = "episode_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    speaker: Mapped[str] = mapped_column(String(16))                  # owner|jarvis
    quote: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="key_fact") # decision|commitment|key_fact|preference
    turn_ref: Mapped[str] = mapped_column(String(64), default="")
    episode: Mapped["Episode"] = relationship(back_populates="quotes")


class GoogleDocument(Base):
    """A Google Doc or Sheet JARVIS herself created (TDD #13).

    THE LOAD-BEARING TABLE (TDD #13 §5.3). append_to_google_doc accepts a
    doc_id ONLY if a row exists here. An arbitrary Drive file ID handed to
    JARVIS in conversation has no row and is refused — same principle as
    FlightOffer/offer_id in flight booking.

    Not thread-scoped (unlike FlightOffer): a document created in a prior
    conversation should remain appendable. The doc_id uniqueness constraint
    is the enforcement boundary.
    """

    __tablename__ = "google_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="doc")   # "doc" | "sheet"
    title: Mapped[str] = mapped_column(String(512), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    thread_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequestLog(Base):
    """One coarse row per top-level request — "what was JARVIS *asked*" (health
    TDD §9 Phase 2). Grain: per-request (a single "book me a flight" is ONE row),
    vs `actions_audit` which is per-*tool* (search + gate + book = several). The
    receipt is written at request start and committed independently, so a crashed
    request still leaves a row (an `in_progress` row that never resolved is itself
    the signal). Retention is time-based (§11) with a row-count safety valve.
    """

    __tablename__ = "request_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="")
    actor: Mapped[str] = mapped_column(String(255), default="")
    thread_key: Mapped[str] = mapped_column(String(255), default="", index=True)
    trigger: Mapped[str] = mapped_column(String(300), default="")   # first ~200 chars of the ask
    disposition: Mapped[str] = mapped_column(String(16), default="in_progress")  # in_progress|ok|error
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str] = mapped_column(Text, default="")


class Component(Base):
    """The deterministic system topology — one row per agent, external API, or
    subsystem (health TDD §4.1). Stable reference data, seeded from the topology
    and reconciled on startup (like the agent roster), editable at runtime.

    `check_type` selects which health check applies; `check_config` (JSON) carries
    that check's thresholds (e.g. the heartbeat staleness seconds), so PR-B reads
    them from here rather than hardcoding. `blast_radius=multi` marks the trunk
    (a failure there takes down many limbs) so it surfaces first.
    """

    __tablename__ = "component"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))          # agent|external_api|internal_subsystem|data_feed
    description: Mapped[str] = mapped_column(String(300), default="")
    depends_on: Mapped[str] = mapped_column(Text, default="")   # comma list of component names / secret names
    check_type: Mapped[str] = mapped_column(String(32), default="none")  # liveness|secret_age|published_expiry|heartbeat|freshness|none
    blast_radius: Mapped[str] = mapped_column(String(16), default="single")  # single|multi
    check_config: Mapped[str] = mapped_column(Text, default="")  # JSON thresholds for the check
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Remediation(Base):
    """The fault -> fix mapping (health TDD §4.2). A tripped check emits a
    `fault_code` against a `component`; the surfacing layer JOINS to the matching
    row for the "place to start". Seeded, runtime-editable (edit a row when a
    consent flow changes — no redeploy)."""

    __tablename__ = "remediation"
    __table_args__ = (UniqueConstraint("component", "fault_code", name="uq_remediation_component_fault"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    component: Mapped[str] = mapped_column(String(64), index=True)
    fault_code: Mapped[str] = mapped_column(String(64))
    runbook: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="warn")   # info|warn|critical
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HealthResult(Base):
    """Transient current status — latest per component, overwritten each check
    (health TDD §4.3). NOT history and NOT reference data: kept separate from
    `component`/`remediation` on purpose. `fault_code` (when not ok) joins to
    `remediation` for the runbook."""

    __tablename__ = "health_result"

    component: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok|degraded|down|unknown
    fault_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    age_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SchedulerHeartbeat(Base):
    """Proof-of-life for the worker's briefing scheduler (health TDD §5.2, §6).

    A single row (id=1), upserted on every worker tick. `beat_at` is how the §5.2
    health check tells a live scheduler from a dead one (a stale beat = the worker
    died). `last_briefing_date` (owner-tz) is the missed-run catch-up guard — it
    fires the brief once even if the worker was down at the scheduled minute, and
    never twice the same day. `next_run_at`/`enabled` let the check report the next
    run or say "disabled" instead of "down".
    """

    __tablename__ = "scheduler_heartbeat"

    id: Mapped[int] = mapped_column(primary_key=True)          # always 1
    beat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_briefing_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class RuntimeSetting(Base):
    """A runtime override for a bounded allow-list of behavioral settings
    (briefing time, quiet hours, outbound-call toggles). The overlay accessor
    `app.runtime_settings.get_effective` returns this row's value when present,
    else the env/`Settings` default — so behavior is visible and changeable
    without a redeploy (health TDD §7). Value is stored as text and coerced to
    the key's declared type on read. NEVER holds a secret: the allow-list in
    `runtime_settings.ALLOWED_KEYS` is the enforcement boundary.
    """

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
