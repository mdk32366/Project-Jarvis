import logging
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text, select
from sqlalchemy.orm import Session
from app.auth import authenticate_user, create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models import ActionAudit, AgentConfig, Conversation, Job, Memory, Message, PersonaProfile, Preference, User
from app.schemas import (AgentIn, AgentOut, AuditOut, ChangePasswordIn, ChatRequest, ChatResponse,
    ConversationOut, HealthResponse, JobOut, MemoryIn, MemoryOut, MessageOut, PersonaOut, PreferenceOut, Token, UserOut)

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


# ── Voice channel (Twilio webhooks) ──────────────────────────────────────────
async def _validated_params(request: Request) -> dict:
    """Twilio signature check. Same pattern as sms_inbound, per-route URL."""
    from app.providers.sms import get_sms_provider

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    signature = request.headers.get("X-Twilio-Signature", "")

    # Per-route URL: base + this request's path & query, else fall back to the
    # observed URL. Behind Fly's proxy, str(request.url) may show http:// — set
    # VOICE_PUBLIC_URL_BASE in prod so the signature base string matches what
    # Twilio signed.
    base = getattr(settings, "voice_public_url_base", None)
    url = f"{base}{request.url.path}" if base else str(request.url)
    if base and request.url.query:
        url = f"{url}?{request.url.query}"

    provider = get_sms_provider()
    if not provider.validate_signature(url, params, signature):
        log.warning("Rejected voice webhook: bad signature (%s)", request.url.path)
        raise HTTPException(status_code=403, detail="Invalid signature")
    return params


def _xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


def _voice_party(db: Session, params: dict) -> str:
    """The HUMAN's number on this call, regardless of who dialled whom.

    On an inbound call Twilio's From is the caller — the human. On a call WE
    placed, From is JARVIS's own Twilio number and the human is in To. Trusting
    From blindly meant every outbound call's first reply was rejected as
    NOT_AUTHORIZED — the allowlist was vetting JARVIS against herself, so no
    outbound call ever completed a single conversational turn.

    Direction is decided by OUR outbound_calls row for this CallSid — we minted
    that sid at dial time, so it can't be spoofed from outside — not by Twilio's
    Direction param, which we'd have to take on faith.
    """
    from app.channels import outbound_voice as ov

    call_sid = params.get("CallSid", "")
    if call_sid and ov.get_by_sid(db, call_sid) is not None:
        return params.get("To", "")
    return params.get("From", "")


@router.post("/voice/inbound", tags=["jarvis"], include_in_schema=False)
async def voice_inbound(request: Request, db: Session = Depends(get_db)):
    """Call connected. Whitelist, then greet and listen."""
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    from_number = params.get("From", "")
    call_sid = params.get("CallSid", "")

    # Kill switch. Caller-ID auth is spoofable, so this is the documented way to
    # take the whole voice channel offline; it must actually gate the entrypoint
    # (see audit H4 — it previously gated nothing).
    if not settings.voice_enabled:
        log.info("Voice disabled (voice_enabled=False); hanging up on %s", call_sid)
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    if not vp.is_allowed(db, from_number):
        # Deliberately uninformative: don't confirm to a stranger that they've
        # found a system worth probing.
        log.info("Rejecting voice call from non-whitelisted number: %s", from_number)
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    log.info("Voice call started: %s from %s", call_sid, from_number)
    return _xml(vp.twiml_gather(vp.GREETING, turn=0))


