# JARVIS — "Prove & Harden Everything" Checklist (2026-07-15)

Ordered by real-blocking → verify-only → design-decision. Check off as you go.

---

## A. Auth fixes (real blockers — do these)

### A1. Google OAuth re-consent (fixes Docs/Sheets creation)
- [ ] `cd backend` first — the `python -m app.google_oauth` error was just wrong
      working dir (`app` package lives under `backend/`, not repo root).
- [ ] Run: `python -m app.google_oauth --client-secrets <path-to-client-secrets.json>`
      (the module uses argparse + `InstalledAppFlow.from_client_secrets_file`;
      it opens a local browser consent flow).
- [ ] Complete consent — approve the FULL scope list, which now includes
      `documents` and `spreadsheets` (TDD #13). The old token predates these,
      which is exactly why doc/sheet creation failed.
- [ ] Copy the printed `GOOGLE_OAUTH_REFRESH_TOKEN=...`
- [ ] `fly secrets set GOOGLE_OAUTH_REFRESH_TOKEN="<new>" --app jarvis-mdk`
- [ ] Verify: ask JARVIS to create a test Google Doc → should return a
      docs.google.com link, not the "reconnect with new scopes" message.

### A2. Calendar service-account scope (fixes brief's missing calendar)
- [ ] SEPARATE auth system from A1 — Calendar uses `GOOGLE_SERVICE_ACCOUNT_JSON`
      (a service account), not OAuth. Yesterday's "invalid scope" is here.
- [ ] Check the service account has calendar scope AND the target calendar is
      shared with the service account's email (`scheduling.py` needs both).
- [ ] Verify: `GET /api/calendar/health` (admin, auth'd) → should report healthy.
- [ ] DECISION to note: Calendar-on-service-account + everything-else-on-OAuth is
      the root of "half of Google works." Consider a TDD to consolidate Calendar
      onto the same OAuth path so there's ONE Google auth to reason about.

---

## B. 4 AM call (verify — already armed)

- [ ] CONFIRMED today: worker logged
      `briefing scheduled daily at 04:00 America/Los_Angeles`.
- [ ] CONFIRMED: secrets deployed, bool coercion clean, `QUIET_HOURS_END=3`
      clears the 4 AM slot, outbound+by-phone on.
- [ ] Optional smoke test (don't wait for 4 AM): manually enqueue a briefing-call
      job so compose→dial runs once while watching `fly logs`.
- [ ] Update stored memory: `briefing_time` preference still says "around 6:00 AM"
      — now contradicts the 4 AM you set. Change it so JARVIS's self-description
      matches reality.

---

## C. Fly $0 credit (verify — NON-blocker)

- [ ] Your instinct was right: Fly bills monthly to your card; `creditBalance=$0`
      is just an empty PREPAID cushion, not money owed. JARVIS's "could get
      suspended" was overcautious.
- [ ] One check: confirm a valid card is on file (Fly dashboard → billing). If
      yes, ignore $0 entirely.
- [ ] Nice-to-have: soften the infra agent's $0 warning so it stops crying wolf.

---

## D. Twilio A2P / SMS (design decision — NOT today's fix)

- [ ] Campaign REJECTED (Error 30912): A2P 10DLC is for BUSINESS app-to-person
      messaging; JARVIS is one app texting one private owner — a structural
      mismatch, not a paperwork typo.
- [ ] Does NOT affect: the 4 AM CALL (voice ≠ SMS, no A2P needed), email, or
      TOTP confirmations. SMS is the only channel behind this wall.
- [ ] Choose a path (no rush):
      - Re-submit with genuine business framing (risky/borderline for 1 recipient)
      - Deprioritize SMS — email + voice + TOTP already cover briefings,
        confirmations, alerts. Your own design already chose TOTP-over-SMS to
        kill this dependency.
      - Research a non-A2P owner-only texting path / provider.
- [ ] Capture the decision in `jarvis-ideas` so it doesn't get lost.

---

## E. "Attach doc/sheet to email" (new capability — TDD, not a fix)

- [ ] NOT a bug: the handlers return a SHAREABLE LINK by design; attach-to-email
      was never built. JARVIS saying "I can't attach" was honest.
- [ ] Also missing: there is NO Drive scope (`auth/drive`) in the OAuth list, so
      true file sharing/attachment isn't possible yet regardless.
- [ ] If you want real attach/share: spec a TDD (add Drive scope + attach-or-share
      flow). Capture in `jarvis-ideas`.

---

## F. Durability (the actual theme — hand to Claude Code)

- [ ] Tasker XML: import, fix permissions/battery, get it pushing, RE-EXPORT from
      phone, commit the phone's export to `Project-Jarvis`. (See
      `tasker-setup-and-recovery.md`.)
- [ ] Scheduler-hardening + settings TDD → Claude Code, PR-1 first
      (minute-granularity quiet hours + briefing exemption). After PR-1 lands,
      set QUIET_HOURS_END back to your real 03:30 intent.
- [ ] Note: Admin + Status tabs ALREADY EXIST in the UI (per screenshot). Have
      Claude Code extend the existing Admin surface, not build a new one.
```
