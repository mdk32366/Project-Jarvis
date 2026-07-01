from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
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
