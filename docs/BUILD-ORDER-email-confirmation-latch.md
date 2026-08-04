# BUILD ORDER — the email confirmation latch

**Priority: first.** This is a total, silent outage of the confirmation gate on the
email channel. Every gated tool — `send_email`, `create_event` with attendees,
`book_flight`, `create_project_from_idea`, `create_project_repo` — is currently
unreachable by email. Observed 2026-08-04: eleven confirmations across two project
creations, none of which resolved.

**Step 0 — `alembic heads`.** Expected `0029_plan_draft_status`. No migration in this
order; confirm anyway, because a surprise here means the clone is not where the order
thinks it is.

---

## 1. The diagnosis, so the Builder is not re-deriving it

Two independent causes. **Each is sufficient on its own.** Fixing one and shipping
leaves the latch in place.

### Cause A — quoted reply text defeats `_bare_match`

`backend/app/channels/email_pipeline.py::_body_text` returns the entire `text/plain`
part of the inbound message. There is no quoted-text stripping anywhere in the
codebase — verified by grep, not by memory.

So the `user_text` reaching `orchestrator.run()` on a Gmail reply is the owner's word
followed by the whole quoted thread:

```
Confirm.

On Tue, Aug 4, 2026 at 6:19 AM jarvismajorus@gmail.com wrote:
> One more explicit confirm needed on this one — reply confirm and I'll
> create the MANETMDK repo (public, from Idea #5).
> ...
```

`backend/app/orchestrator.py::_bare_match` requires **every** token to be an
affirmative token or a `_CONFIRM_FILLER` word:

```python
return all(w in tokens or w in _CONFIRM_FILLER for w in norm.split())
```

A quote block is hundreds of content words. It returns `False` unconditionally. The
turn falls through to normal handling, the model reads the quoted request as a fresh
one, calls the gated tool again, and a **new** `PendingConfirmation` is raised. That
is the loop.

**This is the previous fix over-correcting.** `_bare_match`'s own docstring records
that the prior check fired on anything *starting* with "yes", which sent a
36-hour-old email. The correction moved from over-permissive to unsatisfiable, on the
one channel that quotes. Both failures are the same defect wearing opposite signs:
the boundary between "a confirmation" and "a new instruction" was never separated
from the transport's framing.

### Cause B — a 15-minute TTL against email latency

`settings.pending_confirmation_ttl_seconds = 900`, enforced in
`orchestrator.py::_resolve_pending` (the age check) and
`orchestrator.py::_expire_stale_pending` (the sweep).

Observed reply gaps on 2026-08-04: **76, 32, and 45 minutes.** Every one exceeded the
TTL. **Fix A alone leaves all four of those confirmations expiring.**

The defect is not the number. It is that one constant spans three channels whose
natural latencies differ by orders of magnitude — a call is bounded in seconds, a
chat turn in minutes, an email in hours. `_VOCAB` already recognises that channels
differ and narrows the vocabulary for voice; the TTL never got the same treatment.

---

## 2. What NOT to do

- **Do not raise `pending_confirmation_ttl_seconds` globally.** It is correct where it
  is for voice and chat, and the TTL is a safety control — it exists so a stale "yes"
  cannot fire an hours-old buffered action. Widening it everywhere to fix one channel
  trades a real guarantee for a transport bug.
- **Do not relax `_bare_match` itself.** The all-tokens rule is right. The input is
  wrong. Strip the quote before the test; leave the test alone.
- **Do not strip quotes inside the orchestrator.** Quoting is a mail-transport
  artefact and belongs at the channel boundary, in `email_pipeline`. Putting it in
  `orchestrator` would apply it to SMS, voice and chat bodies that never quote, and
  would make a mail concern load-bearing for every channel.

---

## 3. Step 1 — strip quoted text at the email boundary

New pure function in `backend/app/channels/email_pipeline.py`. Pure: text in, text
out, no DB, no network, no logging — the same shape as `app/secretscan.py`, and for
the same reason: it is fully testable offline and the enforcement happens at the call
site.

**Handle at minimum:**

| Shape | Example |
|---|---|
| Gmail/Apple attribution line + `>` block | `On Tue, Aug 4, 2026 at 6:19 AM X wrote:` |
| Bare `>` quote levels with no attribution | `> One more explicit confirm...` |
| Outlook separator | `-----Original Message-----`, `________________________________` |
| Signature delimiter | a line that is exactly `-- ` |
| `From:`/`Sent:`/`To:`/`Subject:` header block | Outlook inline reply style |

**Truncate at the first quote marker; keep everything above it.** Do not attempt to
reassemble interleaved replies — an inline-quoted reply where the owner has typed
between quote levels is a *new instruction*, not a bare confirmation, and falling
through to normal handling is the correct outcome for it.