@router.post("/voice/gather", tags=["jarvis"], include_in_schema=False)
async def voice_gather(
    request: Request,
    background: BackgroundTasks,
    turn: int = 0,
    db: Session = Depends(get_db),
):
    """Speech transcript arrives. Kick off orchestration, return TwiML at once."""
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    from_number = _voice_party(db, params)
    call_sid = params.get("CallSid", "")

    if not vp.is_allowed(db, from_number):
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    if turn >= vp.MAX_TURNS:
        vp.email_transcript(db, call_sid, from_number)
        return _xml(vp.twiml_hangup("We've been at this a while. I'll email you. Goodbye."))

    speech = (params.get("SpeechResult") or "").strip()
    if not speech:
        # Nothing heard — re-prompt without burning a turn.
        return _xml(vp.twiml_gather("I didn't catch that. Say again?", turn=turn))

    if speech.lower().rstrip(".!") in {"goodbye", "hang up", "that's all", "nothing else"}:
        vp.email_transcript(db, call_sid, from_number)
        return _xml(vp.twiml_hangup("Very good. Goodbye."))

    # THE [error] FIX: never start a turn while an earlier one is still
    # orchestrating. Two run_turns sharing a CallSid share a thread_key, share a
    # conversation row, and collide — the loser was recorded as [error].
    #
    # This happened constantly because the poll budget expired mid-orchestration,
    # the caller heard "I'll email you," and spoke again while the abandoned task
    # was still running.
    if vp.prior_turn_still_running(db, call_sid, turn):
        log.info("turn %s/%s deferred — prior turn still running", call_sid, turn)
        return _xml(vp.twiml_gather(
            "Still finishing the last one — give me a moment, then say that again.",
            turn=turn,
        ))

    log.info("Voice turn %s/%s: %r", call_sid, turn, speech)
    vp.open_turn(db, call_sid, turn, speech)
    # Runs after this response is sent. THIS is why we don't call orchestrate()
    # inline — it can take far longer than Twilio will wait.
    # NOTE: run_turn deliberately does NOT receive `db`. Depends(get_db) closes
    # the session when this response is sent; a BackgroundTask runs after that.
    # run_turn opens its own session. Passing `db` here caused every call's last
    # turn to fail with "[error]".
    background.add_task(vp.run_turn, call_sid, turn, from_number, speech)

    return _xml(vp.twiml_working(call_sid, turn, poll=0))


@router.post("/voice/poll", tags=["jarvis"], include_in_schema=False)
async def voice_poll(
    request: Request,
    turn: int = 0,
    poll: int = 0,
    db: Session = Depends(get_db),
):
    """Is the turn done yet? Speak it, or bounce again, or bail to email."""
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    from_number = _voice_party(db, params)
    call_sid = params.get("CallSid", "")

    if not vp.is_allowed(db, from_number):
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    row = vp.get_turn(db, call_sid, turn)

    if row is None:
        log.error("poll: no turn row for %s/%s", call_sid, turn)
        return _xml(vp.twiml_gather("Something went wrong. Try again?", turn=turn + 1))

    if row.status == "done":
        return _xml(vp.twiml_gather(row.reply or "Done.", turn=turn + 1))

    if row.status == "error":
        return _xml(
            vp.twiml_gather("I hit an error on that one. Try something else?", turn=turn + 1)
        )

    # Still pending. Rather than dump the caller to email (or, worse, loop
    # "Still there?"), offer the real choice: hold the line while she finishes,
    # or hand off to email. Note turn stays the SAME — hold_choice acts on THIS
    # still-running turn, it does not start a new one.
    if poll >= vp.MAX_POLLS:
        log.info("poll budget exhausted for %s/%s — offering hold/handoff", call_sid, turn)
        return _xml(vp.twiml_gather(vp.TIMEOUT_FALLBACK, turn=turn,
                                    action="/api/voice/hold_choice"))

    return _xml(vp.twiml_working(call_sid, turn, poll=poll))


@router.post("/voice/hold_choice", tags=["jarvis"], include_in_schema=False)
async def voice_hold_choice(request: Request, turn: int = 0, db: Session = Depends(get_db)):
    """Caller answered 'wait or email?'. Enter the hold loop, or hand off.

    `turn` is the STILL-RUNNING turn we're waiting on — not a new one.
    """
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    from_number = _voice_party(db, params)
    call_sid = params.get("CallSid", "")

    if not vp.is_allowed(db, from_number):
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    # It may have finished while she was asking the question.
    row = vp.get_turn(db, call_sid, turn)
    if row is not None and row.status == "done":
        return _xml(vp.twiml_gather(row.reply or "Done.", turn=turn + 1))
    if row is not None and row.status == "error":
        return _xml(vp.twiml_gather("I hit an error on that one. Try something else?", turn=turn + 1))

    speech = (params.get("SpeechResult") or "").strip()
    if vp.wants_callback(speech):
        vp.mark_notify_on_completion(db, call_sid, turn, from_number)
        return _xml(vp.twiml_hangup(vp.HANDOFF_LINE))

    # Hold. `since` stamps when holding began, so /voice/hold can bound it.
    return _xml(vp.twiml_hold(turn, since=int(time.time()), intro=True))


