# Get the 4 AM briefing call working — tomorrow, no code

This is the fast path. It flips the switches that are currently off. It gets you
a **call tomorrow at 4:00 AM**. The permanent fixes (real 21:00–03:30 quiet
window, Admin UI) come from the companion TDD; this unblocks you now.

## Why nothing fired

- `BRIEFING_ENABLED` defaults **false** → the scheduler logs "disabled" and
  never registers. No brief, no call.
- `BRIEFING_BY_PHONE` and `OUTBOUND_CALLS_ENABLED` default **false** → even if
  enabled, you'd get an *email*, not a call.
- The hardcoded time is **06:30, not 04:00**.
- Quiet hours are **21:00–07:00** in the deployed code, and `due_calls`
  suppresses any non-callback call inside that window — so a 4 AM briefing call
  would be composed and then **silently skipped**.

## Fly secrets to set

```bash
fly secrets set \
  BRIEFING_ENABLED=true \
  OUTBOUND_CALLS_ENABLED=true \
  BRIEFING_BY_PHONE=true \
  BRIEFING_HOUR=4 \
  BRIEFING_MINUTE=0 \
  QUIET_HOURS_END=3 \
  --app jarvis-mdk
```

### On `QUIET_HOURS_END=3` (interim)

Your intended quiet window is 21:00–**03:30**, but the current config only
accepts whole hours (`quiet_hours_end` is an int, minutes hardcoded to 0). So:

- `QUIET_HOURS_END=3` → quiet 21:00–03:00, and your 04:00 call clears it with an
  hour of margin. **Recommended interim.**
- Do **not** use `QUIET_HOURS_END=4` — that ends quiet exactly at 04:00, which is
  dangerously tight against a 04:00 call; a few seconds of skew and it gets
  suppressed.
- The true 03:30 boundary + a briefing-call exemption from quiet hours both land
  in TDD PR-1, after which the interim `QUIET_HOURS_END` can go back to your real
  preference and the call fires regardless of the window.

## Verify it's actually armed

Fly secrets restart the affected machines. After the deploy settles:

1. **Confirm the flags are set (names only, not values):**
   ```bash
   fly secrets list --app jarvis-mdk
   ```
   Look for `BRIEFING_ENABLED`, `OUTBOUND_CALLS_ENABLED`, `BRIEFING_BY_PHONE`,
   `BRIEFING_HOUR`, `BRIEFING_MINUTE`, `QUIET_HOURS_END`.

2. **Confirm the scheduler registered** — watch the worker boot log:
   ```bash
   fly logs --app jarvis-mdk | grep -i "briefing scheduled\|briefing disabled"
   ```
   You want to see `briefing scheduled daily at 04:00 America/Los_Angeles`.
   If you see "disabled", `BRIEFING_ENABLED` didn't take.

   > Note: today's build logs *nothing explicit* if the scheduler is healthy but
   > idle — that blind spot is exactly what TDD §4.4(b) (startup heartbeat)
   > fixes. Until then, the boot line above is your only signal.

3. **Optional smoke test without waiting for 4 AM:** trigger a briefing call path
   manually (enqueue a `briefing_call` job, or hit the briefing endpoint) to
   confirm compose→dial works end to end, so 4 AM isn't the first real run.

## If the 4 AM call still doesn't come

Check, in order:
- Worker machine actually running? (`fly status --app jarvis-mdk` — the `worker`
  process must be up; it hosts the scheduler.)
- `outbound_calls_enabled` true? (call path is gated in `_handle_place_calls`
  and `_place_due_calls`.)
- Quiet hours: is the effective end ≤ 3? (a 4 AM call inside quiet is dropped.)
- Compose returned empty? Today that's silent ("nothing to brief"); TDD §4.4(d)
  makes it notify.
