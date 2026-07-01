import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text, select
from sqlalchemy.orm import Session
from app.auth import authenticate_user, create_access_token, get_current_user
from app.config import settings
from app.database import get_db
from app.models import Conversation, Job, Memory, Message, PersonaProfile, Preference, User
from app.schemas import (ChatRequest, ChatResponse, ConversationOut, HealthResponse,
    JobOut, MemoryIn, MemoryOut, MessageOut, PersonaOut, PreferenceOut, Token, UserOut)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1")); db_status = "connected"
    except Exception:
        db_status = "error"
    return HealthResponse(status="ok", environment=settings.environment, database=db_status)

@router.post("/auth/login", response_model=Token, tags=["auth"])
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password", headers={"WWW-Authenticate": "Bearer"})
    return Token(access_token=create_access_token(user.username))

@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(current_user: User = Depends(get_current_user)):
    return current_user

@router.post("/chat", response_model=ChatResponse, tags=["jarvis"])
def chat(req: ChatRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.orchestrator import run as orchestrate
    reply = orchestrate(db=db, channel="web", thread_key=f"web:{current_user.username}:{req.thread_key}", user_text=req.message, actor=current_user.username)
    return ChatResponse(reply=reply)

# ── SMS channel (Twilio webhook) ─────────────────────────────────────────────
# Unauthenticated (Twilio calls it) but protected by signature validation + a
# phone-number whitelist. Replies are returned as TwiML.
@router.post("/sms/inbound", tags=["jarvis"], include_in_schema=False)
async def sms_inbound(request: Request, db: Session = Depends(get_db)):
    from app.channels.sms_pipeline import handle_inbound, to_twiml
    from app.providers.sms import get_sms_provider

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")
    url = settings.sms_public_url or str(request.url)

    provider = get_sms_provider()
    if not provider.validate_signature(url, params, signature):
        log.warning("Rejected SMS webhook: bad signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    from_number = params.get("From", "")
    body = params.get("Body", "")
    reply = handle_inbound(db, from_number, body)
    # Non-whitelisted (reply is None) => empty TwiML, no message sent back.
    return Response(content=to_twiml(reply or ""), media_type="application/xml")

@router.get("/memory/persona", response_model=list[PersonaOut], tags=["memory"])
def list_persona(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(PersonaProfile)).scalars().all()

@router.get("/memory/preferences", response_model=list[PreferenceOut], tags=["memory"])
def list_preferences(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Preference)).scalars().all()

@router.get("/memory", response_model=list[MemoryOut], tags=["memory"])
def list_memories(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Memory).order_by(Memory.created_at.desc())).scalars().all()

@router.post("/memory", response_model=MemoryOut, tags=["memory"])
def add_memory(item: MemoryIn, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.memory import remember
    m = remember(db, content=item.content, category=item.category, source="manual", sensitive=item.sensitive)
    return m

@router.delete("/memory/{memory_id}", tags=["memory"])
def delete_memory(memory_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(Memory, memory_id)
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(m); db.commit(); return {"deleted": memory_id}

@router.get("/conversations", response_model=list[ConversationOut], tags=["jarvis"])
def list_conversations(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Conversation).order_by(Conversation.created_at.desc()).limit(50)).scalars().all()

@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut], tags=["jarvis"])
def conversation_messages(conversation_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)).scalars().all()

@router.get("/jobs", response_model=list[JobOut], tags=["jarvis"])
def list_jobs(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Job).order_by(Job.created_at.desc()).limit(50)).scalars().all()