@router.post("/voice/hold", tags=["jarvis"], include_in_schema=False)
async def voice_hold(request: Request, turn: int = 0, since: int = 0,
                     db: Session = Depends(get_db)):
    """Keep the line warm on a running turn: speak the answer when ready, or hand
    off to email once we've held longer than voice_hold_max_seconds."""
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    from_number = _voice_party(db, params)
    call_sid = params.get("CallSid", "")

    if not vp.is_allowed(db, from_number):
        return _xml(vp.twiml_hangup(vp.NOT_AUTHORIZED))

    row = vp.get_turn(db, call_sid, turn)
    if row is None:
        return _xml(vp.twiml_gather("Something went wrong. Try again?", turn=turn + 1))
    if row.status == "done":
        # The answer landed — speak it and re-open the conversation.
        return _xml(vp.twiml_gather(row.reply or "Done.", turn=turn + 1))
    if row.status == "error":
        return _xml(vp.twiml_gather("I hit an error on that one. Try something else?", turn=turn + 1))

    elapsed = int(time.time()) - since
    if since <= 0 or elapsed >= settings.voice_hold_max_seconds:
        # Held long enough. Hand off: the answer will be emailed on completion.
        vp.mark_notify_on_completion(db, call_sid, turn, from_number)
        vp.email_transcript(db, call_sid, from_number)
        return _xml(vp.twiml_hangup(vp.HANDOFF_LINE))

    return _xml(vp.twiml_hold(turn, since=since, intro=False))


@router.post("/voice/outbound", tags=["jarvis"], include_in_schema=False)
async def voice_outbound(request: Request, call: int = 0, db: Session = Depends(get_db)):
    """JARVIS placed this call. Twilio fetches TwiML here once it's answered.

    The difference from /voice/inbound: SHE rang, so she opens by saying who she
    is and why — the person answering has no idea what this is about otherwise.
    The opening was written BEFORE dialling, so there's no dead air.
    """
    from app.channels import outbound_voice as ov
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    row = ov.get_by_id(db, call)
    if row is None:
        log.error("outbound webhook for unknown call id %s", call)
        return _xml(vp.twiml_hangup("Sorry, wrong number."))

    # Answering machine: leave the opening and go. Don't monologue at voicemail.
    if (params.get("AnsweredBy") or "").startswith("machine"):
        log.info("call #%s hit voicemail", row.id)
        row.status = "no_answer"
        db.commit()
        return _xml(vp.twiml_hangup(row.opening))

    row.status = "answered"
    if not row.call_sid:
        row.call_sid = params.get("CallSid", "")
    db.commit()

    # Seed the conversation with WHY she called, so the first thing the user says
    # ("go on", "what about it?") lands in context rather than in a vacuum.
    if row.context:
        vp.seed_context(db, row.call_sid or params.get("CallSid", ""), row.context)

    log.info("outbound call #%s answered (%s)", row.id, row.kind)
    return _xml(vp.twiml_gather(row.opening, turn=0))


