from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