**The empty case is load-bearing.** If stripping yields an empty string — a
top-posted reply with nothing typed above the quote — return the **original,
unstripped** body. An empty `user_text` would be a different bug, and a stripper that
can silently erase a message is worse than one that occasionally under-strips.

Call it in `_body_text`, or immediately after it at the single call site before
`orchestrate(...)`. Name the module path in the commit message.

## 4. Step 2 — per-channel confirmation TTL

Mirror the existing `_VOCAB` / `_vocab(channel)` pattern in
`backend/app/orchestrator.py`. A module-level map, a `_ttl(channel)` accessor, and
`settings.pending_confirmation_ttl_seconds` as the default for any channel not named.

| Channel | TTL | Why |
|---|---|---|
| `voice` | unchanged (900) | A call is bounded by CallSid; a stale yes from a prior call already cannot resolve a new confirmation. Nothing here needs widening. |
| `web` / chat | unchanged (900) | Live session. Correct as-is. |
| `sms` | unchanged (900) | Live-ish, and a stale SMS yes is a real hazard. |
| `email` | **4 hours**, `email_confirmation_ttl_seconds`, on the runtime allow-list | Bounded by the working day rather than the session. Long enough for a reply from bed; short enough that yesterday's readback is dead. |

**Both the age check in `_resolve_pending` and the cutoff in `_expire_stale_pending`
must read through `_ttl(channel)`.** They are two computations of one rule and will
drift if only one is changed — the same shape as the naming-check duplication already
queued from #76.

`_expire_stale_pending` takes only `thread_key` today. It needs the channel. Pass it;
do not infer it from the thread key.

Add `email_confirmation_ttl_seconds` to `app/runtime_settings.py::ALLOWED_KEYS` with a
sane bound (min 300, max 86400) so the window is tunable without a redeploy. It is
behavioural, not a secret, so it belongs on the overlay.

## 5. Step 3 — the plants

**Do not report a green suite as evidence.** Each property below gets a plant that is
verified applied before its result is read.

| # | Property | Plant | Must go red |
|---|---|---|---|
| P1 | A quoted reply resolves | Bypass the stripper (pass the raw body) | The end-to-end email-confirm test |
| P2 | A new instruction still falls through | Make the stripper truncate at the first blank line instead of the first quote marker | The "Yes please also do X" test — it must NOT confirm |
| P3 | Email TTL is actually longer | Point `_ttl("email")` at the global default | The 45-minute-gap test |
| P4 | Both TTL readers share one rule | Change `_ttl` and confirm **both** the `_resolve_pending` test and the `_expire_stale_pending` test redden | **If only one reddens they are not sharing it** — that is the whole property |
| P5 | The stripper cannot erase a message | Remove the empty-result fallback | The top-posted-empty test |

**P2 is the one that matters most.** The 36-hour-old-email bug is the failure this
system already made once, and a stripper that is too aggressive reintroduces it by a
new route. A test asserting that a reply carrying a genuine new instruction does *not*
confirm is non-negotiable, and it must be negative-validated — §2.7 applies: inject a
value no branch can legitimately produce, not one that coincides with an expected
output.

## 6. Step 4 — the audit-blindness note

This outage produced a stream of healthy-looking `actions_audit` rows for its entire
life. Refusals and re-confirmations are deliberately in the ok-family (a refused
booking is a healthy system), so an audit-derived check is **structurally incapable**
of seeing a gate that never resolves.

**Do not build a health check for this in this order.** Record it instead — a short
entry in `docs/findings.md`, newest first, naming what was believed, what was
measured, and what changes. This is the fourth member of the latch family after the
relay body, calendar liveness, and the UI auth latch, and all four share one shape: a
failure that emits fluent, well-formed, plausible output.

The trigger for building detection is named here rather than acted on: **a
confirmation raised and re-raised on the same thread more than twice without
resolution.** That is a countable fact, it needs no judgement, and it is the signal
this outage would have tripped on day one. It belongs in the defect journal
(`TDD-defect-journal.md`) as its first automatic detector, not as a bespoke component.

## 7. Living-document rule

`CLAUDE.md` requires `docs/ARCHITECTURE.md` to be updated in the same PR when gate or
confirmation behaviour changes. §4 (the orchestrator and the confirmation gate) gains
the per-channel TTL; §3 (channels and their trust model) gains the quote-strip at the
email boundary. Update both, plus the `_VOCAB` note if the TTL map lands beside it.

## 8. Done means

- A reply carrying a full Gmail quote block resolves a pending confirmation.
- A reply carrying a new instruction does not, and that is proven by a plant.
- An email confirmation 45 minutes after the readback still resolves.
- Both TTL readers redden together under one plant.
- `findings.md` carries the entry.
- `ARCHITECTURE.md` §3 and §4 updated in the same PR.