@router.post("/voice/status", tags=["jarvis"], include_in_schema=False)
async def voice_status(request: Request, db: Session = Depends(get_db)):
    """Twilio status callback — fires on call completion. Emails the transcript.

    Configure as the number's Status Callback URL with event 'completed'.
    """
    from app.channels import voice_pipeline as vp

    params = await _validated_params(request)
    call_sid = params.get("CallSid", "")
    from_number = _voice_party(db, params)
    call_status = params.get("CallStatus", "")

    # Twilio fires this callback on ANY terminal state, with the real outcome
    # in CallStatus: completed | busy | no-answer | failed | canceled. We must
    # act on all of them — an unanswered outbound call that only closed out on
    # "completed" would sit in the OutboundCall table as "ringing" forever, so
    # due_calls never re-dials it, pending_callbacks lists a ghost, and
    # cancel_callback (which only cancels "queued") can't clear it.
    _TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}
    if call_status not in _TERMINAL:
        return _xml(vp.twiml_empty())

    log.info("Voice call ended: %s (%s)", call_sid, call_status)

    # If this was a call SHE placed, close the row out based on the outcome.
    from app.channels import outbound_voice as ov

    row = ov.get_by_sid(db, call_sid)
    if row is not None and row.status in ("ringing", "answered"):
        if call_status == "completed":
            # "completed" only means the call connected and hung up normally if
            # we actually answered it; an unanswered call can also report
            # completed after ringing out, so trust the row's own state.
            row.status = "done" if row.status == "answered" else "no_answer"
        else:
            row.status = "no_answer" if call_status in ("busy", "no-answer") else "failed"
        db.commit()

    if call_status == "completed":
        vp.email_transcript(db, call_sid, from_number)

        # Episodic memory (TDD #14): the call is over — enqueue distillation.
        # One trigger covers inbound and outbound alike (this callback fires
        # for both). Never inline, never fatal: memory is best-effort, a
        # distiller bug must not break the hangup path. Only for connected
        # calls — an unanswered call has no turns worth distilling.
        try:
            from app.episodic import close_episode

            close_episode(db, "voice", call_sid, source_ref=call_sid)
        except Exception as e:  # noqa: BLE001
            log.error("could not enqueue episode distillation for %s: %s", call_sid, e)

    return _xml(vp.twiml_empty())


# ── Location ingest (the phone reports where it is) ──────────────────────────
# The phone PUSHES; JARVIS receives. Nothing here lets a voice on a phone line
# reach into the device — that asymmetry is why this is safe.
#
# AUTH: a shared secret in X-Jarvis-Token. Tasker isn't Twilio, so it can't sign
# requests. Possession of the secret IS the authentication, which makes this
# endpoint strictly STRONGER than the voice channel's spoofable caller ID.
@router.post("/location", tags=["jarvis"], include_in_schema=False)
async def location_ingest(request: Request, db: Session = Depends(get_db)):
    from app.handlers.location import record_ping

    if not settings.location_token:
        raise HTTPException(status_code=503, detail="Location reporting is not configured")

    token = request.headers.get("X-Jarvis-Token", "")
    # Constant-time compare: a plain == leaks the secret one byte at a time to
    # anyone willing to measure. Cheap to do right.
    import hmac

    if not hmac.compare_digest(token, settings.location_token):
        log.warning("location ping rejected: bad token")
        raise HTTPException(status_code=403, detail="Invalid token")

    # PARSE ONCE, FROM THE RAW BYTES.
    #
    # The obvious version -- try request.json(), fall back to request.form() --
    # is a trap: .json() CONSUMES the body stream. When it fails, .form() finds
    # an empty stream and raises, and that exception is unhandled. 500.
    #
    # Read the bytes once and try each shape against them. Tasker sends whatever
    # it feels like depending on version and how the Body field was filled in, so
    # accept JSON, form-encoded, and query params, and stop being precious about it.
    import json as _json
    from urllib.parse import parse_qs

    raw = (await request.body()).decode("utf-8", errors="replace").strip()
    body: dict = {}

    if raw:
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            # form-encoded: lat=48.5&lon=-122.6
            body = {k: v[0] for k, v in parse_qs(raw).items() if v}

    # ...and query params, because Tasker's "Query Parameters" field is the least
    # fiddly of the three and people will reasonably use it.
    for k, v in request.query_params.items():
        body.setdefault(k, v)

    try:
        lat = float(body["lat"])
        lon = float(body["lon"])
    except (KeyError, TypeError, ValueError):
        log.warning("location ping had no usable lat/lon; raw body was: %r", raw[:200])
        raise HTTPException(
            status_code=400,
            detail=("lat and lon are required. Send JSON "
                    '{"lat":48.5,"lon":-122.6}, form-encoded lat=48.5&lon=-122.6, '
                    "or query params."),
        )

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="lat/lon out of range")

    # Tasker will happily send accuracy as "" or as an unresolved "%gl_accuracy".
    # A bad accuracy value must never lose a good position.
    try:
        accuracy = float(body.get("accuracy") or 0)
    except (TypeError, ValueError):
        accuracy = 0.0

    p = record_ping(
        db, lat=lat, lon=lon,
        accuracy_m=accuracy,
        source=str(body.get("source") or "phone"),
        label=str(body.get("label") or ""),
    )
    log.info("location ping: %.4f,%.4f (%s)", lat, lon, p.label or "no label")
    return {"ok": True, "id": p.id}


