# BUILD ORDER — "Unauthorized" regression diagnostic

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Type:** Diagnostic-only. **No code changes** until logs identify the layer. Report findings back before any fix.

---

## Symptom (from admin UI screenshot)

- Location fix + nearby-restaurants reply worked end-to-end (real ping, real Places results).
- Immediately after, **three consecutive messages** returned a bare `Unauthorized`:
  - "How about now? Can you see a history of pings?"
  - "Unauthorized?"
  - "Good morning"
- `Good morning` was working earlier in the session. The failure is now **content-independent and uniform** — every message 401s identically.

This pattern (uniform, mid-session onset, right after a good turn) points at **session/token state or a request-level auth dependency**, not a per-tool permission gate.

---

## Do these in order. Stop and report after each numbered block.

### 1. Locate the exact failure and its origin

```
fly logs -a jarvis-mdk
```

Find the first `Unauthorized` timestamp (right after the successful nearby-restaurants turn). For that request, capture:

- The full exception/stack, or the status line if it's a returned response not a raised error.
- **Is "Unauthorized" our own app's string, or a passthrough from upstream?** Grep the repo:
  ```
  grep -rn "Unauthorized" backend/app
  ```
  - If it's **our** string → which layer emits it (a FastAPI `Depends` auth dependency, middleware, a route guard)?
  - If it's **not** in our source → it's an upstream 401 (Anthropic / Google / Twilio / Tavily) surfacing raw. Note which client call raised it.

**Report:** the raising layer + whether string is ours or upstream. This single fact splits the tree.

---

### 2. Confirm scope — is it truly every route, or the orchestrator path specifically?

- Does a pure-static or health route still respond, while all chat/orchestrator requests 401? (Tells us middleware-global vs orchestrator-entry.)
- Is the admin UI chat authenticating **differently** from the voice/SMS path? If voice/SMS still works and only the UI 401s, that narrows it to the UI session/token, not a shared secret.

**Report:** global vs orchestrator-scoped; UI-only vs all channels.

---

### 3. Session / token state at the failure timestamp

Depending on what §1 pointed at:

- **If our auth dependency:** what is it checking — a session cookie/JWT, a header token, a DB-backed session row? Did that token expire or get invalidated between the good turn and the first failure? Check TTL/expiry logic and the actual token's issued/expiry times.
- **If upstream 401:** which credential. Given recent OAuth refresh-token minting, **Google OAuth refresh token is the prime suspect.** Check:
  ```
  fly secrets list -a jarvis-mdk
  ```
  for anything rotated near the failure timestamp, and confirm `GOOGLE_OAUTH_REFRESH_TOKEN` (and any Anthropic/Twilio/Tavily key) is present and current.

**Report:** which token/credential, its state, and whether anything rotated between the working turn and the failures.

---

### 4. Correlate against recent changes

- Any deploy, `fly secrets set`, or config change between the last good turn and the first `Unauthorized`? (`fly releases -a jarvis-mdk`, recent commits.)
- The location work (PRs #36–#40) and recent OAuth token minting are the two things that touched auth-adjacent state lately — check whether either landed in this window.

**Report:** any change in the window, with timestamp.

---

## Guardrails

- **Diagnosis before code.** No fix, no redeploy, no secret rotation until §1–§4 identify the layer. A blind `fly secrets set` could mask the real cause.
- **`unknown` never maps to green** — if a check can't confirm a credential is valid, report it as unknown/failing, not assumed-fine.
- If the fix turns out to be a secret rotation or an outward-facing switch, that's **gated — explicit approval before you flip it.**

## Expected output back to Planner

The raising layer, ours-vs-upstream, scope (global/orchestrator, UI-only/all-channels), the specific credential or session at fault, and any change in the failure window. That's enough to write the fix order.