@router.get("/memory/audit", tags=["memory"])
def memory_audit(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything JARVIS believes about the owner, as plain text.

    The same content she emails on request — but readable in the browser, because
    an audit you have to ask for out loud is one you'll never actually do.
    """
    from app.handlers.audit import build_audit

    return Response(content=build_audit(db), media_type="text/plain")


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


# ── Admin: agents (data-driven specialist roster) ────────────────────────────
import json as _json


def _agent_out(a: AgentConfig) -> AgentOut:
    return AgentOut(id=a.id, name=a.name, description=a.description,
                    system_prompt=a.system_prompt, tools=_json.loads(a.tools or "[]"),
                    enabled=a.enabled)


@router.get("/agents", response_model=list[AgentOut], tags=["admin"])
def list_agents(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_agent_out(a) for a in db.execute(select(AgentConfig).order_by(AgentConfig.name)).scalars().all()]


@router.get("/agents/tools", tags=["admin"])
def assignable_tools(_: User = Depends(get_current_user)):
    """Tool names a sub-agent can be assigned (the sub-agent registry)."""
    from app.handlers.base import build_registry
    return {"tools": [t["name"] for t in build_registry().anthropic_tools()]}


@router.post("/agents", response_model=AgentOut, tags=["admin"])
def create_agent(item: AgentIn, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.execute(select(AgentConfig).where(AgentConfig.name == item.name)).scalars().first():
        raise HTTPException(status_code=409, detail="Agent name already exists")
    a = AgentConfig(name=item.name, description=item.description, system_prompt=item.system_prompt,
                    tools=_json.dumps(item.tools), enabled=item.enabled)
    db.add(a); db.commit(); db.refresh(a)
    return _agent_out(a)


@router.put("/agents/{agent_id}", response_model=AgentOut, tags=["admin"])
def update_agent(agent_id: int, item: AgentIn, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(AgentConfig, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Not found")
    a.name = item.name; a.description = item.description; a.system_prompt = item.system_prompt
    a.tools = _json.dumps(item.tools); a.enabled = item.enabled
    db.commit(); db.refresh(a)
    return _agent_out(a)


@router.delete("/agents/{agent_id}", tags=["admin"])
def delete_agent(agent_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(AgentConfig, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(a); db.commit()
    return {"deleted": agent_id}


@router.get("/audit", response_model=list[AuditOut], tags=["admin"])
def list_audit(limit: int = 100, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(ActionAudit).order_by(ActionAudit.created_at.desc()).limit(limit)).scalars().all()
    return rows


@router.get("/calendar/health", tags=["admin"])
def calendar_health(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the RAW calendar_lookup output so calendar setup issues are visible."""
    from app.handlers.base import Context
    from app.handlers.scheduling import _calendar_lookup
    ctx = Context(db=db, channel="web", actor="admin", thread_key="admin")
    return {"result": _calendar_lookup({"range": "this week"}, ctx)}


@router.get("/infra/health", tags=["admin"])
def infra_health(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return RAW fleet health + spend so Fly setup issues are visible in the UI."""
    from app.handlers.base import Context
    from app.handlers.infra import _fleet_health, _fleet_spend

    ctx = Context(db=db, channel="admin", actor="admin", thread_key="infra")
    return {"health": _fleet_health({}, ctx), "spend": _fleet_spend({}, ctx)}


@router.post("/auth/change-password", tags=["auth"])
def change_password(body: ChangePasswordIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    current_user.hashed_password = hash_password(body.new_password)
    db.commit()
    return {"status": "password changed"}


@router.get("/briefing", tags=["jarvis"])
def briefing_preview(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Compose the morning briefing on demand (does not email)."""
    from app.briefing import compose_briefing
    return {"briefing": compose_briefing(db)}


@router.post("/briefing/send", tags=["jarvis"])
def briefing_send(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Compose and email the briefing to the owner now."""
    from app.briefing import send_briefing
    return {"status": send_briefing(db)}
